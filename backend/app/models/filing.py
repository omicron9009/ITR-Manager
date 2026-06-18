"""Model: itr_filings — Core transaction table, one record per client per FY."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import FilingStatus


class ITRFiling(Base):
    __tablename__ = "itr_filings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    financial_year = Column(String(9), nullable=False)  # Format: '2024-2025'
    status = Column(Enum(FilingStatus, name="filing_status"), nullable=False, default=FilingStatus.INITIATED)
    assigned_executive_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Milestone timestamps
    initiated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    onboarding_completed_at = Column(DateTime(timezone=True), nullable=True)
    documents_submitted_at = Column(DateTime(timezone=True), nullable=True)
    documents_approved_at = Column(DateTime(timezone=True), nullable=True)
    computation_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    computation_approved_at = Column(DateTime(timezone=True), nullable=True)
    is_tax_paid = Column(Boolean, nullable=False, server_default="false", default=False)
    tax_paid_at = Column(DateTime(timezone=True), nullable=True)
    filed_at = Column(DateTime(timezone=True), nullable=True)
    payment_received_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Engagement letter
    professional_fee = Column(Numeric(10, 2), nullable=True)
    no_fees_applicable = Column(Boolean, nullable=False, server_default="false", default=False)
    engagement_accepted_at = Column(DateTime(timezone=True), nullable=True)
    engagement_letter_key = Column(Text, nullable=True)

    # Fee change proposal
    proposed_fee = Column(Numeric(10, 2), nullable=True)
    fee_proposed_at = Column(DateTime(timezone=True), nullable=True)
    fee_proposed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Halt info
    halted_at = Column(DateTime(timezone=True), nullable=True)
    halted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    halt_reason = Column(Text, nullable=True)

    # Income heads snapshot (confirmed by client at filing initiate / before doc upload)
    income_heads_snapshot = Column(JSONB, nullable=True)
    income_heads_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # Audit columns
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    client = relationship("User", foreign_keys=[client_id], back_populates="filings")
    assigned_executive = relationship("User", foreign_keys=[assigned_executive_id])
    halter = relationship("User", foreign_keys=[halted_by])
    creator = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])

    documents = relationship("FilingDocument", back_populates="filing", cascade="all, delete-orphan")
    text_fields = relationship("FilingTextField", back_populates="filing", cascade="all, delete-orphan")
    computations = relationship("FilingComputation", back_populates="filing", cascade="all, delete-orphan")
    completed_docs = relationship("FilingCompletedDoc", back_populates="filing", cascade="all, delete-orphan")
    other_docs = relationship("FilingOtherDoc", back_populates="filing", cascade="all, delete-orphan")
    internal_working_docs = relationship("InternalWorkingDoc", back_populates="filing", cascade="all, delete-orphan")
    state_history = relationship("FilingStateHistory", back_populates="filing", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("client_id", "financial_year", name="uq_client_fy"),
    )
