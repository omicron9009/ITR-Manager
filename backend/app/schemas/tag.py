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
    client_count: int = 0

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


class LocationSummaryItem(BaseModel):
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0


class LocationSummaryResponse(BaseModel):
    items: list[LocationSummaryItem]
    total: int


# ─── Detailed Single-Tag View ───────────────────────────────

class FilingBriefItem(BaseModel):
    filing_id: UUID
    client_name: str
    financial_year: str
    status: str
    last_updated: datetime


class LocationDetailResponse(BaseModel):
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    executives: list[ExecutiveBrief] = []
    recent_filings: list[FilingBriefItem] = []


# ─── Partner Tag Summary / Analytics ────────────────────────

class PartnerTagSummaryItem(BaseModel):
    tag_id: UUID
    tag_name: str
    client_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0


class PartnerTagSummaryResponse(BaseModel):
    items: list[PartnerTagSummaryItem]
    total: int


class ClientBriefItem(BaseModel):
    client_id: UUID
    client_name: str
    account_status: str
    active_filing_year: Optional[str] = None
    filing_status: Optional[str] = None


class PartnerTagDetailResponse(BaseModel):
    tag_id: UUID
    tag_name: str
    client_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    clients: list[ClientBriefItem] = []
    recent_filings: list[FilingBriefItem] = []
