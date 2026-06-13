"""Model: internal_working_docs — Internal working documents uploaded during computation phase.

Supports versioning via "replace" semantics:
- ``replaces_id`` points to the older row this row supersedes (NULL on first upload).
- ``superseded_at`` is set on the OLD row when it is replaced; active rows have NULL.
- On replace, the old MinIO object is preserved (no S3 delete) so history is recoverable.
"""

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class InternalWorkingDoc(Base):
    __tablename__ = "internal_working_docs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filing_id = Column(UUID(as_uuid=True), ForeignKey("itr_filings.id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(UUID(as_uuid=True), ForeignKey("stored_files.id", ondelete="RESTRICT"), nullable=False)
    label = Column(String(255), nullable=True)  # Optional description
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    # ── Versioning fields ──────────────────────────────────────
    # New row points back to the older row it replaces (NULL = original upload)
    replaces_id = Column(
        UUID(as_uuid=True),
        ForeignKey("internal_working_docs.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Set on the OLD row when it is replaced. NULL = active.
    superseded_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    filing = relationship("ITRFiling", back_populates="internal_working_docs")
    file = relationship("StoredFile", foreign_keys=[file_id])
    uploader = relationship("User", foreign_keys=[uploaded_by])
    replaces = relationship("InternalWorkingDoc", remote_side=[id], foreign_keys=[replaces_id])
