"""Service — Executive management and assignment."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AccountStatus, AuditEventType, UserRole
from app.core.cache import NS, bump_version, cached
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification


async def create_executive(
    db: AsyncSession,
    email: str,
    full_name: str,
    password_hash: str,
    created_by: UUID,
) -> User:
    """Create an Executive account (Partner action)."""
    executive = User(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        role=UserRole.EXECUTIVE,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(executive)
    await db.flush()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.EXECUTIVE_CREATED,
        actor_id=created_by,
        details={"email": email, "full_name": full_name, "executive_id": str(executive.id)},
    )

    return executive


async def deactivate_executive(db: AsyncSession, executive_id: UUID, deactivated_by: UUID) -> User:
    """Deactivate an Executive account."""
    result = await db.execute(select(User).where(User.id == executive_id, User.role == UserRole.EXECUTIVE))
    executive = result.scalar_one_or_none()
    if not executive:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found")

    executive.is_active = False
    executive.account_status = AccountStatus.DEACTIVATED

    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_DEACTIVATED,
        actor_id=deactivated_by,
        details={"executive_id": str(executive_id)},
    )

    await db.flush()
    # Invalidate auth-user cache so deactivation takes effect immediately
    await bump_version(NS.USER_BY_ID)
    return executive


async def reactivate_executive(db: AsyncSession, executive_id: UUID, reactivated_by: UUID) -> User:
    """Reactivate a deactivated Executive account."""
    result = await db.execute(select(User).where(User.id == executive_id, User.role == UserRole.EXECUTIVE))
    executive = result.scalar_one_or_none()
    if not executive:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found")

    executive.is_active = True
    executive.account_status = AccountStatus.ACTIVE

    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_REACTIVATED,
        actor_id=reactivated_by,
        details={"executive_id": str(executive_id)},
    )

    await db.flush()
    await bump_version(NS.USER_BY_ID)
    return executive


async def assign_executive_to_client(
    db: AsyncSession,
    executive_id: UUID,
    client_id: UUID,
    assigned_by: UUID,
) -> ExecutiveClientAssignment:
    """Assign an Executive to a Client. Deactivates previous assignment if any."""
    # Deactivate existing active assignment
    existing = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    current = existing.scalar_one_or_none()
    if current:
        current.is_active = False
        current.unassigned_at = datetime.now(timezone.utc)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.EXECUTIVE_UNASSIGNED,
            actor_id=assigned_by,
            client_id=client_id,
            details={"previous_executive_id": str(current.executive_id)},
        )

    # Create new assignment
    assignment = ExecutiveClientAssignment(
        executive_id=executive_id,
        client_id=client_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)

    # Also update the assigned_executive_id on active filings
    filings_result = await db.execute(
        select(ITRFiling).where(
            ITRFiling.client_id == client_id,
            ITRFiling.status.notin_(["COMPLETED", "HALTED"]),
        )
    )
    for filing in filings_result.scalars().all():
        filing.assigned_executive_id = executive_id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.EXECUTIVE_ASSIGNED,
        actor_id=assigned_by,
        client_id=client_id,
        details={"executive_id": str(executive_id)},
    )

    await db.flush()
    # Invalidate exec→client and partner-client-list caches
    await bump_version(NS.EXEC_CLIENTS)
    await bump_version(NS.PARTNER_CLIENT_IDS)
    await bump_version(NS.DASHBOARD_SUMMARY)
    await bump_version(NS.CLIENT_LIST)
    return assignment


@cached(NS.EXEC_CLIENTS, ttl=30, key_builder=lambda db, executive_id: str(executive_id))
async def get_executive_client_ids(
    db: AsyncSession,
    executive_id: UUID,
) -> list[UUID]:
    """Return active client IDs assigned to an executive (cached 30s)."""
    result = await db.execute(
        select(ExecutiveClientAssignment.client_id).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    return [row[0] for row in result.all()]
