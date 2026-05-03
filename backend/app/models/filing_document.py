"""Model: filing_documents — Document placeholders and upload tracking per filing."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import DocumentStatus


class FilingDocument(Base):
    __tablename__ = "filing_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    document_type_id = Column(
        UUID(as_uuid=True), ForeignKey("master_document_types.id", ondelete="RESTRICT"), nullable=False
    )
    status = Column(Enum(DocumentStatus, name="document_status"), nullable=False, default=DocumentStatus.PENDING_UPLOAD)
    file_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    filing = relationship("ITRFiling", back_populates="documents")
    document_type = relationship("MasterDocumentType", back_populates="filing_documents")
    file = relationship("StoredFile", foreign_keys=[file_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        UniqueConstraint("filing_id", "document_type_id", name="uq_filing_doc_type"),
    )
