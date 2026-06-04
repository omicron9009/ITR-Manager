"""Model: filing_completed_docs — ITR Acknowledgement and Invoice documents."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import CompletedDocStatus, CompletedDocType


class FilingCompletedDoc(Base):
    __tablename__ = "filing_completed_docs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    doc_type = Column(Enum(CompletedDocType, name="completed_doc_type"), nullable=False)
    file_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False)
    status = Column(
        Enum(CompletedDocStatus, name="completed_doc_status"),
        nullable=False,
        default=CompletedDocStatus.UPLOADED,
    )
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # Manager approval
    manager_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)

    # Manager rejection
    manager_rejected_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manager_rejected_at = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Partner approval
    partner_approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    partner_approved_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    filing = relationship("ITRFiling", back_populates="completed_docs")
    file = relationship("StoredFile", foreign_keys=[file_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
    manager_approver = relationship("User", foreign_keys=[manager_approved_by])
    manager_rejector = relationship("User", foreign_keys=[manager_rejected_by])
    partner_approver = relationship("User", foreign_keys=[partner_approved_by])

    __table_args__ = (
        UniqueConstraint("filing_id", "doc_type", name="uq_filing_completed_doc_type"),
    )
