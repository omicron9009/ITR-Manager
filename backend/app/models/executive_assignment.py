"""Model: executive_client_assignments — Maps Executive to Client."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ExecutiveClientAssignment(Base):
    __tablename__ = "executive_client_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    executive_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    unassigned_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    # Relationships
    executive = relationship("User", foreign_keys=[executive_id], back_populates="executive_assignments")
    client = relationship("User", foreign_keys=[client_id], back_populates="client_assignments")
    assigner = relationship("User", foreign_keys=[assigned_by])
