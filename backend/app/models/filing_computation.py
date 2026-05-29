"""Model: filing_computations — Versioned computation documents per filing."""

import uuid
from datetime import datetime

import sqlalchemy as sa
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

    # Manager approval (first level)
    manager_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)
    manager_rejected_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manager_rejected_at = Column(DateTime(timezone=True), nullable=True)
    manager_rejection_reason = Column(sa.Text, nullable=True)

    # Partner approval (second level)
    partner_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    partner_approved_at = Column(DateTime(timezone=True), nullable=True)

    # Client approval/rejection (final)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(sa.Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Relationships
    filing = relationship("ITRFiling", back_populates="computations")
    file = relationship("StoredFile", foreign_keys=[file_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
    manager_approver = relationship("User", foreign_keys=[manager_approved_by])
    manager_rejector = relationship("User", foreign_keys=[manager_rejected_by])
    partner_approver = relationship("User", foreign_keys=[partner_approved_by])
    approver = relationship("User", foreign_keys=[approved_by])
    rejector = relationship("User", foreign_keys=[rejected_by])

    __table_args__ = (
        UniqueConstraint("filing_id", "version", name="uq_filing_computation_version"),
    )
