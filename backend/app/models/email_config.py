"""Model: email_config — Partner-managed email/SMTP configuration."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class EmailConfig(Base):
    __tablename__ = "email_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_email = Column(String(255), nullable=False)
    credentials_json = Column(Text, nullable=False)  # Encrypted OAuth credentials JSON
    token_json = Column(Text, nullable=True)  # Encrypted OAuth token JSON (after auth)
    configured_by = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(
        # Only one active config at a time
        default=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
