"""Schemas — Internal Working documents."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class InternalWorkingUploadRequest(BaseModel):
    filing_id: UUID
    filename: str
    content_type: str
    label: Optional[str] = None


class InternalWorkingUploadURLResponse(BaseModel):
    upload_url: str
    object_key: str


class InternalWorkingResponse(BaseModel):
    id: UUID
    filing_id: UUID
    file_id: UUID
    label: Optional[str] = None
    original_filename: Optional[str] = None
    uploaded_by: UUID
    uploaded_by_name: Optional[str] = None
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class InternalWorkingListResponse(BaseModel):
    items: list[InternalWorkingResponse]
    count: int
