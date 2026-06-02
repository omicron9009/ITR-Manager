"""Model: email_config — Partner-managed SMTP configuration."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class EmailConfig(Base):
    __tablename__ = "email_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_email = Column(String(255), nullable=False)
    smtp_host = Column(String(255), nullable=False, default="smtp.gmail.com")
    smtp_port = Column(Integer, nullable=False, default=587)
    smtp_user = Column(String(255), nullable=False)
    smtp_password = Column(String(255), nullable=False)
    use_tls = Column(Boolean, nullable=False, default=True)
    configured_by = Column(UUID(as_uuid=True), nullable=False)
    is_active = Column(
        Boolean,
        default=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
