"""Service — Notification creation and delivery."""

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import NotificationChannel
from app.models.notification import Notification


async def create_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    message: str,
    channel: NotificationChannel = NotificationChannel.BOTH,
    related_filing_id: Optional[UUID] = None,
    related_client_id: Optional[UUID] = None,
) -> Notification:
    """Create an in-app notification for a user."""
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        channel=channel,
        related_filing_id=related_filing_id,
        related_client_id=related_client_id,
    )
    db.add(notification)
    await db.flush()
    return notification


async def get_user_notifications(
    db: AsyncSession,
    user_id: UUID,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
) -> tuple[list[Notification], int, int]:
    """Get notifications for a user with pagination. Returns (items, total, unread_count)."""
    query = select(Notification).where(Notification.user_id == user_id)

    if unread_only:
        query = query.where(Notification.is_read == False)

    # Total count
    count_query = select(func.count()).select_from(
        select(Notification.id).where(Notification.user_id == user_id).subquery()
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Unread count
    unread_query = select(func.count()).select_from(
        select(Notification.id).where(
            Notification.user_id == user_id, Notification.is_read == False
        ).subquery()
    )
    unread_result = await db.execute(unread_query)
    unread_count = unread_result.scalar() or 0

    # Paginated items
    query = query.order_by(Notification.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return items, total, unread_count


async def mark_notifications_read(
    db: AsyncSession,
    user_id: UUID,
    notification_ids: list[UUID],
) -> int:
    """Mark specific notifications as read. Returns count updated."""
    stmt = (
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.id.in_(notification_ids),
            Notification.is_read == False,
        )
        .values(is_read=True)
    )
    result = await db.execute(stmt)
    return result.rowcount


async def mark_all_notifications_read(db: AsyncSession, user_id: UUID) -> int:
    """Mark all notifications as read for a user."""
    stmt = (
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)
        .values(is_read=True)
    )
    result = await db.execute(stmt)
    return result.rowcount
