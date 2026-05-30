"""Schemas — Filing feedback (star rating)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FeedbackSubmitRequest(BaseModel):
    filing_id: UUID
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 (worst) to 5 (best)")


class FeedbackResponse(BaseModel):
    id: UUID
    filing_id: UUID
    client_id: UUID
    client_name: Optional[str] = None
    financial_year: Optional[str] = None
    rating: int
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedbackSummaryResponse(BaseModel):
    total_feedbacks: int
    average_rating: Optional[float] = None
    rating_breakdown: dict[int, int]  # {1: count, 2: count, ...5: count}
    feedbacks: list[FeedbackResponse]
