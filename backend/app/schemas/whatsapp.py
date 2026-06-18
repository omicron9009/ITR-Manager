"""Schemas — WhatsApp configuration and messaging."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ─── Config ──────────────────────────────────────────────────
class WhatsAppConfigSetupRequest(BaseModel):
    openwa_base_url: str = Field(..., min_length=4, max_length=255)
    api_key: str = Field(..., min_length=8, max_length=512)
    session_name: str = Field(default="itr-platform", min_length=3, max_length=50)

    @field_validator("openwa_base_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("openwa_base_url must start with http:// or https://")
        return v

    @field_validator("session_name")
    @classmethod
    def _validate_session_name(cls, v: str) -> str:
        v = v.strip()
        if not all(c.isalnum() or c == "-" for c in v):
            raise ValueError("session_name must be alphanumeric with hyphens only")
        return v


class WhatsAppConfigResponse(BaseModel):
    id: UUID
    openwa_base_url: str
    session_name: str
    openwa_session_id: Optional[str] = None
    session_status: str
    phone_number_e164: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_qr_at: Optional[datetime] = None
    last_error: Optional[str] = None
    is_active: bool
    is_configured: bool = True
    configured_by: UUID
    created_at: datetime
    updated_at: datetime


# ─── Session ─────────────────────────────────────────────────
class WhatsAppSessionStatus(BaseModel):
    session_id: Optional[str] = None
    status: str
    phone_number_e164: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_error: Optional[str] = None


class WhatsAppQRResponse(BaseModel):
    qr_code: Optional[str] = None  # data:image/png;base64,...
    status: str


# ─── Messaging ───────────────────────────────────────────────
class WhatsAppTestRequest(BaseModel):
    to_phone: str = Field(..., min_length=8, max_length=20, description="E.164, e.g. +919876543210")
    text: Optional[str] = Field(default=None, max_length=1000)


class WhatsAppSendResponse(BaseModel):
    sent: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


# ─── Client opt-in ───────────────────────────────────────────
class ClientWhatsAppPreferenceResponse(BaseModel):
    phone_number: Optional[str] = None
    whatsapp_opt_in: bool


class ClientWhatsAppPreferenceUpdate(BaseModel):
    phone_number: Optional[str] = Field(default=None, max_length=20)
    whatsapp_opt_in: bool

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip().replace(" ", "")
        if not v.startswith("+"):
            raise ValueError("phone_number must be E.164 (start with '+', e.g. +919876543210)")
        digits = v[1:]
        if not digits.isdigit() or not (8 <= len(digits) <= 15):
            raise ValueError("phone_number must contain 8-15 digits after '+'")
        return v
