"""Schemas — Dashboard views and status tracking."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import FilingStatus


# ─── Partner / Executive Dashboard ──────────────────────────
class FilingStatusCounter(BaseModel):
    status: FilingStatus
    count: int
    label: str


class DashboardSummaryResponse(BaseModel):
    counters: list[FilingStatusCounter]
    total_clients: int
    pending_verification_count: int
    total_active_filings: int


# ─── Verification Queue (Partner Only) ──────────────────────
class PendingVerificationItem(BaseModel):
    id: UUID
    full_name: str
    email: str
    pan_document_id: Optional[UUID] = None
    pan_document_url: Optional[str] = None
    registered_at: datetime

    model_config = {"from_attributes": True}


class PendingVerificationResponse(BaseModel):
    items: list[PendingVerificationItem]
    total: int


# ─── Filing Drill-Down List ─────────────────────────────────
class FilingDrillDownItem(BaseModel):
    filing_id: UUID
    client_id: UUID
    client_name: str
    client_email: str
    financial_year: str
    status: FilingStatus
    assigned_executive_name: Optional[str] = None
    last_updated: datetime

    model_config = {"from_attributes": True}


class FilingDrillDownResponse(BaseModel):
    status: FilingStatus
    items: list[FilingDrillDownItem]
    total: int


# ─── Client Dashboard ───────────────────────────────────────
class ClientDashboardResponse(BaseModel):
    account_status: str
    full_name: str
    email: str
    pan_number: Optional[str] = None
    registered_at: datetime
    active_filings: list["ClientFilingOverview"]
    unread_notification_count: int


class ClientFilingOverview(BaseModel):
    filing_id: UUID
    financial_year: str
    status: FilingStatus
    progress_percentage: int
    initiated_at: datetime
    last_updated: datetime
    documents_total: int = 0
    documents_approved: int = 0
    documents_pending: int = 0
    documents_rejected: int = 0

    model_config = {"from_attributes": True}


# ─── Client Directory View ──────────────────────────────────
class DirectoryDocumentItem(BaseModel):
    id: UUID
    document_type_name: str
    status: str
    file_id: Optional[UUID] = None
    original_filename: Optional[str] = None
    uploaded_at: Optional[datetime] = None


class DirectoryComputationItem(BaseModel):
    id: UUID
    version: int
    status: str
    original_filename: Optional[str] = None
    uploaded_at: datetime


class DirectoryCompletedDocItem(BaseModel):
    id: UUID
    doc_type: str
    original_filename: Optional[str] = None
    uploaded_at: datetime


class FilingDirectoryResponse(BaseModel):
    filing_id: UUID
    financial_year: str
    status: FilingStatus
    documents_required: list[DirectoryDocumentItem]
    computations: list[DirectoryComputationItem]
    completed_docs: list[DirectoryCompletedDocItem]


# ─── Executive Workload ─────────────────────────────────────
class ExecutiveWorkloadItem(BaseModel):
    executive_id: UUID
    executive_name: str
    assigned_clients: int
    active_filings: int

    model_config = {"from_attributes": True}


class ExecutiveWorkloadResponse(BaseModel):
    items: list[ExecutiveWorkloadItem]
