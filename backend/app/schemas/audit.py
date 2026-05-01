"""Schemas — Audit log."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import AuditEventType


class AuditLogResponse(BaseModel):
    id: UUID
    event_type: AuditEventType
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    client_id: Optional[UUID] = None
    client_name: Optional[str] = None
    filing_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    details: dict[str, Any] = {}
    ip_address: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int


class AuditLogFilterRequest(BaseModel):
    client_id: Optional[UUID] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    event_types: Optional[list[AuditEventType]] = None
    page: int = 1
    page_size: int = 50
