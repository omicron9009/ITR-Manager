"""Schemas — Email configuration (Partner-only SMTP setup)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class EmailConfigSetupRequest(BaseModel):
    """Partner sets up SMTP credentials for the platform."""
    sender_email: EmailStr
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    use_tls: bool = True


class EmailConfigResponse(BaseModel):
    """Current email configuration status."""
    id: UUID
    sender_email: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    use_tls: bool
    is_configured: bool
    configured_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailConfigTestRequest(BaseModel):
    """Test the email configuration by sending a test email."""
    test_recipient: EmailStr
