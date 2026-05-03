"""Model: filing_computations — Versioned computation documents per filing."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import ComputationStatus


class FilingComputation(Base):
    __tablename__ = "filing_computations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    file_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False)
    status = Column(
        Enum(ComputationStatus, name="computation_status"), nullable=False, default=ComputationStatus.UPLOADED
    )
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    filing = relationship("ITRFiling", back_populates="computations")
    file = relationship("StoredFile", foreign_keys=[file_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
    approver = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        UniqueConstraint("filing_id", "version", name="uq_filing_computation_version"),
    )
