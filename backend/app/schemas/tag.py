"""Schemas — Tag management and tag-based analytics."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.enums import TagType


# ─── Tag CRUD ───────────────────────────────────────────────

class TagCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    tag_type: TagType
    description: Optional[str] = None


class TagUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class TagResponse(BaseModel):
    id: UUID
    name: str
    tag_type: TagType
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    executive_count: int = 0

    model_config = {"from_attributes": True}


class TagListResponse(BaseModel):
    items: list[TagResponse]
    total: int


# ─── Tag Assignment ─────────────────────────────────────────

class ExecutiveTagAssignRequest(BaseModel):
    executive_id: UUID
    tag_id: UUID


class ExecutiveTagBulkAssignRequest(BaseModel):
    executive_ids: list[UUID] = Field(..., min_length=1)
    tag_id: UUID


class ExecutiveTagResponse(BaseModel):
    id: UUID
    executive_id: UUID
    executive_name: str
    tag_id: UUID
    tag_name: str
    tag_type: TagType
    assigned_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


# ─── Executive Tags View ────────────────────────────────────

class TagBrief(BaseModel):
    id: UUID
    name: str
    tag_type: TagType


class ExecutiveTagsView(BaseModel):
    executive_id: UUID
    executive_name: str
    manager_tags: list[TagBrief]
    location_tags: list[TagBrief]


class ExecutiveTagsListResponse(BaseModel):
    items: list[ExecutiveTagsView]
    total: int


# ─── Summary / Analytics ────────────────────────────────────

class ExecutiveBrief(BaseModel):
    executive_id: UUID
    executive_name: str
    active_filings: int = 0
    completed_filings: int = 0
    total_filings: int = 0


class ManagerSummaryItem(BaseModel):
    tag_id: UUID
    manager_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    executives: list[ExecutiveBrief] = []


class ManagerSummaryResponse(BaseModel):
    items: list[ManagerSummaryItem]
    total: int


class LocationSummaryItem(BaseModel):
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    manager_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0


class LocationSummaryResponse(BaseModel):
    items: list[LocationSummaryItem]
    total: int


# ─── Hierarchy: Location → Managers → Executives ────────────

class HierarchyManagerItem(BaseModel):
    tag_id: UUID
    manager_name: str
    executives: list[ExecutiveBrief] = []
    total_filings: int = 0
    completed_filings: int = 0


class HierarchyLocationItem(BaseModel):
    tag_id: UUID
    location_name: str
    managers: list[HierarchyManagerItem] = []
    total_executives: int = 0
    total_filings: int = 0
    completed_filings: int = 0


class HierarchyResponse(BaseModel):
    items: list[HierarchyLocationItem]
    total: int


# ─── Detailed Single-Tag View ───────────────────────────────

class FilingBriefItem(BaseModel):
    filing_id: UUID
    client_name: str
    financial_year: str
    status: str
    last_updated: datetime


class ManagerDetailResponse(BaseModel):
    tag_id: UUID
    manager_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    executives: list[ExecutiveBrief] = []
    recent_filings: list[FilingBriefItem] = []


class LocationDetailResponse(BaseModel):
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    manager_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    executives: list[ExecutiveBrief] = []
    recent_filings: list[FilingBriefItem] = []
