"""Schemas — Internal Working documents."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import InternalWorkingDocType


class InternalWorkingUploadRequest(BaseModel):
    filing_id: UUID
    filename: str
    content_type: str
    doc_type: InternalWorkingDocType
    label: Optional[str] = None


class InternalWorkingUploadURLResponse(BaseModel):
    upload_url: str
    object_key: str


class InternalWorkingReplaceUploadRequest(BaseModel):
    """Request body for getting a presigned URL when replacing an existing IW doc."""
    filename: str
    content_type: str


class InternalWorkingReplaceConfirmRequest(BaseModel):
    """Request body to finalize the replacement after the new file is in MinIO."""
    object_key: str
    filename: str
    content_type: str
    file_size: int


class InternalWorkingResponse(BaseModel):
    id: UUID
    filing_id: UUID
    file_id: UUID
    doc_type: Optional[InternalWorkingDocType] = None
    label: Optional[str] = None
    original_filename: Optional[str] = None
    uploaded_by: UUID
    uploaded_by_name: Optional[str] = None
    uploaded_at: datetime
    # Versioning
    replaces_id: Optional[UUID] = None
    superseded_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class InternalWorkingListResponse(BaseModel):
    items: list[InternalWorkingResponse]
    count: int
