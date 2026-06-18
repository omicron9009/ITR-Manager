"""Model: stored_files — MinIO object metadata for all uploaded files."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class StoredFile(Base):
    __tablename__ = "stored_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bucket = Column(String(63), nullable=False)
    object_key = Column(Text, nullable=False)
    original_filename = Column(Text, nullable=False)
    content_type = Column(String(255), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Relationships
    uploader = relationship("User", foreign_keys=[uploaded_by], back_populates="uploaded_files")

    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="idx_stored_files_bucket_key"),
    )
