"""Schemas — Email configuration (Partner-only setup)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class EmailConfigSetupRequest(BaseModel):
    """Partner sets up email credentials for the platform."""
    sender_email: EmailStr
    credentials_json: str  # The OAuth client credentials JSON content (from Google Console)


class EmailConfigTokenRequest(BaseModel):
    """Partner provides the OAuth token after authorization flow."""
    token_json: str  # The OAuth token JSON (after browser auth)


class EmailConfigResponse(BaseModel):
    """Current email configuration status."""
    id: UUID
    sender_email: str
    is_configured: bool  # True if both credentials and token are set
    configured_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailConfigTestRequest(BaseModel):
    """Test the email configuration by sending a test email."""
    test_recipient: EmailStr


class EmailConfigAuthUrlResponse(BaseModel):
    """OAuth authorization URL for the partner to complete Gmail auth."""
    auth_url: str
    message: str
