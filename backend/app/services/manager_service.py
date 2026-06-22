"""Service — Manager management, team assignment, and scoped queries."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AccountStatus, AuditEventType, FilingStatus, UserRole
from app.core.cache import NS, bump_version, cached
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.manager_client_assignment import ManagerClientAssignment
from app.models.manager_executive_assignment import ManagerExecutiveAssignment
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification


async def create_manager(
    db: AsyncSession,
    email: str,
    full_name: str,
    password_hash: str,
    created_by: UUID,
) -> User:
    """Create a Manager account (Partner action)."""
    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    manager = User(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        role=UserRole.MANAGER,
        account_status=AccountStatus.ACTIVE,
        is_active=True,
    )
    db.add(manager)
    await db.flush()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MANAGER_CREATED,
        actor_id=created_by,
        details={"email": email, "full_name": full_name, "manager_id": str(manager.id)},
    )

    return manager


async def assign_executive_to_manager(
    db: AsyncSession,
    manager_id: UUID,
    executive_id: UUID,
    assigned_by: UUID,
) -> ManagerExecutiveAssignment:
    """Assign an Executive to a Manager's team."""
    # Validate executive exists
    exec_result = await db.execute(
        select(User).where(User.id == executive_id, User.role == UserRole.EXECUTIVE)
    )
    executive = exec_result.scalar_one_or_none()
    if not executive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found")

    # Validate manager exists
    mgr_result = await db.execute(
        select(User).where(User.id == manager_id, User.role == UserRole.MANAGER)
    )
    manager = mgr_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    # Check if executive already has an active manager
    existing_result = await db.execute(
        select(ManagerExecutiveAssignment).where(
            ManagerExecutiveAssignment.executive_id == executive_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    existing = existing_result.scalar_one_or_none()
    old_manager_id = None
    if existing:
        if existing.manager_id == manager_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Executive is already assigned to this manager",
            )
        # Deactivate previous assignment (one manager per executive)
        old_manager_id = existing.manager_id
        existing.is_active = False

    # Check if a deactivated row already exists for this pair (reactivate it)
    prev_result = await db.execute(
        select(ManagerExecutiveAssignment).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.executive_id == executive_id,
            ManagerExecutiveAssignment.is_active == False,
        )
    )
    prev = prev_result.scalar_one_or_none()
    if prev:
        prev.is_active = True
        prev.assigned_by = assigned_by
        from datetime import datetime, timezone
        prev.assigned_at = datetime.now(timezone.utc)
        assignment = prev
    else:
        # Create new assignment
        assignment = ManagerExecutiveAssignment(
            manager_id=manager_id,
            executive_id=executive_id,
            assigned_by=assigned_by,
        )
        db.add(assignment)

    await db.flush()

    # Transfer executive's clients to the new manager
    if old_manager_id is not None:
        await _transfer_executive_clients_to_manager(
            db=db,
            executive_id=executive_id,
            new_manager_id=manager_id,
            old_manager_id=old_manager_id,
            transferred_by=assigned_by,
        )

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MANAGER_EXECUTIVE_ASSIGNED,
        actor_id=assigned_by,
        details={
            "manager_id": str(manager_id),
            "executive_id": str(executive_id),
            "manager_name": manager.full_name,
            "executive_name": executive.full_name,
        },
    )

    # Invalidate scope caches — team composition changed
    await bump_version(NS.MANAGER_TEAM_EXECS)
    await bump_version(NS.MANAGER_CLIENTS)
    await bump_version(NS.EXEC_CLIENTS)
    await bump_version(NS.DASHBOARD_SUMMARY)

    return assignment


async def unassign_executive_from_manager(
    db: AsyncSession,
    manager_id: UUID,
    executive_id: UUID,
    removed_by: UUID,
) -> None:
    """Remove an Executive from a Manager's team."""
    result = await db.execute(
        select(ManagerExecutiveAssignment).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.executive_id == executive_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Executive is not assigned to this manager",
        )

    assignment.is_active = False
    await db.flush()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MANAGER_EXECUTIVE_UNASSIGNED,
        actor_id=removed_by,
        details={
            "manager_id": str(manager_id),
            "executive_id": str(executive_id),
        },
    )

    await bump_version(NS.MANAGER_TEAM_EXECS)
    await bump_version(NS.MANAGER_CLIENTS)
    await bump_version(NS.DASHBOARD_SUMMARY)


async def get_manager_team_executive_ids(
    db: AsyncSession,
    manager_id: UUID,
) -> list[UUID]:
    """Get all active executive IDs managed by a manager."""
    return await _get_manager_team_executive_ids_cached(db, manager_id)


@cached(NS.MANAGER_TEAM_EXECS, ttl=30, key_builder=lambda db, manager_id: str(manager_id))
async def _get_manager_team_executive_ids_cached(
    db: AsyncSession,
    manager_id: UUID,
) -> list[UUID]:
    result = await db.execute(
        select(ManagerExecutiveAssignment.executive_id).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    return [row[0] for row in result.all()]


async def get_manager_team_client_ids(
    db: AsyncSession,
    manager_id: UUID,
) -> list[UUID]:
    """Get all active client IDs directly assigned to a manager."""
    return await _get_manager_team_client_ids_cached(db, manager_id)


@cached(NS.MANAGER_CLIENTS, ttl=30, key_builder=lambda db, manager_id: str(manager_id))
async def _get_manager_team_client_ids_cached(
    db: AsyncSession,
    manager_id: UUID,
) -> list[UUID]:
    result = await db.execute(
        select(ManagerClientAssignment.client_id).where(
            ManagerClientAssignment.manager_id == manager_id,
            ManagerClientAssignment.is_active == True,
        )
    )
    return [row[0] for row in result.all()]


async def assign_client_to_manager(
    db: AsyncSession,
    manager_id: UUID,
    client_id: UUID,
    assigned_by: UUID,
) -> ManagerClientAssignment:
    """Assign a client to a manager (Partner action)."""
    # Validate manager
    mgr_result = await db.execute(
        select(User).where(User.id == manager_id, User.role == UserRole.MANAGER)
    )
    manager = mgr_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    # Validate client
    client_result = await db.execute(
        select(User).where(User.id == client_id, User.role == UserRole.CLIENT)
    )
    client = client_result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    # Check if client already has an active manager
    existing_result = await db.execute(
        select(ManagerClientAssignment).where(
            ManagerClientAssignment.client_id == client_id,
            ManagerClientAssignment.is_active == True,
        )
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        if existing.manager_id == manager_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Client is already assigned to this manager",
            )
        # Deactivate previous assignment (one manager per client)
        existing.is_active = False

    # Check if a deactivated row already exists for this pair (reactivate it)
    prev_result = await db.execute(
        select(ManagerClientAssignment).where(
            ManagerClientAssignment.manager_id == manager_id,
            ManagerClientAssignment.client_id == client_id,
            ManagerClientAssignment.is_active == False,
        )
    )
    prev = prev_result.scalar_one_or_none()
    if prev:
        prev.is_active = True
        prev.assigned_by = assigned_by
        from datetime import datetime, timezone
        prev.assigned_at = datetime.now(timezone.utc)
        assignment = prev
    else:
        assignment = ManagerClientAssignment(
            manager_id=manager_id,
            client_id=client_id,
            assigned_by=assigned_by,
        )
        db.add(assignment)

    await db.flush()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MANAGER_CLIENT_ASSIGNED,
        actor_id=assigned_by,
        details={
            "manager_id": str(manager_id),
            "client_id": str(client_id),
            "manager_name": manager.full_name,
            "client_name": client.full_name,
        },
    )

    # Notify manager
    await create_notification(
        db=db,
        user_id=manager_id,
        title="New Client Assigned",
        message=f"Client {client.full_name} has been assigned to you. You are now responsible for overseeing their filing progress.",
        client_name=client.full_name,
        action_url_path=f"/clients/{client_id}",
        cta_label="View Client",
    )

    await bump_version(NS.MANAGER_CLIENTS)
    await bump_version(NS.DASHBOARD_SUMMARY)

    return assignment


async def unassign_client_from_manager(
    db: AsyncSession,
    manager_id: UUID,
    client_id: UUID,
    removed_by: UUID,
) -> None:
    """Remove a client from a manager."""
    result = await db.execute(
        select(ManagerClientAssignment).where(
            ManagerClientAssignment.manager_id == manager_id,
            ManagerClientAssignment.client_id == client_id,
            ManagerClientAssignment.is_active == True,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client is not assigned to this manager",
        )

    assignment.is_active = False
    await db.flush()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MANAGER_CLIENT_UNASSIGNED,
        actor_id=removed_by,
        details={
            "manager_id": str(manager_id),
            "client_id": str(client_id),
        },
    )

    await bump_version(NS.MANAGER_CLIENTS)
    await bump_version(NS.DASHBOARD_SUMMARY)


async def get_client_manager_id(
    db: AsyncSession,
    client_id: UUID,
) -> UUID | None:
    """Get the manager ID for a client (or None if unassigned)."""
    result = await db.execute(
        select(ManagerClientAssignment.manager_id).where(
            ManagerClientAssignment.client_id == client_id,
            ManagerClientAssignment.is_active == True,
        )
    )
    row = result.first()
    return row[0] if row else None


async def ensure_manager_client_link(
    db: AsyncSession,
    manager_id: UUID,
    client_id: UUID,
    assigned_by: UUID,
) -> None:
    """Ensure a manager-client link exists (create if missing). Used for auto-linking."""
    existing = await db.execute(
        select(ManagerClientAssignment).where(
            ManagerClientAssignment.manager_id == manager_id,
            ManagerClientAssignment.client_id == client_id,
        )
    )
    existing_row = existing.scalar_one_or_none()
    if existing_row:
        if existing_row.is_active:
            return  # Already linked
        # Reactivate the existing inactive row
        existing_row.is_active = True
        existing_row.assigned_by = assigned_by
    else:
        # Deactivate any other active manager for this client
        other = await db.execute(
            select(ManagerClientAssignment).where(
                ManagerClientAssignment.client_id == client_id,
                ManagerClientAssignment.is_active == True,
            )
        )
        for row in other.scalars().all():
            row.is_active = False

        assignment = ManagerClientAssignment(
            manager_id=manager_id,
            client_id=client_id,
            assigned_by=assigned_by,
        )
        db.add(assignment)

    await db.flush()


async def _transfer_executive_clients_to_manager(
    db: AsyncSession,
    executive_id: UUID,
    new_manager_id: UUID,
    old_manager_id: UUID,
    transferred_by: UUID,
) -> None:
    """Transfer all active clients of an executive to the new manager.

    When an executive is moved from one manager to another, all clients
    handled by that executive must have their ManagerClientAssignment
    updated to point to the new manager for data consistency.
    """
    from datetime import datetime, timezone
    import logging
    logger = logging.getLogger("app")

    # Get all active clients of this executive
    client_result = await db.execute(
        select(ExecutiveClientAssignment.client_id).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    client_ids = [row[0] for row in client_result.all()]

    if not client_ids:
        return

    transferred_count = 0
    for client_id in client_ids:
        # Deactivate old manager-client link (if it was under the old manager)
        old_link_result = await db.execute(
            select(ManagerClientAssignment).where(
                ManagerClientAssignment.client_id == client_id,
                ManagerClientAssignment.manager_id == old_manager_id,
                ManagerClientAssignment.is_active == True,
            )
        )
        old_link = old_link_result.scalar_one_or_none()
        if old_link:
            old_link.is_active = False

        # Check if new manager already has an active link to this client
        new_link_result = await db.execute(
            select(ManagerClientAssignment).where(
                ManagerClientAssignment.client_id == client_id,
                ManagerClientAssignment.manager_id == new_manager_id,
            )
        )
        new_link = new_link_result.scalar_one_or_none()

        if new_link:
            # Reactivate existing deactivated row
            if not new_link.is_active:
                new_link.is_active = True
                new_link.assigned_by = transferred_by
                new_link.assigned_at = datetime.now(timezone.utc)
                transferred_count += 1
        else:
            # Create new manager-client assignment
            new_assignment = ManagerClientAssignment(
                manager_id=new_manager_id,
                client_id=client_id,
                assigned_by=transferred_by,
            )
            db.add(new_assignment)
            transferred_count += 1

    await db.flush()

    if transferred_count > 0:
        logger.info(
            f"Transferred {transferred_count} client(s) from manager {old_manager_id} "
            f"to manager {new_manager_id} (executive {executive_id} reassignment)"
        )
        await record_audit_event(
            db=db,
            event_type=AuditEventType.MANAGER_EXECUTIVE_ASSIGNED,
            actor_id=transferred_by,
            details={
                "action": "client_transfer_on_executive_reassignment",
                "executive_id": str(executive_id),
                "old_manager_id": str(old_manager_id),
                "new_manager_id": str(new_manager_id),
                "clients_transferred": transferred_count,
                "client_ids": [str(c) for c in client_ids[:20]],  # cap for large lists
            },
        )


async def toggle_manager_elevation(
    db: AsyncSession,
    manager_id: UUID,
    elevate: bool,
    toggled_by: UUID,
) -> User:
    """Set or clear is_elevated on a Manager. Partner-only action."""
    result = await db.execute(
        select(User).where(User.id == manager_id, User.role == UserRole.MANAGER)
    )
    manager = result.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    if manager.is_elevated == elevate:
        label = "elevated" if elevate else "de-elevated"
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Manager is already {label}")

    manager.is_elevated = elevate
    await db.flush()

    event_type = AuditEventType.MANAGER_ELEVATED if elevate else AuditEventType.MANAGER_DE_ELEVATED
    await record_audit_event(
        db=db,
        event_type=event_type,
        actor_id=toggled_by,
        details={
            "manager_id": str(manager_id),
            "manager_name": manager.full_name,
            "elevated": elevate,
        },
    )

    await bump_version(NS.CLIENT_LIST)
    await bump_version(NS.MANAGER_CLIENTS)
    return manager
