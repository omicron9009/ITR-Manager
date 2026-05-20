"""Model: executive_tags — Maps Tags to Executives (many-to-many)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ExecutiveTag(Base):
    __tablename__ = "executive_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    executive_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tag_id = Column(UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    # Relationships
    executive = relationship("User", foreign_keys=[executive_id])
    tag = relationship("Tag", foreign_keys=[tag_id], back_populates="executive_tags")
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        UniqueConstraint("executive_id", "tag_id", name="uq_executive_tag"),
    )
