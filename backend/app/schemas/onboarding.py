"""Schemas — Onboarding form builder."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import FormFieldType


class FormFieldCreateRequest(BaseModel):
    field_label: str = Field(..., min_length=1, max_length=255)
    field_key: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z_][a-z0-9_]*$")
    field_type: FormFieldType
    field_options: Optional[list[str]] = None
    is_required: bool = False
    display_order: int = 0


class FormFieldUpdateRequest(BaseModel):
    field_label: Optional[str] = Field(None, min_length=1, max_length=255)
    field_type: Optional[FormFieldType] = None
    field_options: Optional[list[str]] = None
    is_required: Optional[bool] = None
    display_order: Optional[int] = None
    is_active: Optional[bool] = None


class FormFieldResponse(BaseModel):
    id: UUID
    field_label: str
    field_key: str
    field_type: FormFieldType
    field_options: Optional[list[str]] = None
    is_required: bool
    display_order: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class FormFieldListResponse(BaseModel):
    items: list[FormFieldResponse]
    total: int


class OnboardingFormSubmitRequest(BaseModel):
    form_data: dict[str, Any]


class OnboardingFormResponse(BaseModel):
    fields: list[FormFieldResponse]
    submitted: bool = False
    submitted_data: Optional[dict[str, Any]] = None
    submitted_at: Optional[datetime] = None
