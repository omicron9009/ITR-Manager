"""Model: reminder_dispatch_logs — One row per reminder actually sent.

Used for dedup / rate-limit checks: `dedup_key` follows the format
`"{ReminderType.value}:{filing_id or 'no_filing'}:{recipient_user_id}"`.
See `services/reminder_service.py::_should_send`.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base
from app.enums import ReminderType


class ReminderDispatchLog(Base):
    __tablename__ = "reminder_dispatch_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reminder_type = Column(Enum(ReminderType, name="reminder_type"), nullable=False)
    subject_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    related_client_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    related_filing_id = Column(
        UUID(as_uuid=True),
        ForeignKey("itr_filings.id", ondelete="SET NULL"),
        nullable=True,
    )
    dedup_key = Column(String(255), nullable=False)
    notification_id = Column(
        UUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="SET NULL"),
        nullable=True,
    )
    sent_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_reminder_dispatch_dedup_sent", "dedup_key", "sent_at"),
    )
