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
    phone_number: Optional[str] = None
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


# ═══════════════════════════════════════════════════════════════
# ANALYTICS — Partner / Executive / Client
# ═══════════════════════════════════════════════════════════════


# ─── Shared sub-models ──────────────────────────────────────

class ExecutiveClientInfo(BaseModel):
    client_id: UUID
    client_name: str
    client_email: str
    filing_status: Optional[str] = None
    financial_year: Optional[str] = None


class FilingStatusClientInfo(BaseModel):
    client_id: UUID
    client_name: str
    financial_year: str
    assigned_executive: Optional[str] = None
    last_updated: Optional[datetime] = None


class ClientStatusBreakdown(BaseModel):
    status: str
    count: int


class FilingStatusBreakdown(BaseModel):
    status: str
    count: int
    clients: list[FilingStatusClientInfo]


class FYDistribution(BaseModel):
    financial_year: str
    total_filings: int
    completed: int
    active: int


class ExecutiveClientDetail(BaseModel):
    executive_id: UUID
    executive_name: str
    executive_email: str
    is_active: bool
    clients: list[ExecutiveClientInfo]
    total_clients: int
    active_filings: int
    completed_filings: int


# ─── Partner Analytics ──────────────────────────────────────

class PartnerAnalyticsResponse(BaseModel):
    # Overview
    total_clients: int
    active_clients: int
    pending_verification_clients: int
    rejected_clients: int
    total_executives: int
    active_executives: int

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int
    halted_filings: int

    # Executive → Client mapping
    executive_client_mapping: list[ExecutiveClientDetail]
    unassigned_clients: list[ExecutiveClientInfo]

    # State-wise breakdown with client details
    filing_status_breakdown: list[FilingStatusBreakdown]

    # Client account status breakdown
    client_status_breakdown: list[ClientStatusBreakdown]

    # Financial year distribution
    fy_distribution: list[FYDistribution]

    # Average processing times (in days)
    avg_days_initiated_to_completed: Optional[float] = None
    avg_days_in_processing: Optional[float] = None
    avg_days_in_computation: Optional[float] = None

    # Recent activity
    recent_filings: list[FilingStatusClientInfo]


# ─── Executive Analytics ────────────────────────────────────

class ExecutiveAnalyticsResponse(BaseModel):
    executive_name: str
    executive_email: str

    # Client overview
    total_assigned_clients: int
    clients: list[ExecutiveClientInfo]

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int
    halted_filings: int

    # State-wise breakdown (assigned clients only)
    filing_status_breakdown: list[FilingStatusBreakdown]

    # FY distribution
    fy_distribution: list[FYDistribution]

    # Processing metrics
    avg_days_initiated_to_completed: Optional[float] = None
    avg_days_in_processing: Optional[float] = None

    # Document stats
    total_documents_pending: int = 0
    total_documents_rejected: int = 0
    total_documents_approved: int = 0

    # Recent activity
    recent_filings: list[FilingStatusClientInfo]


# ─── Client Analytics ───────────────────────────────────────

class ClientFilingDetail(BaseModel):
    filing_id: UUID
    financial_year: str
    status: str
    progress_percentage: int
    initiated_at: datetime
    completed_at: Optional[datetime] = None
    last_updated: datetime
    assigned_executive_name: Optional[str] = None
    documents_total: int = 0
    documents_approved: int = 0
    documents_pending: int = 0
    documents_rejected: int = 0
    computation_status: Optional[str] = None
    days_since_initiated: int = 0


class ClientAnalyticsResponse(BaseModel):
    # Profile
    client_name: str
    client_email: str
    account_status: str
    registered_at: datetime
    pan_number: Optional[str] = None

    # Filing overview
    total_filings: int
    active_filings: int
    completed_filings: int

    # Detailed filing list
    filings: list[ClientFilingDetail]

    # Notifications
    total_notifications: int
    unread_notifications: int

    # Document overview across all filings
    total_documents: int
    total_approved: int
    total_pending: int
    total_rejected: int
