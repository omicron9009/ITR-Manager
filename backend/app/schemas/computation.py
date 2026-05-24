"""Schemas — Computation workflow."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import ComputationStatus


class ComputationUploadRequest(BaseModel):
    filing_id: UUID
    filename: str
    content_type: str


class ComputationUploadURLResponse(BaseModel):
    upload_url: str
    computation_id: Optional[UUID] = None
    version: int
    object_key: str


class ComputationResponse(BaseModel):
    id: UUID
    filing_id: UUID
    version: int
    file_id: UUID
    original_filename: Optional[str] = None
    status: ComputationStatus
    uploaded_by: UUID
    uploaded_by_name: Optional[str] = None
    uploaded_at: datetime
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    rejected_by: Optional[UUID] = None
    rejected_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    model_config = {"from_attributes": True}


class ComputationListResponse(BaseModel):
    items: list[ComputationResponse]
    current_version: Optional[ComputationResponse] = None


class ComputationApproveRequest(BaseModel):
    computation_id: UUID
    is_tax_paid: bool = False


class ComputationRejectRequest(BaseModel):
    computation_id: UUID
    reason: str
