import asyncio
import uuid
from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image

from shared_utils.storage import get_object_storage

import os
import tempfile
import ffmpeg
import imageio_ffmpeg

import shutil
import subprocess
from mimetypes import guess_type


ALLOWED_EXTENSIONS = [
    "jpg", "jpeg", "png", "gif", "webp",
    "pdf", "docx", "txt",
    "mp4", "mp3", "avi", "mkv",
    "svg", "ai", "eps",
]

DEFAULT_MAX_FILE_SIZE_MB = 1000

COMPRESSIBLE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
COMPRESSIBLE_VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}

TARGET_VIDEO_MIN_MB = 5
TARGET_VIDEO_MAX_MB = 10
MEDIA_CACHE_CONTROL = "public, max-age=31536000, immutable"

object_storage = get_object_storage()
r2_client = object_storage.client


class _StorageSettingsAdapter:
    R2_BUCKET = object_storage.settings.bucket
    R2_PUBLIC_BASE_URL = object_storage.settings.public_base_url


settings = _StorageSettingsAdapter()


def _get_extension(filename: str) -> str:
    return filename.split(".")[-1].lower()


def compress_image_sync(content: bytes, size=(800, 800), quality=50) -> bytes:
    try:
        img = Image.open(BytesIO(content))
        img = img.convert("RGB")
        img.thumbnail(size)

        img_io = BytesIO()
        img.save(img_io, format="WEBP", quality=quality)
        return img_io.getvalue()

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Image compression failed: {e}")


def _file_size_mb(path: str) -> float:
    return os.path.getsize(path) / (1024 * 1024)


def _content_type_for_hls_file(filename: str) -> str:
    if filename.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    if filename.endswith(".ts"):
        return "video/mp2t"
    return guess_type(filename)[0] or "application/octet-stream"

def compress_video_sync(content: bytes, ext: str, target_mb: int = 6) -> bytes:
    ffmpeg_binary = imageio_ffmpeg.get_ffmpeg_exe()

    input_path = None
    output_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as input_file:
            input_file.write(content)
            input_path = input_file.name

        # Do NOT use ffmpeg.probe here.
        # imageio_ffmpeg bundles ffmpeg, not ffprobe.
        _, duration = imageio_ffmpeg.count_frames_and_secs(input_path)

        if not duration or duration <= 0:
            raise ValueError("Could not detect video duration")

        audio_bitrate = 48000
        target_bits = target_mb * 8 * 1024 * 1024
        total_bitrate = int(target_bits / duration)

        video_bitrate = total_bitrate - audio_bitrate
        video_bitrate = min(max(video_bitrate, 150000), 2_000_000)

        output_path = input_path + ".compressed.mp4"

        (
            ffmpeg
            .input(input_path)
            .output(
                output_path,
                vcodec="libx264",
                acodec="aac",
                vf="scale='min(720,iw)':-2",
                preset="veryfast",
                pix_fmt="yuv420p",
                movflags="+faststart",
                **{
                    "b:v": str(video_bitrate),
                    "maxrate": str(video_bitrate),
                    "bufsize": str(video_bitrate * 2),
                    "b:a": "48k",
                }
            )
            .overwrite_output()
            .run(cmd=ffmpeg_binary, quiet=True)
        )

        with open(output_path, "rb") as f:
            optimized = f.read()

        if not optimized:
            raise ValueError("Compressed video output is empty")

        return optimized

    except ffmpeg.Error as e:
        stderr = e.stderr.decode("utf-8", errors="ignore") if e.stderr else str(e)
        raise HTTPException(status_code=400, detail=f"Video compression failed: {stderr}")

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Video compression failed: {e}")

    finally:
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                os.remove(path)



async def save_compressed_video_as_hls(
    file: UploadFile,
    upload_to: str,
    *,
    reel_id: int,
    max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
    allowed_extensions=COMPRESSIBLE_VIDEO_EXTENSIONS,
    progress_callback=None,
) -> str:
    filename = (file.filename or "").strip()

    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="Filename must include a valid extension")

    ext = _get_extension(filename)

    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid video type: {ext}")

    content = bytearray()
    chunk_size = 1024 * 1024

    if progress_callback:
        progress_callback(1)

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break

        content.extend(chunk)
        if progress_callback and isinstance(getattr(file, "size", None), int) and file.size > 0:
            read_percent = min(30, int((len(content) / file.size) * 30))
            progress_callback(max(1, read_percent))

        if len(content) > max_size * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds the allowed limit")

    loop = asyncio.get_running_loop()

    def _compress_then_hls() -> str:
        temp_dir = tempfile.mkdtemp(prefix=f"reel_{reel_id}_")
        compressed_path = os.path.join(temp_dir, "compressed.mp4")
        hls_dir = os.path.join(temp_dir, "hls")

        try:
            os.makedirs(hls_dir, exist_ok=True)

            compressed_bytes = compress_video_sync(
                bytes(content),
                ext,
                6,
            )
            if progress_callback:
                progress_callback(45)

            with open(compressed_path, "wb") as f:
                f.write(compressed_bytes)

            ffmpeg_binary = imageio_ffmpeg.get_ffmpeg_exe()

            index_path = os.path.join(hls_dir, "index.m3u8")
            segment_pattern = os.path.join(hls_dir, "segment_%03d.ts")

            subprocess.run(
                [
                    ffmpeg_binary,
                    "-y",
                    "-i",
                    compressed_path,
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-hls_time",
                    "2",
                    "-hls_playlist_type",
                    "vod",
                    "-hls_flags",
                    "independent_segments",
                    "-hls_segment_filename",
                    segment_pattern,
                    index_path,
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if progress_callback:
                progress_callback(65)

            version = uuid.uuid4().hex
            base_key = f"{upload_to}/{reel_id}/{version}"

            upload_files = []
            for root, _, files in os.walk(hls_dir):
                for file_name in files:
                    local_path = os.path.join(root, file_name)
                    relative_path = os.path.relpath(local_path, hls_dir)
                    key = f"{base_key}/{relative_path}"
                    upload_files.append((local_path, file_name, key))

            total_files = len(upload_files) or 1
            uploaded_files = 0
            for local_path, file_name, key in upload_files:

                with open(local_path, "rb") as f:
                    r2_client.put_object(
                        Bucket=settings.R2_BUCKET,
                        Key=key,
                        Body=f.read(),
                        ContentType=_content_type_for_hls_file(file_name),
                        CacheControl=MEDIA_CACHE_CONTROL,
                    )
                uploaded_files += 1
                if progress_callback:
                    upload_percent = 65 + int((uploaded_files / total_files) * 34)
                    progress_callback(min(99, upload_percent))

            return f"{settings.R2_PUBLIC_BASE_URL}/{base_key}/index.m3u8"

        except subprocess.CalledProcessError as error:
            stderr = error.stderr.decode("utf-8", errors="ignore") if error.stderr else str(error)
            raise HTTPException(status_code=400, detail=f"HLS processing failed: {stderr}")

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    return await loop.run_in_executor(None, _compress_then_hls)


async def save_file(
    file: UploadFile,
    upload_to: str,
    *,
    max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
    allowed_extensions=ALLOWED_EXTENSIONS,
    compress: bool = True,
    quality: int = 50,
    size=(800, 800),
) -> str:
    filename = (file.filename or "").strip()

    if not filename or "." not in filename:
        raise HTTPException(status_code=400, detail="Filename must include a valid extension")

    ext = _get_extension(filename)

    if ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"Invalid file type: {ext}")

    content = bytearray()
    chunk_size = 1024 * 1024

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break

        content.extend(chunk)

        if len(content) > max_size * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File size exceeds the allowed limit")

    if compress and ext in COMPRESSIBLE_IMAGE_EXTENSIONS:
        loop = asyncio.get_running_loop()

        final_content = await loop.run_in_executor(
            None,
            compress_image_sync,
            bytes(content),
            size,
            quality,
        )

        stored_filename = f"{uuid.uuid4().hex}.webp"
        content_type = "image/webp"

    elif compress and ext in COMPRESSIBLE_VIDEO_EXTENSIONS:
        loop = asyncio.get_running_loop()

        final_content = await loop.run_in_executor(
            None,
            compress_video_sync,
            bytes(content),
            ext,
            6,
        )

        stored_filename = f"{uuid.uuid4().hex}.mp4"
        content_type = "video/mp4"

    else:
        final_content = bytes(content)
        stored_filename = f"{uuid.uuid4().hex}.{ext}"
        content_type = file.content_type or "application/octet-stream"

    key = f"{upload_to}/{stored_filename}"

    loop = asyncio.get_running_loop()

    await loop.run_in_executor(
        None,
        lambda: r2_client.put_object(
            Bucket=settings.R2_BUCKET,
            Key=key,
            Body=final_content,
            ContentType=content_type,
            CacheControl=MEDIA_CACHE_CONTROL,
        ),
    )

    return f"{settings.R2_PUBLIC_BASE_URL}/{key}"


async def delete_file(file_url: str) -> bool:
    if not file_url:
        return False

    try:
        key = object_storage.key_from_url(file_url)

        loop = asyncio.get_running_loop()

        await loop.run_in_executor(
            None,
            lambda: r2_client.delete_object(
                Bucket=settings.R2_BUCKET,
                Key=key,
            ),
        )

        return True

    except Exception as e:
        print(f"R2 delete failed: {e}")
        return False



async def delete_hls_folder(index_url: str) -> bool:
    if not index_url:
        return False

    try:
        key = object_storage.key_from_url(index_url)

        folder_prefix = key.rsplit("/", 1)[0] + "/"

        loop = asyncio.get_running_loop()

        def _delete_folder():
            response = r2_client.list_objects_v2(
                Bucket=settings.R2_BUCKET,
                Prefix=folder_prefix,
            )

            objects = response.get("Contents", [])

            if not objects:
                return

            r2_client.delete_objects(
                Bucket=settings.R2_BUCKET,
                Delete={
                    "Objects": [{"Key": obj["Key"]} for obj in objects],
                    "Quiet": True,
                },
            )

        await loop.run_in_executor(None, _delete_folder)

        return True

    except Exception as e:
        print(f"R2 HLS folder delete failed: {e}")
        return False

async def update_file(
    new_file: UploadFile,
    file_url: str | None,
    upload_to: str,
    *,
    max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
    allowed_extensions=ALLOWED_EXTENSIONS,
    compress: bool = True,
    quality: int = 50,
    size=(800, 800),
) -> str:
    if file_url:
        await delete_file(file_url)

    return await save_file(
        new_file,
        upload_to=upload_to,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        compress=compress,
        quality=quality,
        size=size,
    )


async def update_compressed_video_as_hls(
    new_file: UploadFile,
    old_index_url: str | None,
    upload_to: str,
    *,
    reel_id: int,
    max_size: int = DEFAULT_MAX_FILE_SIZE_MB,
    allowed_extensions=COMPRESSIBLE_VIDEO_EXTENSIONS,
    progress_callback=None,
) -> str:
    new_url = await save_compressed_video_as_hls(
        new_file,
        upload_to=upload_to,
        reel_id=reel_id,
        max_size=max_size,
        allowed_extensions=allowed_extensions,
        progress_callback=progress_callback,
    )

    if old_index_url and old_index_url != new_url:
        await delete_hls_folder(old_index_url)

    return new_url
