"""Model: filing_text_fields — Per-filing text-field placeholders + values."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import TextFieldStatus


class FilingTextField(Base):
    __tablename__ = "filing_text_fields"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False)
    field_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("master_text_field_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status = Column(
        Enum(TextFieldStatus, name="text_field_status"),
        nullable=False,
        default=TextFieldStatus.PENDING,
    )
    value = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    filled_at = Column(DateTime(timezone=True), nullable=True)
    filled_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    filing = relationship("ITRFiling", back_populates="text_fields")
    field_type = relationship("MasterTextFieldType", back_populates="filing_text_fields")
    filler = relationship("User", foreign_keys=[filled_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        # Performance index — multiple instances of same field type per filing allowed
        Index("ix_filing_text_field_type", "filing_id", "field_type_id"),
    )
