"""Schemas — Reminders subsystem (Partner-managed rules + dispatch log)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ReminderType


class ReminderChannels(BaseModel):
    """Per-reminder channel flags stored on `reminder_configs.channels` (JSONB)."""

    in_app: bool = True
    email: bool = True
    whatsapp: bool = True

    model_config = ConfigDict(from_attributes=True)


class ReminderConfigResponse(BaseModel):
    id: UUID
    reminder_type: ReminderType
    is_enabled: bool
    threshold_days: int
    repeat_interval_days: int
    max_sends: int
    channels: ReminderChannels
    custom_title: Optional[str] = None
    custom_message: Optional[str] = None
    updated_by: Optional[UUID] = None
    updated_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReminderConfigUpdate(BaseModel):
    """PATCH-style update body — only non-None fields are applied."""

    is_enabled: Optional[bool] = None
    threshold_days: Optional[int] = Field(default=None, ge=0, le=365)
    repeat_interval_days: Optional[int] = Field(default=None, ge=0, le=365)
    max_sends: Optional[int] = Field(default=None, ge=0, le=100)
    channels: Optional[ReminderChannels] = None
    custom_title: Optional[str] = Field(default=None, max_length=255)
    custom_message: Optional[str] = Field(default=None, max_length=2000)


class ReminderDispatchLogResponse(BaseModel):
    id: UUID
    reminder_type: ReminderType
    subject_user_id: UUID
    related_client_id: Optional[UUID] = None
    related_filing_id: Optional[UUID] = None
    dedup_key: str
    notification_id: Optional[UUID] = None
    sent_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReminderDispatchLogPage(BaseModel):
    items: list[ReminderDispatchLogResponse]
    total: int
    page: int
    page_size: int


class ReminderRunNowResponse(BaseModel):
    started_at: datetime
    finished_at: datetime
    dispatched: dict[str, int]
    skipped: dict[str, int]
