"""Model: audit_logs — Append-only audit trail for all system events."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import AuditEventType


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(Enum(AuditEventType, name="audit_event_type"), nullable=False)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="SET NULL"), nullable=True)
    document_id = Column(UUID(as_uuid=True), nullable=True)
    details = Column(JSONB, nullable=False, default=dict)
    ip_address = Column(INET, nullable=True)
    user_agent = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    actor = relationship("User", foreign_keys=[actor_id])
    client = relationship("User", foreign_keys=[client_id])
    filing = relationship("ITRFiling", foreign_keys=[filing_id])
