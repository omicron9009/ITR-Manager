"""Schemas — Notifications."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import ReminderType


class NotificationResponse(BaseModel):
    id: UUID
    title: str
    message: str
    is_read: bool
    related_filing_id: Optional[UUID] = None
    related_client_id: Optional[UUID] = None
    # Populated (via LEFT JOIN to reminder_dispatch_logs) when the notification
    # was created by the reminders subsystem. NULL for regular notifications.
    reminder_type: Optional[ReminderType] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int


class NotificationMarkReadRequest(BaseModel):
    notification_ids: list[UUID]
