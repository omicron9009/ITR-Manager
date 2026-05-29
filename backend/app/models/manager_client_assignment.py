"""Model: manager_client_assignments — Manager-to-Client ownership relationships."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class ManagerClientAssignment(Base):
    __tablename__ = "manager_client_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    client_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)

    # Relationships
    manager = relationship("User", foreign_keys=[manager_id], backref="managed_clients")
    client = relationship("User", foreign_keys=[client_id], backref="manager_assignment")
    assigner = relationship("User", foreign_keys=[assigned_by])

    __table_args__ = (
        UniqueConstraint("manager_id", "client_id", name="uq_manager_client"),
    )
