"""Schemas — User and Client related request/response models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


# ─── User Base ──────────────────────────────────────────────
class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)


class UserResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    phone_number: Optional[str] = None
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


# ─── Authentication ─────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    recovery_codes: Optional[list[str]] = None
    email: Optional[str] = None


# ─── Password Reset / Change ────────────────────────────────
class PasswordResetRequest(BaseModel):
    email: EmailStr
    recovery_code: str = Field(..., min_length=1, max_length=32)
    new_password: str = Field(..., min_length=8, max_length=128)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)


class RecoveryCodesResponse(BaseModel):
    codes: list[str]
    message: str


# ─── Email Change ────────────────────────────────────────────
class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    password: str = Field(..., min_length=1)


class ChangeEmailResponse(BaseModel):
    message: str
    email: str


# ─── Admin Recovery Codes ────────────────────────────────────
class AdminGenerateRecoveryCodesRequest(BaseModel):
    email: EmailStr


# ─── Client Registration ────────────────────────────────────
class ClientRegistrationRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)
    phone_number: Optional[str] = Field(None, max_length=20)
    declaration_accepted: bool = Field(..., description="Must be true to indicate consent to the data protection declaration")

    # Income Heads (Yes/No)
    salary: bool = Field(False)
    esop: bool = Field(False)
    rental_income: bool = Field(False)
    more_than_2_properties: bool = Field(False)
    capital_gain_shares: bool = Field(False)
    capital_gain_land: bool = Field(False)
    business_profession: bool = Field(False)
    interest_dividend: bool = Field(False)
    foreign_assets: bool = Field(False)
    any_other: bool = Field(False)

    @field_validator("declaration_accepted")
    @classmethod
    def validate_declaration_accepted(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the Confidentiality & Data Protection Declaration to register")
        return v

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            digits = v.strip()
            if not digits.isdigit() or len(digits) != 10:
                raise ValueError("Phone number must be exactly 10 digits")
        return v


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
    professional_fee: Optional[Decimal] = Field(None, gt=0, description="Professional fee in rupees (set by Partner)")


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


class IncomeHeadsResponse(BaseModel):
    salary: bool = False
    esop: bool = False
    rental_income: bool = False
    more_than_2_properties: bool = False
    capital_gain_shares: bool = False
    capital_gain_land: bool = False
    business_profession: bool = False
    interest_dividend: bool = False
    foreign_assets: bool = False
    any_other: bool = False

    model_config = {"from_attributes": True}


class IncomeHeadsUpdateRequest(BaseModel):
    """Update income heads — all fields optional for partial update."""
    salary: Optional[bool] = None
    esop: Optional[bool] = None
    rental_income: Optional[bool] = None
    more_than_2_properties: Optional[bool] = None
    capital_gain_shares: Optional[bool] = None
    capital_gain_land: Optional[bool] = None
    business_profession: Optional[bool] = None
    interest_dividend: Optional[bool] = None
    foreign_assets: Optional[bool] = None
    any_other: Optional[bool] = None


class ProfileUpdateRequest(BaseModel):
    """Update user profile (name)."""
    full_name: str = Field(..., min_length=1, max_length=255)


class ClientProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    pan_number: Optional[str] = None
    aadhaar_number: Optional[str] = None
    date_of_birth: Optional[date] = None
    contact_number: Optional[str] = None
    address: Optional[str] = None
    income_type: Optional[str] = None
    bank_account_details: Optional[str] = None
    form_data: dict[str, Any] = {}
    form_submitted_at: Optional[datetime] = None
    assigned_executive_id: Optional[UUID] = None
    assigned_executive_name: Optional[str] = None
    income_heads: Optional[IncomeHeadsResponse] = None
    professional_fee: Optional[Decimal] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ─── Client List (Admin View) ───────────────────────────────
class ClientListItem(BaseModel):
    id: UUID
    full_name: str
    email: str
    phone_number: Optional[str] = None
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
