"""Schemas — Filing lifecycle."""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.enums import FilingStatus


# ─── Filing Initiation ──────────────────────────────────────
class FilingInitiateRequest(BaseModel):
    financial_year: str = Field(..., pattern=r"^\d{4}-\d{4}$")
    engagement_accepted: bool = Field(..., description="Must be true to accept the engagement letter")

    @field_validator("engagement_accepted")
    @classmethod
    def validate_engagement(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the Engagement Letter to initiate filing")
        return v


class FilingResponse(BaseModel):
    id: UUID
    client_id: UUID
    client_name: Optional[str] = None
    financial_year: str
    status: FilingStatus
    assigned_executive_id: Optional[UUID] = None
    assigned_executive_name: Optional[str] = None
    initiated_at: datetime
    onboarding_completed_at: Optional[datetime] = None
    documents_submitted_at: Optional[datetime] = None
    documents_approved_at: Optional[datetime] = None
    computation_uploaded_at: Optional[datetime] = None
    computation_approved_at: Optional[datetime] = None
    is_tax_paid: bool = False
    tax_paid_at: Optional[datetime] = None
    professional_fee: Optional[Decimal] = None
    no_fees_applicable: bool = False
    proposed_fee: Optional[Decimal] = None
    fee_proposed_at: Optional[datetime] = None
    engagement_accepted_at: Optional[datetime] = None
    filed_at: Optional[datetime] = None
    payment_received_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    halted_at: Optional[datetime] = None
    halt_reason: Optional[str] = None
    has_internal_workings: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FilingListResponse(BaseModel):
    items: list[FilingResponse]
    total: int


# ─── State Transition ───────────────────────────────────────
class FilingStateChangeRequest(BaseModel):
    to_status: FilingStatus
    remarks: Optional[str] = None


class FilingHaltRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


# ─── Confirm Income Heads (Client) ──────────────────────────
class ConfirmIncomeHeadsRequest(BaseModel):
    """Body for POST /filings/{filing_id}/confirm-income-heads.

    All booleans default False if omitted. Updates the client's master
    `ClientIncomeHeads` row AND snapshots the values onto the filing.
    """
    salary: bool = False
    esop: bool = False
    rental_income: bool = False
    more_than_2_properties: bool = False
    capital_gain_shares: bool = False
    capital_gain_land: bool = False
    business_profession: bool = False
    interest_dividend: bool = False
    foreign_assets: bool = False
    any_other: bool = False
    any_other_text: Optional[str] = Field(None, max_length=255)


class ConfirmIncomeHeadsResponse(BaseModel):
    filing_id: UUID
    income_heads_snapshot: dict
    income_heads_confirmed_at: datetime
    base_documents_assigned: int
    transitioned_to: Optional[FilingStatus] = None


# ─── State History ──────────────────────────────────────────
class FilingStateHistoryItem(BaseModel):
    id: UUID
    from_status: Optional[FilingStatus] = None
    to_status: FilingStatus
    changed_by: UUID
    changed_by_name: Optional[str] = None
    changed_at: datetime
    remarks: Optional[str] = None

    model_config = {"from_attributes": True}


# ─── Filing Tracking (Client View) ──────────────────────────
class FilingTrackingItem(BaseModel):
    id: UUID
    financial_year: str
    status: FilingStatus
    initiated_at: datetime
    completed_at: Optional[datetime] = None
    progress_percentage: int = 0

    model_config = {"from_attributes": True}


class FilingTrackingResponse(BaseModel):
    items: list[FilingTrackingItem]
