"""Model: reminder_configs — Partner-managed configuration for each ReminderType.

One row per `ReminderType` (unique). Seeded on startup by
`_seed_reminder_configs` in `app/main.py` with `is_enabled=false` so nothing
fires until the Partner explicitly turns each type on.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base
from app.enums import ReminderType


def _default_channels() -> dict:
    """Fresh dict returned per row — never share a mutable default across rows."""
    return {"in_app": True, "email": True, "whatsapp": True}


class ReminderConfig(Base):
    __tablename__ = "reminder_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reminder_type = Column(
        Enum(ReminderType, name="reminder_type"),
        nullable=False,
        unique=True,
    )
    is_enabled = Column(Boolean, nullable=False, server_default="false", default=False)
    threshold_days = Column(Integer, nullable=False, server_default="7", default=7)
    repeat_interval_days = Column(Integer, nullable=False, server_default="3", default=3)
    max_sends = Column(Integer, nullable=False, server_default="5", default=5)
    channels = Column(
        JSONB,
        nullable=False,
        server_default=text(
            "'{\"in_app\": true, \"email\": true, \"whatsapp\": true}'::jsonb"
        ),
        default=_default_channels,
    )
    custom_title = Column(String(255), nullable=True)
    custom_message = Column(Text, nullable=True)
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
