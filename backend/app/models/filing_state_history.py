"""Model: filing_state_history — State transition log for filing state machine."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import FilingStatus


class FilingStateHistory(Base):
    __tablename__ = "filing_state_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    from_status = Column(Enum(FilingStatus, name="filing_status"), nullable=True)
    to_status = Column(Enum(FilingStatus, name="filing_status"), nullable=False)
    changed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    changed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    remarks = Column(Text, nullable=True)

    # Relationships
    filing = relationship("ITRFiling", back_populates="state_history")
    actor = relationship("User", foreign_keys=[changed_by])
