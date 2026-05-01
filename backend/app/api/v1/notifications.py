"""API v1 — Notification endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.notification import NotificationListResponse, NotificationMarkReadRequest, NotificationResponse
from app.services.notification_service import (
    get_user_notifications,
    mark_all_notifications_read,
    mark_notifications_read,
)

router = APIRouter()


# ─── GET /notifications ─────────────────────────────────────
@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get notifications for the current user."""
    items, total, unread_count = await get_user_notifications(
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
    )

    return NotificationListResponse(
        items=[NotificationResponse.model_validate(n) for n in items],
        total=total,
        unread_count=unread_count,
    )


# ─── GET /notifications/unread-count ────────────────────────
@router.get("/unread-count", response_model=dict)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the unread notification count (for bell icon badge)."""
    _, _, unread_count = await get_user_notifications(
        db=db,
        user_id=current_user.id,
        page=1,
        page_size=1,
    )
    return {"unread_count": unread_count}


# ─── POST /notifications/mark-read ──────────────────────────
@router.post("/mark-read", response_model=dict)
async def mark_read(
    body: NotificationMarkReadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark specific notifications as read."""
    count = await mark_notifications_read(db, current_user.id, body.notification_ids)
    return {"marked_read": count}


# ─── POST /notifications/mark-all-read ──────────────────────
@router.post("/mark-all-read", response_model=dict)
async def mark_all_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read for the current user."""
    count = await mark_all_notifications_read(db, current_user.id)
    return {"marked_read": count}
