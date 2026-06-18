"""Model: whatsapp_config — Partner-managed OpenWA gateway configuration (singleton)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class WhatsAppConfig(Base):
    """Single-row config that the Partner manages from the UI.

    The OpenWA admin API key is stored Fernet-encrypted (see whatsapp_service).
    Session lifecycle fields (status / phone / connected_at) are kept in sync
    with OpenWA on every status check.
    """

    __tablename__ = "whatsapp_config"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # OpenWA gateway connection
    openwa_base_url = Column(String(255), nullable=False)
    api_key_encrypted = Column(Text, nullable=False)
    session_name = Column(String(50), nullable=False, default="itr-platform")
    openwa_session_id = Column(String(100), nullable=True)

    # Cached session state (updated on every /session/status call)
    session_status = Column(String(32), nullable=False, default="created")
    phone_number_e164 = Column(String(20), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    last_qr_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)
    configured_by = Column(UUID(as_uuid=True), nullable=False)

    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
