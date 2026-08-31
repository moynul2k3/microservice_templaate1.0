import asyncio
import os
import tempfile
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator, Optional, Set, Tuple

from fastapi import BackgroundTasks, HTTPException, UploadFile

from applications.reels.reels import Reel
from app.utils.file_manager import (
    save_file,
    update_file,
    save_compressed_video_as_hls,
    update_compressed_video_as_hls,
)

VIDEO_EXTENSIONS: Set[str] = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}
IMAGE_EXTENSIONS: Set[str] = {"jpg", "jpeg", "png", "webp", "svg", "gif"}
MAX_VIDEO_SIZE_MB = 300
MAX_IMAGE_SIZE_MB = 20
_PROGRESS_SUBSCRIBERS: dict[int, set[asyncio.Queue]] = defaultdict(set)
_PROGRESS_LOCK = asyncio.Lock()
_LATEST_PROGRESS_EVENT: dict[int, dict[str, Any]] = {}
_TEMP_UPLOAD_ROOT = os.path.join(tempfile.gettempdir(), "rappio_reels")


async def publish_reel_upload_progress(reel_id: int, event: dict[str, Any]) -> None:
    _LATEST_PROGRESS_EVENT[reel_id] = event
    async with _PROGRESS_LOCK:
        listeners = list(_PROGRESS_SUBSCRIBERS.get(reel_id, set()))
    for queue in listeners:
        await queue.put(event)


async def subscribe_reel_upload_progress(reel_id: int) -> AsyncIterator[dict[str, Any]]:
    queue: asyncio.Queue = asyncio.Queue()
    async with _PROGRESS_LOCK:
        _PROGRESS_SUBSCRIBERS[reel_id].add(queue)
    try:
        latest_event = _LATEST_PROGRESS_EVENT.get(reel_id)
        if latest_event:
            yield latest_event
        else:
            yield {"event": "idle", "reel_id": reel_id, "progress_percent": 0}
        while True:
            event = await queue.get()
            yield event
    finally:
        async with _PROGRESS_LOCK:
            listeners = _PROGRESS_SUBSCRIBERS.get(reel_id)
            if listeners:
                listeners.discard(queue)
                if not listeners:
                    _PROGRESS_SUBSCRIBERS.pop(reel_id, None)


def _has_upload(file: Optional[Any]) -> bool:
    if not file:
        return False
    if isinstance(file, dict):
        return bool(file.get("path") and file.get("filename"))
    return bool(file.filename and file.filename.strip())


def _build_temp_upload_path(filename: str) -> str:
    os.makedirs(_TEMP_UPLOAD_ROOT, exist_ok=True)
    safe_name = os.path.basename(filename or "upload.bin")
    return os.path.join(_TEMP_UPLOAD_ROOT, f"{uuid.uuid4().hex}_{safe_name}")


def _validate_extension(file: UploadFile, allowed_extensions: Set[str], label: str) -> None:
    filename = (file.filename or "").strip()
    if "." not in filename:
        raise HTTPException(status_code=400, detail=f"{label} has no valid extension")

    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid {label} type: {extension}")


def _validate_size_hint(file: UploadFile, max_size_mb: int, label: str) -> None:
    size = getattr(file, "size", None)
    if isinstance(size, int) and size > max_size_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"{label} exceeds {max_size_mb}MB size limit")


def validate_reel_upload_inputs(
    *,
    media_file: Optional[UploadFile] = None,
    thumbnail: Optional[UploadFile] = None,
    logo: Optional[UploadFile] = None,
) -> None:
    if _has_upload(media_file):
        _validate_extension(media_file, VIDEO_EXTENSIONS, "media_file")
        _validate_size_hint(media_file, MAX_VIDEO_SIZE_MB, "media_file")
    if _has_upload(thumbnail):
        _validate_extension(thumbnail, IMAGE_EXTENSIONS, "thumbnail")
        _validate_size_hint(thumbnail, MAX_IMAGE_SIZE_MB, "thumbnail")
    if _has_upload(logo):
        _validate_extension(logo, IMAGE_EXTENSIONS, "logo")
        _validate_size_hint(logo, MAX_IMAGE_SIZE_MB, "logo")


async def _save_or_update_uploaded_file(
    file: UploadFile,
    *,
    existing_url: Optional[str],
    upload_to: str,
    max_size: int,
    allowed_extensions: Set[str],
    compress: bool,
) -> str:
    if existing_url:
        return await update_file(
            file,
            existing_url,
            upload_to=upload_to,
            max_size=max_size,
            allowed_extensions=allowed_extensions,
            compress=compress,
        )
    return await save_file(
        file,
        upload_to=upload_to,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        compress=compress,
    )


async def _duplicate_upload_for_background(file: UploadFile) -> Optional[UploadFile]:
    source_file = getattr(file, "file", None)
    if source_file is None:
        return None

    duplicated_fd: Optional[int] = None
    duplicated_file = None
    try:
        rollover = getattr(source_file, "rollover", None)
        if callable(rollover):
            rollover()

        flush = getattr(source_file, "flush", None)
        if callable(flush):
            flush()

        source_file.seek(0)
        duplicated_fd = os.dup(source_file.fileno())
        duplicated_file = os.fdopen(duplicated_fd, "rb")
        duplicated_fd = None

        return UploadFile(
            file=duplicated_file,
            filename=file.filename,
            headers=getattr(file, "headers", None),
            size=getattr(file, "size", None),
        )
    except Exception:
        if duplicated_file is not None:
            duplicated_file.close()
        elif duplicated_fd is not None:
            os.close(duplicated_fd)
        return None


async def stage_upload_for_background(file: UploadFile) -> Optional[Any]:
    if not _has_upload(file):
        return None

    await file.seek(0)
    try:
        duplicated_upload = await _duplicate_upload_for_background(file)
        if duplicated_upload is not None:
            return duplicated_upload

        temp_path = _build_temp_upload_path(file.filename or "upload.bin")
        chunk_size = 1024 * 1024
        try:
            with open(temp_path, "wb") as temp_file:
                while True:
                    chunk = await file.read(chunk_size)
                    if not chunk:
                        break
                    temp_file.write(chunk)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

        return {
            "path": temp_path,
            "filename": file.filename or os.path.basename(temp_path),
            "size": getattr(file, "size", None),
        }
    finally:
        await file.close()


async def _save_background_upload(
    background_upload: Any,
    *,
    existing_url: Optional[str],
    upload_to: str,
    max_size: int,
    allowed_extensions: Set[str],
    compress: bool,
) -> str:
    if isinstance(background_upload, UploadFile):
        await background_upload.seek(0)
        return await _save_or_update_uploaded_file(
            background_upload,
            existing_url=existing_url,
            upload_to=upload_to,
            max_size=max_size,
            allowed_extensions=allowed_extensions,
            compress=compress,
        )
    return await _upload_staged_file(
        background_upload,
        existing_url=existing_url,
        upload_to=upload_to,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        compress=compress,
    )


async def _upload_staged_file(
    temp_upload: dict[str, Any],
    *,
    existing_url: Optional[str],
    upload_to: str,
    max_size: int,
    allowed_extensions: Set[str],
    compress: bool,
) -> str:
    with open(temp_upload["path"], "rb") as buffered_file:
        upload = UploadFile(
            file=buffered_file,
            filename=temp_upload.get("filename"),
            headers=None,
            size=temp_upload.get("size"),
        )
        return await _save_or_update_uploaded_file(
            upload,
            existing_url=existing_url,
            upload_to=upload_to,
            max_size=max_size,
            allowed_extensions=allowed_extensions,
            compress=compress,
        )


async def _upload_single_field(
    field_name: str,
    temp_upload: Any,
    *,
    existing_url: Optional[str],
    upload_to: str,
    max_size: int,
    allowed_extensions: Set[str],
    compress: bool,
) -> Tuple[str, Optional[str], Optional[str]]:
    try:
        uploaded_url = await _save_background_upload(
            temp_upload,
            existing_url=existing_url,
            upload_to=upload_to,
            max_size=max_size,
            allowed_extensions=allowed_extensions,
            compress=compress,
        )
        return field_name, uploaded_url, None
    except Exception as error:
        return field_name, None, str(error)

async def _upload_hls_video_field(
    field_name: str,
    temp_upload: Any,
    *,
    reel_id: int,
    existing_url: Optional[str],
    max_size: int,
    allowed_extensions: Set[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    try:
        loop = asyncio.get_running_loop()
        last_percent = {"value": 0}
        milestone_step = 20

        def notify_percent(percent: int) -> None:
            bounded = max(0, min(100, int(percent)))
            if bounded <= last_percent["value"]:
                return
            start = ((last_percent["value"] // milestone_step) + 1) * milestone_step
            end = (bounded // milestone_step) * milestone_step
            if start > end:
                return
            for value in range(start, end + 1, milestone_step):
                loop.call_soon_threadsafe(
                    asyncio.create_task,
                    publish_reel_upload_progress(
                        reel_id,
                        {
                            "event": "video_progress",
                            "reel_id": reel_id,
                            "field": field_name,
                            "progress_percent": value,
                        },
                    ),
                )
            last_percent["value"] = bounded

        if isinstance(temp_upload, UploadFile):
            upload = temp_upload
            await upload.seek(0)
            if existing_url:
                uploaded_url = await update_compressed_video_as_hls(
                    upload,
                    existing_url,
                    upload_to="reels/hls",
                    reel_id=reel_id,
                    max_size=max_size,
                    allowed_extensions=allowed_extensions,
                    progress_callback=notify_percent,
                )
            else:
                uploaded_url = await save_compressed_video_as_hls(
                    upload,
                    upload_to="reels/hls",
                    reel_id=reel_id,
                    max_size=max_size,
                    allowed_extensions=allowed_extensions,
                    progress_callback=notify_percent,
                )
        else:
            with open(temp_upload["path"], "rb") as buffered_file:
                upload = UploadFile(
                    file=buffered_file,
                    filename=temp_upload.get("filename"),
                    headers=None,
                    size=temp_upload.get("size"),
                )

                if existing_url:
                    uploaded_url = await update_compressed_video_as_hls(
                        upload,
                        existing_url,
                        upload_to="reels/hls",
                        reel_id=reel_id,
                        max_size=max_size,
                        allowed_extensions=allowed_extensions,
                        progress_callback=notify_percent,
                    )
                else:
                    uploaded_url = await save_compressed_video_as_hls(
                        upload,
                        upload_to="reels/hls",
                        reel_id=reel_id,
                        max_size=max_size,
                        allowed_extensions=allowed_extensions,
                        progress_callback=notify_percent,
                    )

        return field_name, uploaded_url, None

    except Exception as error:
        return field_name, None, str(error)



async def process_reel_uploads_in_background(
    reel_id: int,
    media_file: Optional[Any] = None,
    thumbnail: Optional[Any] = None,
    logo: Optional[Any] = None,
    old_media_url: Optional[str] = None,
    old_thumbnail_url: Optional[str] = None,
    old_logo_url: Optional[str] = None,
) -> None:
    await publish_reel_upload_progress(
        reel_id,
        {"event": "started", "reel_id": reel_id, "progress_percent": 0},
    )
    try:
        upload_jobs = []
        total_jobs = 0

        if _has_upload(media_file):
            assert media_file is not None
            total_jobs += 1
            upload_jobs.append(
                _upload_hls_video_field(
                    "media_file",
                    media_file,
                    reel_id=reel_id,
                    existing_url=old_media_url,
                    max_size=MAX_VIDEO_SIZE_MB,
                    allowed_extensions=VIDEO_EXTENSIONS,
                )
            )

        if _has_upload(thumbnail):
            assert thumbnail is not None
            total_jobs += 1
            upload_jobs.append(
                _upload_single_field(
                    "thumbnail",
                    thumbnail,
                    existing_url=old_thumbnail_url,
                    upload_to="reels/thumbnails",
                    max_size=MAX_IMAGE_SIZE_MB,
                    allowed_extensions=IMAGE_EXTENSIONS,
                    compress=True,
                )
            )

        if _has_upload(logo):
            assert logo is not None
            total_jobs += 1
            upload_jobs.append(
                _upload_single_field(
                    "logo",
                    logo,
                    existing_url=old_logo_url,
                    upload_to="reels/logos",
                    max_size=MAX_IMAGE_SIZE_MB,
                    allowed_extensions=IMAGE_EXTENSIONS,
                    compress=True,
                )
            )

        if not upload_jobs:
            await publish_reel_upload_progress(
                reel_id,
                {
                    "event": "completed",
                    "reel_id": reel_id,
                    "processed": 0,
                    "failed": 0,
                    "total": 0,
                    "progress_percent": 100,
                },
            )
            return

        results = await asyncio.gather(*upload_jobs)
        update_data = {}
        failed_fields = {}
        processed = 0
        for field, uploaded_url, error in results:
            processed += 1
            progress_percent = int((processed / total_jobs) * 100) if total_jobs > 0 else 100
            if uploaded_url:
                update_data[field] = uploaded_url
                await publish_reel_upload_progress(
                    reel_id,
                    {
                        "event": "field_done",
                        "reel_id": reel_id,
                        "field": field,
                        "status": "success",
                        "processed": processed,
                        "total": total_jobs,
                        "progress_percent": progress_percent,
                    },
                )
            if error:
                failed_fields[field] = error
                await publish_reel_upload_progress(
                    reel_id,
                    {
                        "event": "field_done",
                        "reel_id": reel_id,
                        "field": field,
                        "status": "failed",
                        "error": error,
                        "processed": processed,
                        "total": total_jobs,
                        "progress_percent": progress_percent,
                    },
                )

        if update_data:
            reel_exists = await Reel.filter(id=reel_id).exists()

            if not reel_exists:
                print(f"[reel-upload] skipped db update for deleted reel_id={reel_id}")
            else:
                await Reel.filter(id=reel_id).update(**update_data)
                print(f"[reel-upload] updated reel_id={reel_id}: {list(update_data.keys())}")

        if failed_fields:
            print(f"[reel-upload] partial failure for reel_id={reel_id}: {failed_fields}")
        await publish_reel_upload_progress(
            reel_id,
            {
                "event": "completed",
                "reel_id": reel_id,
                "processed": processed,
                "failed": len(failed_fields),
                "total": total_jobs,
                "updated_fields": list(update_data.keys()),
                "progress_percent": 100,
            },
        )
    except Exception as error:
        print(f"[reel-upload] failed for reel_id={reel_id}: {error}")
        await publish_reel_upload_progress(
            reel_id,
            {"event": "failed", "reel_id": reel_id, "error": str(error), "progress_percent": 100},
        )
    finally:
        for file in (media_file, thumbnail, logo):
            if isinstance(file, UploadFile):
                try:
                    await file.close()
                except Exception:
                    pass
            elif file and isinstance(file, dict):
                try:
                    temp_path = file.get("path")
                    if temp_path and os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass


async def queue_reel_upload_task(
    background_tasks: BackgroundTasks,
    *,
    reel_id: int,
    media_file: Optional[UploadFile] = None,
    thumbnail: Optional[UploadFile] = None,
    logo: Optional[UploadFile] = None,
    old_media_url: Optional[str] = None,
    old_thumbnail_url: Optional[str] = None,
    old_logo_url: Optional[str] = None,
) -> str:
    validate_reel_upload_inputs(media_file=media_file, thumbnail=thumbnail, logo=logo)

    if not any((_has_upload(media_file), _has_upload(thumbnail), _has_upload(logo))):
        return "not_provided"

    staged_media = await stage_upload_for_background(media_file) if _has_upload(media_file) else None
    staged_thumbnail = await stage_upload_for_background(thumbnail) if _has_upload(thumbnail) else None
    staged_logo = await stage_upload_for_background(logo) if _has_upload(logo) else None

    background_tasks.add_task(
        publish_reel_upload_progress,
        reel_id,
        {"event": "queued", "reel_id": reel_id, "progress_percent": 0},
    )
    background_tasks.add_task(
        process_reel_uploads_in_background,
        reel_id=reel_id,
        media_file=staged_media,
        thumbnail=staged_thumbnail,
        logo=staged_logo,
        old_media_url=old_media_url,
        old_thumbnail_url=old_thumbnail_url,
        old_logo_url=old_logo_url,
    )
    return "processing"
