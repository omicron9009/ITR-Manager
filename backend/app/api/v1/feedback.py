"""API v1 — Feedback endpoints (client rating for completed filings)."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_client, get_current_partner
from app.database import get_db
from app.enums import FilingStatus
from app.models.filing import ITRFiling
from app.models.filing_feedback import FilingFeedback
from app.models.user import User
from app.schemas.feedback import FeedbackResponse, FeedbackSubmitRequest, FeedbackSummaryResponse

router = APIRouter()


# ─── POST /feedback ─────────────────────────────────────────
@router.post("", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    body: FeedbackSubmitRequest,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client submits a star rating (1–5) for a completed filing."""
    # Verify filing exists and belongs to this client
    filing_result = await db.execute(
        select(ITRFiling).where(ITRFiling.id == body.filing_id)
    )
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    # Filing must be COMPLETED
    if filing.status != FilingStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback can only be submitted for completed filings",
        )

    # Check for duplicate
    existing_result = await db.execute(
        select(FilingFeedback).where(FilingFeedback.filing_id == body.filing_id)
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback already submitted for this filing",
        )

    feedback = FilingFeedback(
        filing_id=body.filing_id,
        client_id=current_user.id,
        rating=body.rating,
    )
    db.add(feedback)
    await db.flush()

    return FeedbackResponse(
        id=feedback.id,
        filing_id=feedback.filing_id,
        client_id=feedback.client_id,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        rating=feedback.rating,
        created_at=feedback.created_at,
    )


# ─── GET /feedback/filing/{filing_id} ───────────────────────
@router.get("/filing/{filing_id}", response_model=FeedbackResponse)
async def get_filing_feedback(
    filing_id: UUID,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client checks if they have submitted feedback for a filing."""
    # Verify filing belongs to this client
    filing_result = await db.execute(
        select(ITRFiling).where(ITRFiling.id == filing_id)
    )
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    feedback_result = await db.execute(
        select(FilingFeedback).where(FilingFeedback.filing_id == filing_id)
    )
    feedback = feedback_result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No feedback submitted yet")

    return FeedbackResponse(
        id=feedback.id,
        filing_id=feedback.filing_id,
        client_id=feedback.client_id,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        rating=feedback.rating,
        created_at=feedback.created_at,
    )


# ─── GET /feedback/summary (Partner Only) ───────────────────
@router.get("/summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Partner views all feedback with average and breakdown."""
    # All feedbacks with client and filing info
    result = await db.execute(
        select(FilingFeedback).order_by(FilingFeedback.created_at.desc())
    )
    feedbacks = result.scalars().all()

    items = []
    for fb in feedbacks:
        # Get client name
        client_result = await db.execute(select(User.full_name).where(User.id == fb.client_id))
        client_name = client_result.scalar()

        # Get financial year
        filing_result = await db.execute(select(ITRFiling.financial_year).where(ITRFiling.id == fb.filing_id))
        fy = filing_result.scalar()

        items.append(FeedbackResponse(
            id=fb.id,
            filing_id=fb.filing_id,
            client_id=fb.client_id,
            client_name=client_name,
            financial_year=fy,
            rating=fb.rating,
            created_at=fb.created_at,
        ))

    # Average
    avg_result = await db.execute(select(func.avg(FilingFeedback.rating)))
    avg_rating = avg_result.scalar()

    # Breakdown per star
    breakdown_result = await db.execute(
        select(FilingFeedback.rating, func.count(FilingFeedback.id))
        .group_by(FilingFeedback.rating)
    )
    rating_breakdown = {i: 0 for i in range(1, 6)}
    for row in breakdown_result.all():
        rating_breakdown[row[0]] = row[1]

    return FeedbackSummaryResponse(
        total_feedbacks=len(items),
        average_rating=round(float(avg_rating), 2) if avg_rating else None,
        rating_breakdown=rating_breakdown,
        feedbacks=items,
    )
