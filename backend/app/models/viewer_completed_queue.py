"""Model: viewer_completed_queue — Queue of completed filings for dashboard viewers."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ViewerCompletedQueue(Base):
    __tablename__ = "viewer_completed_queue"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    viewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    client_name = Column(String(255), nullable=False)
    financial_year = Column(String(20), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=False)
    completed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    dismissed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    viewer = relationship("User", foreign_keys=[viewer_id])
    filing = relationship("ITRFiling", foreign_keys=[filing_id])
    completed_by_user = relationship("User", foreign_keys=[completed_by])
