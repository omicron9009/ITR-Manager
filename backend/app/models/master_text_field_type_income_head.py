"""Model: master_text_field_type_income_heads — Junction mapping a text-field type to an income head + sub-category.

Mirrors `master_doc_type_income_heads`. Allows text-field types to be categorized
under one or more income heads (BASE or INCREMENTAL), so that BASE text-field
placeholders can be auto-assigned when the client confirms income heads.

No mapping rows => the text-field type lives in the OTHERS bucket implicitly
(unchanged from prior behaviour).
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import DocSubCategory, IncomeHeadCategory


class MasterTextFieldTypeIncomeHead(Base):
    __tablename__ = "master_text_field_type_income_heads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    text_field_type_id = Column(
        UUID(as_uuid=True),
        ForeignKey("master_text_field_types.id", ondelete="CASCADE"),
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

    text_field_type = relationship(
        "MasterTextFieldType",
        back_populates="income_head_mappings",
    )

    __table_args__ = (
        UniqueConstraint(
            "text_field_type_id",
            "income_head",
            name="uq_text_field_type_income_head",
        ),
    )
