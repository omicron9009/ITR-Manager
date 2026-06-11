"""Model: master_doc_type_income_heads — Junction mapping a doc type to an income head + sub-category."""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import DocSubCategory, IncomeHeadCategory


class MasterDocTypeIncomeHead(Base):
    __tablename__ = "master_doc_type_income_heads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("master_document_types.id", ondelete="CASCADE"),
        nullable=False,
    )
    income_head = Column(
        Enum(IncomeHeadCategory, name="income_head_category"),
        nullable=False,
    )
    sub_category = Column(
        Enum(DocSubCategory, name="doc_sub_category"),
        nullable=False,
        default=DocSubCategory.INCREMENTAL,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    doc_type = relationship("MasterDocumentType", back_populates="income_head_mappings")

    __table_args__ = (
        UniqueConstraint("doc_type_id", "income_head", name="uq_doc_type_income_head"),
    )
