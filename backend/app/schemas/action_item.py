"""Schemas — Action Items (dynamically computed, no DB table)."""

from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import ActionItemPriority, ActionItemType


class ActionItemResponse(BaseModel):
    type: ActionItemType
    title: str
    description: str
    priority: ActionItemPriority
    related_filing_id: Optional[UUID] = None
    related_client_id: Optional[UUID] = None
    financial_year: Optional[str] = None
    metadata: dict = {}
    action_url: Optional[str] = None


class ActionItemListResponse(BaseModel):
    items: list[ActionItemResponse]
    total: int
    counts_by_type: dict[str, int]
