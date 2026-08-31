import atexit
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from urllib3.exceptions import HTTPError as UrllibHTTPError

from shared_utils.storage import ObjectStorageSettings

try:
    from minio import Minio
    from minio.deleteobjects import DeleteObject
    from minio.error import InvalidResponseError, S3Error, ServerError
except ImportError:  # pragma: no cover - exercised in runtime environments with MinIO installed.
    Minio = None
    InvalidResponseError = None
    S3Error = None
    ServerError = None

    class DeleteObject:  # type: ignore[no-redef]
        def __init__(self, object_name: str):
            self.object_name = object_name


logger = logging.getLogger(__name__)
_BUCKET_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="minio-bucket-delete")
atexit.register(_BUCKET_EXECUTOR.shutdown, wait=False, cancel_futures=True)

_ACCESS_DENIED_CODES = {
    "AccessDenied",
    "AllAccessDisabled",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
}
_BAD_REQUEST_CODES = {"InvalidBucketName"}


@dataclass(frozen=True)
class BucketDeletionResult:
    bucket_name: str
    force: bool
    deleted_objects: int
    detail: str


@dataclass
class BucketDeletionError(Exception):
    status_code: int
    code: str
    message: str
    details: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)

    def to_response(self, *, bucket_name: str, force: bool) -> dict[str, Any]:
        return {
            "success": False,
            "bucket": bucket_name,
            "force": force,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details or {},
            },
        }


class MinioBucketDeletionService:
    def __init__(
        self,
        settings: ObjectStorageSettings | None = None,
        *,
        client: Any | None = None,
        delete_batch_size: int = 1000,
    ) -> None:
        self.settings = settings or ObjectStorageSettings.from_env()
        self.client = client or self._build_client()
        self.delete_batch_size = delete_batch_size

    def _build_client(self) -> Any:
        if Minio is None:  # pragma: no cover - protected by dependency installation in deployment.
            raise RuntimeError("The 'minio' package is required for bucket deletion operations.")

        parsed = urlparse(self.settings.endpoint_url)
        endpoint = parsed.netloc or parsed.path
        if not endpoint:
            raise RuntimeError("Invalid STORAGE_ENDPOINT_URL for MinIO client configuration.")

        return Minio(
            endpoint,
            access_key=self.settings.access_key,
            secret_key=self.settings.secret_key,
            secure=parsed.scheme == "https",
            region=self.settings.region_name,
        )

    async def delete_bucket(
        self,
        bucket_name: str,
        *,
        force: bool = False,
        actor_id: int | None = None,
        actor_email: str | None = None,
    ) -> BucketDeletionResult:
        loop = asyncio.get_running_loop()
        result, error_payload = await loop.run_in_executor(
            _BUCKET_EXECUTOR,
            self._delete_bucket_sync_captured,
            bucket_name,
            force,
            actor_id,
            actor_email,
        )
        if error_payload is not None:
            raise BucketDeletionError(**error_payload)
        return result

    def _delete_bucket_sync_captured(
        self,
        bucket_name: str,
        force: bool,
        actor_id: int | None,
        actor_email: str | None,
    ) -> tuple[BucketDeletionResult | None, dict[str, Any] | None]:
        try:
            return (
                self._delete_bucket_sync(bucket_name, force, actor_id, actor_email),
                None,
            )
        except BucketDeletionError as exc:
            return (
                None,
                {
                    "status_code": exc.status_code,
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            )

    def _delete_bucket_sync(
        self,
        bucket_name: str,
        force: bool,
        actor_id: int | None,
        actor_email: str | None,
    ) -> BucketDeletionResult:
        logger.info(
            "Bucket deletion requested bucket=%s force=%s actor_id=%s actor_email=%s",
            bucket_name,
            force,
            actor_id,
            actor_email,
        )

        try:
            exists = self.client.bucket_exists(bucket_name)
        except Exception as exc:  # pragma: no cover - covered through _translate_exception tests.
            error = self._translate_exception(exc, bucket_name=bucket_name)
            self._log_failure(error, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
            raise error from exc

        if not exists:
            error = BucketDeletionError(
                status_code=404,
                code="bucket_not_found",
                message=f"Bucket '{bucket_name}' was not found.",
            )
            self._log_failure(error, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
            raise error

        deleted_objects = 0

        if force:
            try:
                deleted_objects = self._delete_all_objects(bucket_name)
            except BucketDeletionError as exc:
                self._log_failure(exc, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
                raise
        else:
            try:
                first_object = self._find_first_object(bucket_name)
            except BucketDeletionError as exc:
                self._log_failure(exc, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
                raise

            if first_object is not None:
                error = BucketDeletionError(
                    status_code=409,
                    code="bucket_not_empty",
                    message=(
                        f"Bucket '{bucket_name}' is not empty. "
                        "Delete all objects first or retry with force=true."
                    ),
                    details={"first_object": first_object},
                )
                self._log_failure(error, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
                raise error

        try:
            self.client.remove_bucket(bucket_name)
        except Exception as exc:
            error = self._translate_exception(exc, bucket_name=bucket_name)
            self._log_failure(error, bucket_name=bucket_name, force=force, actor_id=actor_id, actor_email=actor_email)
            raise error from exc

        detail = (
            f"Bucket '{bucket_name}' and {deleted_objects} object(s) were deleted successfully."
            if force
            else f"Bucket '{bucket_name}' was deleted successfully."
        )
        logger.info(
            "Bucket deletion succeeded bucket=%s force=%s deleted_objects=%s actor_id=%s actor_email=%s",
            bucket_name,
            force,
            deleted_objects,
            actor_id,
            actor_email,
        )
        return BucketDeletionResult(
            bucket_name=bucket_name,
            force=force,
            deleted_objects=deleted_objects,
            detail=detail,
        )

    def _find_first_object(self, bucket_name: str) -> str | None:
        try:
            for obj in self.client.list_objects(bucket_name, recursive=True):
                return obj.object_name
        except Exception as exc:
            raise self._translate_exception(exc, bucket_name=bucket_name) from exc
        return None

    def _delete_all_objects(self, bucket_name: str) -> int:
        batch: list[DeleteObject] = []
        deleted_objects = 0

        try:
            for obj in self.client.list_objects(bucket_name, recursive=True):
                batch.append(DeleteObject(obj.object_name))
                if len(batch) >= self.delete_batch_size:
                    deleted_objects += self._flush_delete_batch(bucket_name, batch)
                    batch = []
        except BucketDeletionError:
            raise
        except Exception as exc:
            raise self._translate_exception(exc, bucket_name=bucket_name) from exc

        if batch:
            deleted_objects += self._flush_delete_batch(bucket_name, batch)

        return deleted_objects

    def _flush_delete_batch(self, bucket_name: str, batch: list[DeleteObject]) -> int:
        try:
            errors = list(self.client.remove_objects(bucket_name, batch))
        except Exception as exc:
            raise self._translate_exception(exc, bucket_name=bucket_name) from exc

        if errors:
            first_error = errors[0]
            error_code = getattr(first_error, "code", None) or "object_delete_failed"
            error_message = getattr(first_error, "message", None) or (
                "MinIO failed to delete one or more objects before bucket removal."
            )
            raise BucketDeletionError(
                status_code=502,
                code="object_delete_failed",
                message=error_message,
                details={"upstream_code": error_code},
            )

        return len(batch)

    def _log_failure(
        self,
        error: BucketDeletionError,
        *,
        bucket_name: str,
        force: bool,
        actor_id: int | None,
        actor_email: str | None,
    ) -> None:
        log_message = (
            "Bucket deletion failed bucket=%s force=%s actor_id=%s actor_email=%s "
            "code=%s status=%s details=%s"
        )
        log_args = (
            bucket_name,
            force,
            actor_id,
            actor_email,
            error.code,
            error.status_code,
            error.details or {},
        )
        if error.status_code >= 500:
            logger.exception(log_message, *log_args)
            return
        logger.warning(log_message, *log_args)

    def _translate_exception(self, exc: Exception, *, bucket_name: str) -> BucketDeletionError:
        error_code = getattr(exc, "code", None)
        error_message = getattr(exc, "message", None) or str(exc)
        is_STORAGE_error = (S3Error is not None and isinstance(exc, S3Error)) or error_code is not None

        if is_STORAGE_error:

            if error_code == "NoSuchBucket":
                return BucketDeletionError(
                    status_code=404,
                    code="bucket_not_found",
                    message=f"Bucket '{bucket_name}' was not found.",
                )
            if error_code in _ACCESS_DENIED_CODES:
                return BucketDeletionError(
                    status_code=403,
                    code="access_denied",
                    message=f"Access denied while deleting bucket '{bucket_name}'.",
                )
            if error_code == "BucketNotEmpty":
                return BucketDeletionError(
                    status_code=409,
                    code="bucket_not_empty",
                    message=(
                        f"Bucket '{bucket_name}' is not empty. "
                        "Delete all objects first or retry with force=true."
                    ),
                )
            if error_code in _BAD_REQUEST_CODES:
                return BucketDeletionError(
                    status_code=400,
                    code="invalid_bucket_name",
                    message=error_message,
                )

            return BucketDeletionError(
                status_code=502,
                code="storage_api_error",
                message=error_message,
                details={"upstream_code": error_code or "unknown"},
            )

        if InvalidResponseError is not None and isinstance(exc, InvalidResponseError):
            return BucketDeletionError(
                status_code=502,
                code="storage_invalid_response",
                message="MinIO returned an invalid response while deleting the bucket.",
            )

        if ServerError is not None and isinstance(exc, ServerError):
            status_code = getattr(exc, "status_code", 503)
            return BucketDeletionError(
                status_code=503 if status_code >= 500 else 502,
                code="storage_server_error",
                message="MinIO reported a server-side error while deleting the bucket.",
                details={"upstream_status": status_code},
            )

        if isinstance(exc, (UrllibHTTPError, TimeoutError, OSError)):
            return BucketDeletionError(
                status_code=503,
                code="storage_unavailable",
                message="Unable to reach MinIO while deleting the bucket. Please retry shortly.",
            )

        return BucketDeletionError(
            status_code=500,
            code="bucket_delete_failed",
            message="Bucket deletion failed due to an unexpected server error.",
        )
