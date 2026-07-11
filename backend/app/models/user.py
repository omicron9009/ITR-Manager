"""Model: users — All system users (Partner, Executive, Client)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base
from app.enums import AccountStatus, UserRole


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    phone_number = Column(String(20), nullable=True)
    role = Column(Enum(UserRole, name="user_role"), nullable=False)
    account_status = Column(
        Enum(AccountStatus, name="account_status"),
        nullable=False,
        default=AccountStatus.PENDING_VERIFICATION,
    )
    pan_document_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="SET NULL"), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_elevated = Column(Boolean, nullable=False, default=False)
    recovery_codes_issued = Column(Boolean, nullable=False, default=False)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    activated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    pan_document = relationship("StoredFile", foreign_keys=[pan_document_id])
    activator = relationship("User", remote_side=[id], foreign_keys=[activated_by])
    client_profile = relationship("ClientProfile", back_populates="user", uselist=False, foreign_keys="[ClientProfile.user_id]")
    income_heads = relationship("ClientIncomeHeads", back_populates="user", uselist=False)
    uploaded_files = relationship("StoredFile", foreign_keys="[StoredFile.uploaded_by]", back_populates="uploader")
    filings = relationship("ITRFiling", foreign_keys="[ITRFiling.client_id]", back_populates="client")
    notifications = relationship("Notification", foreign_keys="[Notification.user_id]", back_populates="user")

    # Executive assignments where this user IS the executive
    executive_assignments = relationship(
        "ExecutiveClientAssignment",
        foreign_keys="[ExecutiveClientAssignment.executive_id]",
        back_populates="executive",
    )
    # Client assignments where this user IS the client
    client_assignments = relationship(
        "ExecutiveClientAssignment",
        foreign_keys="[ExecutiveClientAssignment.client_id]",
        back_populates="client",
    )
