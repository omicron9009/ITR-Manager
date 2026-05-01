"""Schemas — Document management."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import DocumentStatus


# ─── Master Document Type ────────────────────────────────────
class MasterDocTypeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    display_order: int = 0


class MasterDocTypeUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class MasterDocTypeResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    is_active: bool
    display_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MasterDocTypeListResponse(BaseModel):
    items: list[MasterDocTypeResponse]
    total: int


# ─── Filing Document (Placeholder) ──────────────────────────
class DocumentPlaceholderAssignRequest(BaseModel):
    document_type_ids: list[UUID] = Field(..., min_length=1)


class FilingDocumentResponse(BaseModel):
    id: UUID
    filing_id: UUID
    document_type_id: UUID
    document_type_name: Optional[str] = None
    status: DocumentStatus
    file_id: Optional[UUID] = None
    original_filename: Optional[str] = None
    rejection_reason: Optional[str] = None
    uploaded_at: Optional[datetime] = None
    reviewed_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    assigned_at: datetime

    model_config = {"from_attributes": True}


class FilingDocumentListResponse(BaseModel):
    items: list[FilingDocumentResponse]
    total: int
    all_approved: bool = False
    pending_count: int = 0
    uploaded_count: int = 0
    rejected_count: int = 0
    approved_count: int = 0


# ─── Document Review ────────────────────────────────────────
class DocumentApproveRequest(BaseModel):
    document_ids: list[UUID] = Field(..., min_length=1)


class DocumentRejectRequest(BaseModel):
    rejections: list["DocumentRejectionItem"] = Field(..., min_length=1)


class DocumentRejectionItem(BaseModel):
    document_id: UUID
    reason: str = Field(..., min_length=1, max_length=1000)


# ─── Document Upload URL ────────────────────────────────────
class DocumentUploadURLRequest(BaseModel):
    document_id: UUID
    filename: str
    content_type: str


class DocumentUploadURLResponse(BaseModel):
    upload_url: str
    document_id: UUID
    object_key: str


class DocumentDownloadURLResponse(BaseModel):
    download_url: str
    filename: str
    content_type: str
