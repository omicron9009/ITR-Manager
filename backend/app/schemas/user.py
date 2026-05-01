"""Schemas — User and Client related request/response models."""

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ─── User Base ──────────────────────────────────────────────
class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    account_status: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UserBrief(BaseModel):
    id: UUID
    full_name: str
    email: str
    role: str

    model_config = {"from_attributes": True}


# ─── Client Registration ────────────────────────────────────
class ClientRegistrationRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    authentik_subject_id: str = Field(..., min_length=1, max_length=255)


class ClientRegistrationResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    account_status: str
    message: str = "Your account is under verification by our team."

    model_config = {"from_attributes": True}


# ─── Client Activation / Rejection ──────────────────────────
class ClientActivationRequest(BaseModel):
    client_id: UUID


class ClientRejectionRequest(BaseModel):
    client_id: UUID
    reason: str = Field(..., min_length=1, max_length=1000)


# ─── Client Profile ─────────────────────────────────────────
class ClientProfileUpdate(BaseModel):
    pan_number: Optional[str] = Field(None, pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
    aadhaar_number: Optional[str] = Field(None, pattern=r"^\d{12}$")
    date_of_birth: Optional[date] = None
    contact_number: Optional[str] = Field(None, max_length=15)
    address: Optional[str] = None
    income_type: Optional[str] = Field(None, max_length=50)
    bank_account_details: Optional[str] = None
    form_data: Optional[dict[str, Any]] = None


class ClientProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    date_of_birth: Optional[date] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None
    income_type: Optional[str] = None
    bank_account_details: Optional[str] = None
    form_data: dict[str, Any] = {}
    form_submitted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Client List (Admin View) ───────────────────────────────
class ClientListItem(BaseModel):
    id: UUID
    full_name: str
    email: str
    account_status: str
    assigned_executive_name: Optional[str] = None
    assigned_executive_id: Optional[UUID] = None
    active_filing_years: list[str] = []
    current_state: Optional[str] = None
    last_updated: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ClientListResponse(BaseModel):
    items: list[ClientListItem]
    total: int
    page: int
    page_size: int
