from typing import Any

from pydantic import BaseModel, Field


class StorageErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class BucketDeletionResponse(BaseModel):
    success: bool = True
    bucket: str
    force: bool
    deleted_objects: int = 0
    detail: str


class BucketDeletionErrorResponse(BaseModel):
    success: bool = False
    bucket: str
    force: bool
    error: StorageErrorBody
