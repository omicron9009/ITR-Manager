"""Model: filing_other_docs — Additional/miscellaneous documents uploaded by Executive/Partner."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class FilingOtherDoc(Base):
    __tablename__ = "filing_other_docs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False)
    label = Column(String(255), nullable=True)  # Optional description (e.g. "Capital Gains Statement")
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    filing = relationship("ITRFiling", back_populates="other_docs")
    file = relationship("StoredFile", foreign_keys=[file_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
