"""Service — Notification creation and delivery."""

import asyncio
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import NotificationChannel
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger(__name__)

# Keep references to fire-and-forget email tasks to prevent GC
_email_tasks: set = set()


async def _deliver_email_for_notification(
    notification_id: UUID,
    user_email: str,
    title: str,
    message: str,
    client_name: Optional[str] = None,
    financial_year: Optional[str] = None,
    filing_status: Optional[str] = None,
    action_by: Optional[str] = None,
    action_url_path: Optional[str] = None,
    cta_label: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> None:
    """Fire-and-forget: send email using an independent DB session."""
    from datetime import datetime

    from app.database import AsyncSessionLocal
    from app.services.email_service import send_notification_email

    try:
        async with AsyncSessionLocal() as db:
            sent = await send_notification_email(
                to_email=user_email,
                title=title,
                message=message,
                db=db,
                client_name=client_name,
                financial_year=financial_year,
                filing_status=filing_status,
                action_by=action_by,
                action_url_path=action_url_path,
                cta_label=cta_label,
                extra_details=extra_details,
            )
            if sent:
                await db.execute(
                    update(Notification)
                    .where(Notification.id == notification_id)
                    .values(email_sent=True, email_sent_at=datetime.utcnow())
                )
                await db.commit()
    except Exception as e:
        logger.warning(f"Background email delivery failed for notification {notification_id}: {e}")


async def create_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    message: str,
    channel: NotificationChannel = NotificationChannel.BOTH,
    related_filing_id: Optional[UUID] = None,
    related_client_id: Optional[UUID] = None,
    # Rich email context (optional — backwards compatible)
    client_name: Optional[str] = None,
    financial_year: Optional[str] = None,
    filing_status: Optional[str] = None,
    action_by: Optional[str] = None,
    action_url_path: Optional[str] = None,
    cta_label: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> Notification:
    """Create an in-app notification and dispatch email in the background.

    Email delivery runs as a fire-and-forget async task with its own DB
    session, so it never delays the caller or blocks the event loop.
    """
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

    # Send email if channel includes EMAIL — fire-and-forget via background task
    if channel in (NotificationChannel.EMAIL, NotificationChannel.BOTH):
        try:
            # Look up user email
            user_result = await db.execute(select(User.email).where(User.id == user_id))
            user_email = user_result.scalar()

            if user_email:
                task = asyncio.create_task(
                    _deliver_email_for_notification(
                        notification_id=notification.id,
                        user_email=user_email,
                        title=clean_title,
                        message=clean_message,
                        client_name=client_name,
                        financial_year=financial_year,
                        filing_status=filing_status,
                        action_by=action_by,
                        action_url_path=action_url_path,
                        cta_label=cta_label,
                        extra_details=extra_details,
                    )
                )
                # Prevent task from being garbage-collected; auto-discard on completion
                _email_tasks.add(task)
                task.add_done_callback(_email_tasks.discard)
        except Exception as e:
            # Email failure must never block the notification creation
            logger.warning(f"Email task dispatch failed for notification {notification.id}: {e}")

    return notification


async def notify_partner_and_manager(
    db: AsyncSession,
    *,
    client_id: UUID,
    title: str,
    message: str,
    channel: NotificationChannel = NotificationChannel.BOTH,
    related_filing_id: Optional[UUID] = None,
    related_client_id: Optional[UUID] = None,
    client_name: Optional[str] = None,
    financial_year: Optional[str] = None,
    filing_status: Optional[str] = None,
    action_by: Optional[str] = None,
    action_url_path: Optional[str] = None,
    cta_label: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> None:
    """Send a notification to both Partner AND the Manager responsible for the client.

    Manager is resolved via: ExecutiveClientAssignment (client → executive) →
    ManagerExecutiveAssignment (executive → manager).

    If the client has no manager (executive has no manager assigned), the
    notification is skipped for the manager role — Partner still receives it.
    If there is no Partner user, Partner notification is skipped silently.
    """
    from app.enums import UserRole
    from app.models.executive_assignment import ExecutiveClientAssignment
    from app.models.manager_executive_assignment import ManagerExecutiveAssignment

    common_kwargs = dict(
        channel=channel,
        related_filing_id=related_filing_id,
        related_client_id=related_client_id,
        client_name=client_name,
        financial_year=financial_year,
        filing_status=filing_status,
        action_by=action_by,
        action_url_path=action_url_path,
        cta_label=cta_label,
        extra_details=extra_details,
    )

    # Prefix client name to title for staff-facing notifications
    staff_title = f"{client_name} — {title}" if client_name else title

    # ── Partner ──────────────────────────────────────────────
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(db=db, user_id=partner.id, title=staff_title, message=message, **common_kwargs)

    # ── Manager (via executive assignment chain) ─────────────
    exec_assign_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    exec_assign = exec_assign_result.scalar_one_or_none()
    if exec_assign:
        mgr_assign_result = await db.execute(
            select(ManagerExecutiveAssignment).where(
                ManagerExecutiveAssignment.executive_id == exec_assign.executive_id,
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        mgr_assign = mgr_assign_result.scalar_one_or_none()
        if mgr_assign:
            await create_notification(
                db=db, user_id=mgr_assign.manager_id, title=staff_title, message=message, **common_kwargs
            )


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
