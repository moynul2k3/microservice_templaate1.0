import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

try:
    import boto3
except ImportError:  # pragma: no cover - dependency is installed in deployed services.
    boto3 = None


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _slugify_bucket_part(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"service$", "", value).strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def _derived_bucket_name() -> str | None:
    service_short_name = _env("SERVICE_SHORT_NAME")
    if service_short_name:
        slug = _slugify_bucket_part(service_short_name)
        if slug:
            return f"{slug}-media"

    app_name = _env("APP_NAME")
    if app_name:
        slug = _slugify_bucket_part(app_name)
        if slug:
            return f"{slug}-media"

    return None


@dataclass(frozen=True)
class ObjectStorageSettings:
    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    public_base_url: str
    region_name: str = "us-east-1"

    @classmethod
    def from_env(cls) -> "ObjectStorageSettings":
        endpoint_url = _env("STORAGE_ENDPOINT_URL", "MINIO_ENDPOINT", "R2_ENDPOINT")
        access_key = _env("STORAGE_ACCESS_KEY", "MINIO_ACCESS_KEY", "MINIO_ROOT_USER", "R2_ACCESS_KEY")
        secret_key = _env("STORAGE_SECRET_KEY", "MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD", "R2_SECRET_KEY")
        bucket = _env("STORAGE_BUCKET", "MINIO_BUCKET", "R2_BUCKET") or _derived_bucket_name()
        public_base_url = _env("STORAGE_PUBLIC_BASE_URL", "MINIO_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL")
        region_name = _env("STORAGE_REGION", "AWS_DEFAULT_REGION", default="us-east-1")

        missing = [
            name
            for name, value in {
                "STORAGE_ENDPOINT_URL": endpoint_url,
                "STORAGE_ACCESS_KEY": access_key,
                "STORAGE_SECRET_KEY": secret_key,
                "STORAGE_BUCKET or SERVICE_SHORT_NAME/APP_NAME": bucket,
                "STORAGE_PUBLIC_BASE_URL": public_base_url,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError(f"Missing object storage settings: {', '.join(missing)}")

        return cls(
            endpoint_url=endpoint_url.rstrip("/"),
            access_key=access_key,
            secret_key=secret_key,
            bucket=bucket,
            public_base_url=public_base_url.rstrip("/"),
            region_name=region_name,
        )


class ObjectStorage:
    def __init__(self, settings: ObjectStorageSettings | None = None):
        self.settings = settings or ObjectStorageSettings.from_env()
        if boto3 is None:  # pragma: no cover - dependency is installed in deployed services.
            raise RuntimeError("The 'boto3' package is required for object storage operations.")
        self.client = boto3.client(
            "s3",
            endpoint_url=self.settings.endpoint_url,
            aws_access_key_id=self.settings.access_key,
            aws_secret_access_key=self.settings.secret_key,
            region_name=self.settings.region_name,
        )

    def public_url(self, key: str) -> str:
        return f"{self.settings.public_base_url}/{quote(key.lstrip('/'))}"

    def key_from_url(self, file_url: str) -> str:
        public_prefix = f"{self.settings.public_base_url}/"
        if file_url.startswith(public_prefix):
            return file_url[len(public_prefix):]

        parsed = urlparse(file_url)
        path = parsed.path.lstrip("/")
        bucket_prefix = f"{self.settings.bucket}/"
        if path.startswith(bucket_prefix):
            return path[len(bucket_prefix):]
        return path

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        cache_control: str | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> str:
        args: dict[str, Any] = {
            "Bucket": self.settings.bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
        }
        if cache_control:
            args["CacheControl"] = cache_control
        if extra_args:
            args.update(extra_args)
        self.client.put_object(**args)
        return self.public_url(key)

    def delete_key(self, key: str) -> None:
        self.client.delete_object(Bucket=self.settings.bucket, Key=key)

    def delete_url(self, file_url: str) -> None:
        self.delete_key(self.key_from_url(file_url))

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.settings.bucket, Prefix=prefix):
            objects = page.get("Contents", [])
            if not objects:
                continue
            self.client.delete_objects(
                Bucket=self.settings.bucket,
                Delete={
                    "Objects": [{"Key": item["Key"]} for item in objects],
                    "Quiet": True,
                },
            )


_storage: ObjectStorage | None = None


def get_object_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = ObjectStorage()
    return _storage
