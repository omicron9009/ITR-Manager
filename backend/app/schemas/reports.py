"""Schemas — Comprehensive reports for DASHBOARD_USER / Partner."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.enums import FilingStatus


# ─── Building Blocks ────────────────────────────────────────

class StatusCount(BaseModel):
    status: str
    count: int


class ClientFilingBrief(BaseModel):
    filing_id: UUID
    client_id: UUID
    client_name: str
    client_email: str
    financial_year: str
    status: str
    assigned_executive_name: Optional[str] = None
    initiated_at: datetime
    last_updated: datetime


class ExecutiveDistItem(BaseModel):
    executive_id: UUID
    executive_name: str
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    avg_days_to_complete: Optional[float] = None


# ─── Overall Summary ────────────────────────────────────────

class StageTiming(BaseModel):
    avg_days_document_processing: Optional[float] = None
    avg_days_computation: Optional[float] = None
    avg_days_filing: Optional[float] = None
    avg_days_payment: Optional[float] = None


class OverallSummary(BaseModel):
    total_clients: int = 0
    active_clients: int = 0
    total_executives: int = 0
    active_executives: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    filing_status_breakdown: list[StatusCount] = []
    avg_days_to_complete: Optional[float] = None
    avg_days_by_stage: StageTiming = StageTiming()


# ─── FY-Wise Summary ────────────────────────────────────────

class FYSummary(BaseModel):
    financial_year: str
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    filing_status_breakdown: list[StatusCount] = []
    avg_days_to_complete: Optional[float] = None
    clients: list[ClientFilingBrief] = []


# ─── Manager Distribution ───────────────────────────────────

class ManagerDistItem(BaseModel):
    manager_id: UUID
    manager_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    avg_days_to_complete: Optional[float] = None
    executives: list[ExecutiveDistItem] = []


# ─── Location Distribution ──────────────────────────────────

class LocationDistItem(BaseModel):
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    total_filings: int = 0
    active_filings: int = 0
    completed_filings: int = 0
    halted_filings: int = 0
    avg_days_to_complete: Optional[float] = None
    executives: list[ExecutiveDistItem] = []


# ─── Pending Report ─────────────────────────────────────────

class PendingFilingItem(BaseModel):
    filing_id: UUID
    client_id: UUID
    client_name: str
    client_email: str
    financial_year: str
    status: str
    assigned_executive_name: Optional[str] = None
    manager_name: Optional[str] = None
    location_tag: Optional[str] = None
    days_pending: float = 0
    initiated_at: datetime
    last_updated: datetime


# ─── Leaderboards ───────────────────────────────────────────

class ExecutiveLeaderboardItem(BaseModel):
    rank: int
    executive_id: UUID
    executive_name: str
    completed_filings: int = 0
    active_filings: int = 0
    total_filings: int = 0
    avg_days_to_complete: Optional[float] = None


class ManagerLeaderboardItem(BaseModel):
    rank: int
    manager_id: UUID
    manager_name: str
    executive_count: int = 0
    completed_filings: int = 0
    total_filings: int = 0
    avg_days_to_complete: Optional[float] = None


class LocationLeaderboardItem(BaseModel):
    rank: int
    tag_id: UUID
    location_name: str
    executive_count: int = 0
    completed_filings: int = 0
    total_filings: int = 0
    avg_days_to_complete: Optional[float] = None


# ─── Top-Level Response ─────────────────────────────────────

class ComprehensiveReportResponse(BaseModel):
    generated_at: datetime
    financial_years: list[str] = []
    filtered_fy: Optional[str] = None
    overall: OverallSummary
    fy_wise: list[FYSummary] = []
    manager_distribution: list[ManagerDistItem] = []
    location_distribution: list[LocationDistItem] = []
    pending_report: list[PendingFilingItem] = []
    leaderboard_executive: list[ExecutiveLeaderboardItem] = []
    leaderboard_manager: list[ManagerLeaderboardItem] = []
    leaderboard_location: list[LocationLeaderboardItem] = []
