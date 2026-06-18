"""Model: notifications — In-app notification feed per user."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import NotificationChannel


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False)
    channel = Column(Enum(NotificationChannel, name="notification_channel"), nullable=False, default=NotificationChannel.BOTH)
    email_sent = Column(Boolean, nullable=False, default=False)
    email_sent_at = Column(DateTime(timezone=True), nullable=True)
    # WhatsApp delivery (CLIENT role only, opt-in based)
    whatsapp_sent = Column(Boolean, nullable=False, default=False)
    whatsapp_sent_at = Column(DateTime(timezone=True), nullable=True)
    whatsapp_message_id = Column(String(120), nullable=True)
    whatsapp_error = Column(Text, nullable=True)
    related_filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="SET NULL"), nullable=True)
    related_client_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="notifications")
    related_filing = relationship("ITRFiling", foreign_keys=[related_filing_id])
    related_client = relationship("User", foreign_keys=[related_client_id])
