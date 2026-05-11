"""Service — Notification creation and delivery."""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import NotificationChannel
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    message: str,
    channel: NotificationChannel = NotificationChannel.BOTH,
    related_filing_id: Optional[UUID] = None,
    related_client_id: Optional[UUID] = None,
) -> Notification:
    """Create an in-app notification and send email if configured."""
    from app.services.audit_service import _sanitize_ascii

    clean_title = _sanitize_ascii(title)
    clean_message = _sanitize_ascii(message)

    notification = Notification(
        user_id=user_id,
        title=clean_title,
        message=clean_message,
        channel=channel,
        related_filing_id=related_filing_id,
        related_client_id=related_client_id,
    )
    db.add(notification)
    await db.flush()

    # Send email if channel includes EMAIL
    if channel in (NotificationChannel.EMAIL, NotificationChannel.BOTH):
        try:
            # Look up user email
            user_result = await db.execute(select(User.email).where(User.id == user_id))
            user_email = user_result.scalar()

            if user_email:
                from app.services.email_service import send_notification_email
                from datetime import datetime

                sent = await send_notification_email(
                    to_email=user_email,
                    title=clean_title,
                    message=clean_message,
                    db=db,
                )

                if sent:
                    notification.email_sent = True
                    notification.email_sent_at = datetime.utcnow()
                    await db.flush()
        except Exception as e:
            # Email failure must never block the notification creation
            logger.warning(f"Email delivery failed for notification {notification.id}: {e}")

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
