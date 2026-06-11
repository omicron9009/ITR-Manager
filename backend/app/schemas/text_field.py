"""Schemas — Text-field placeholders (free-form per-filing inputs)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import TextFieldStatus


# ─── Master Text Field Type ─────────────────────────────────
class MasterTextFieldTypeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    max_length: int = Field(200, ge=1, le=2000)
    display_order: int = 0


class MasterTextFieldTypeUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    max_length: Optional[int] = Field(None, ge=1, le=2000)
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class MasterTextFieldTypeResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    max_length: int
    is_active: bool
    display_order: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MasterTextFieldTypeListResponse(BaseModel):
    items: list[MasterTextFieldTypeResponse]
    total: int


# ─── Filing Text-Field Placeholder ──────────────────────────
class TextFieldAssignRequest(BaseModel):
    field_type_ids: list[UUID] = Field(..., min_length=1)


class TextFieldValueRequest(BaseModel):
    value: str = Field(..., min_length=1)


class FilingTextFieldResponse(BaseModel):
    id: UUID
    filing_id: UUID
    field_type_id: UUID
    field_type_name: Optional[str] = None
    field_type_max_length: Optional[int] = None
    field_type_description: Optional[str] = None
    status: TextFieldStatus
    value: Optional[str] = None
    rejection_reason: Optional[str] = None
    filled_at: Optional[datetime] = None
    filled_by: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[UUID] = None
    assigned_at: datetime

    model_config = {"from_attributes": True}


class FilingTextFieldGroupResponse(BaseModel):
    """Text fields grouped by type — multiple values per type allowed."""
    field_type_id: UUID
    field_type_name: str
    fields: list[FilingTextFieldResponse]


class FilingTextFieldListResponse(BaseModel):
    items: list[FilingTextFieldResponse]
    groups: list[FilingTextFieldGroupResponse] = []
    total: int
    pending_count: int = 0
    filled_count: int = 0
    rejected_count: int = 0
    approved_count: int = 0
    all_approved: bool = False


# ─── Approve / Reject ───────────────────────────────────────
class TextFieldApproveRequest(BaseModel):
    field_ids: list[UUID] = Field(..., min_length=1)


class TextFieldRejectionItem(BaseModel):
    field_id: UUID
    reason: str = Field(..., min_length=1, max_length=1000)


class TextFieldRejectRequest(BaseModel):
    rejections: list[TextFieldRejectionItem] = Field(..., min_length=1)
