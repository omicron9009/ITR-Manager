"""API v1 — Manager management endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_manager_executive_access
from app.core.security import get_current_manager_or_partner, get_current_partner, get_current_partner_or_elevated_manager, get_current_user, hash_password
from app.database import get_db
from app.enums import FilingStatus, UserRole
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.manager_client_assignment import ManagerClientAssignment
from app.models.manager_executive_assignment import ManagerExecutiveAssignment
from app.models.user import User
from app.schemas.executive import ExecutiveAssignRequest, ExecutiveAssignmentResponse
from app.schemas.manager import (
    ManagerAssignClientRequest,
    ManagerAssignExecutiveRequest,
    ManagerAssignTagRequest,
    ManagerClientAssignmentResponse,
    ManagerClientListItem,
    ManagerClientListResponse,
    ManagerCreateRequest,
    ManagerExecutiveAssignmentResponse,
    ManagerListResponse,
    ManagerResponse,
    ManagerTeamExecutiveItem,
    ManagerTeamResponse,
)
from app.services.executive_service import assign_executive_to_client
from app.services.manager_service import (
    assign_client_to_manager,
    assign_executive_to_manager,
    create_manager,
    get_manager_team_client_ids,
    get_manager_team_executive_ids,
    toggle_manager_elevation,
    unassign_client_from_manager,
    unassign_executive_from_manager,
)

router = APIRouter()


# ─── POST /managers ─────────────────────────────────────────
@router.post("", response_model=ManagerResponse, status_code=201)
async def create_new_manager(
    body: ManagerCreateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Manager account (Partner only)."""
    manager = await create_manager(
        db=db,
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        created_by=current_user.id,
    )
    return ManagerResponse(
        id=manager.id,
        email=manager.email,
        full_name=manager.full_name,
        account_status=manager.account_status.value,
        is_active=manager.is_active,
        is_elevated=manager.is_elevated,
        team_executive_count=0,
        team_client_count=0,
        created_at=manager.created_at,
    )


# ─── GET /managers ──────────────────────────────────────────
@router.get("", response_model=ManagerListResponse)
async def list_managers(
    current_user: User = Depends(get_current_partner_or_elevated_manager),
    db: AsyncSession = Depends(get_db),
):
    """List all managers with team info (Partner or Elevated Manager)."""
    result = await db.execute(
        select(User).where(User.role == UserRole.MANAGER).order_by(User.full_name)
    )
    managers = result.scalars().all()

    items = []
    for mgr in managers:
        # Count team executives
        exec_count_result = await db.execute(
            select(func.count()).select_from(ManagerExecutiveAssignment).where(
                ManagerExecutiveAssignment.manager_id == mgr.id,
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        exec_count = exec_count_result.scalar() or 0

        # Count assigned clients (from manager_client_assignments)
        client_count_result = await db.execute(
            select(func.count()).select_from(ManagerClientAssignment).where(
                ManagerClientAssignment.manager_id == mgr.id,
                ManagerClientAssignment.is_active == True,
            )
        )
        client_count = client_count_result.scalar() or 0

        items.append(
            ManagerResponse(
                id=mgr.id,
                email=mgr.email,
                full_name=mgr.full_name,
                account_status=mgr.account_status.value,
                is_active=mgr.is_active,
                is_elevated=mgr.is_elevated,
                team_executive_count=exec_count,
                team_client_count=client_count,
                created_at=mgr.created_at,
            )
        )

    return ManagerListResponse(items=items, total=len(items))


# ─── GET /managers/me/team ──────────────────────────────────
@router.get("/me/team", response_model=ManagerTeamResponse)
async def get_my_team(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manager views their own team."""
    if current_user.role != UserRole.MANAGER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required")

    # Reuse the team endpoint logic
    return await get_manager_team(current_user.id, current_user, db)


# ─── GET /managers/{manager_id}/team ────────────────────────
@router.get("/{manager_id}/team", response_model=ManagerTeamResponse)
async def get_manager_team(
    manager_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a manager's team of executives."""
    # Managers can only view their own team; Partner or elevated manager can view any
    if current_user.role == UserRole.MANAGER and current_user.id != manager_id and not getattr(current_user, "is_elevated", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only view your own team")

    mgr_result = await db.execute(select(User).where(User.id == manager_id, User.role == UserRole.MANAGER))
    manager = mgr_result.scalar_one_or_none()
    if not manager:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    # Get team executives
    assignments_result = await db.execute(
        select(ManagerExecutiveAssignment).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    assignments = assignments_result.scalars().all()

    executives = []
    for assignment in assignments:
        exec_result = await db.execute(select(User).where(User.id == assignment.executive_id))
        exec_user = exec_result.scalar_one_or_none()
        if not exec_user:
            continue

        # Count assigned clients
        client_count_result = await db.execute(
            select(func.count()).select_from(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == exec_user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        client_count = client_count_result.scalar() or 0

        # Count active filings
        filing_count_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == exec_user.id,
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            )
        )
        filing_count = filing_count_result.scalar() or 0

        executives.append(
            ManagerTeamExecutiveItem(
                id=exec_user.id,
                email=exec_user.email,
                full_name=exec_user.full_name,
                account_status=exec_user.account_status.value,
                is_active=exec_user.is_active,
                assigned_client_count=client_count,
                active_filing_count=filing_count,
            )
        )

    return ManagerTeamResponse(
        manager_id=manager.id,
        manager_name=manager.full_name,
        executives=executives,
    )


# ─── POST /managers/{manager_id}/assign-executive ───────────
@router.post("/{manager_id}/assign-executive", response_model=ManagerExecutiveAssignmentResponse, status_code=201)
async def assign_executive(
    manager_id: UUID,
    body: ManagerAssignExecutiveRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign an executive to a manager's team (Partner or the Manager themselves)."""
    # Managers can only assign to their own team
    if current_user.role == UserRole.MANAGER and current_user.id != manager_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only assign to your own team")

    assignment = await assign_executive_to_manager(
        db=db,
        manager_id=manager_id,
        executive_id=body.executive_id,
        assigned_by=current_user.id,
    )

    # Fetch names for response
    mgr_result = await db.execute(select(User).where(User.id == manager_id))
    manager = mgr_result.scalar_one()
    exec_result = await db.execute(select(User).where(User.id == body.executive_id))
    executive = exec_result.scalar_one()

    return ManagerExecutiveAssignmentResponse(
        id=assignment.id,
        manager_id=assignment.manager_id,
        manager_name=manager.full_name,
        executive_id=assignment.executive_id,
        executive_name=executive.full_name,
        assigned_at=assignment.assigned_at,
        is_active=assignment.is_active,
    )


# ─── DELETE /managers/{manager_id}/executives/{executive_id} ─
@router.delete("/{manager_id}/executives/{executive_id}", status_code=204)
async def remove_executive_from_team(
    manager_id: UUID,
    executive_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Remove an executive from a manager's team."""
    if current_user.role == UserRole.MANAGER and current_user.id != manager_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only manage your own team")

    await unassign_executive_from_manager(
        db=db,
        manager_id=manager_id,
        executive_id=executive_id,
        removed_by=current_user.id,
    )


# ─── POST /managers/{manager_id}/assign-client ──────────────
@router.post("/{manager_id}/assign-client", response_model=ExecutiveAssignmentResponse, status_code=201)
async def manager_assign_client_to_executive(
    manager_id: UUID,
    body: ExecutiveAssignRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Manager assigns one of their clients to one of their executives."""
    # Validate manager scope
    if current_user.role == UserRole.MANAGER:
        is_elevated = getattr(current_user, "is_elevated", False)
        if current_user.id != manager_id and not is_elevated:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only assign within your team")

        # Ensure the executive belongs to this manager's team (elevated can use any executive)
        if not is_elevated:
            has_access = await check_manager_executive_access(db, manager_id, body.executive_id)
            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This executive is not on your team",
                )

        # Ensure the client belongs to this manager (elevated can assign any client)
        if not is_elevated:
            client_ids = await get_manager_team_client_ids(db, manager_id)
            if body.client_id not in client_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This client is not assigned to you",
                )

    assignment = await assign_executive_to_client(
        db=db,
        executive_id=body.executive_id,
        client_id=body.client_id,
        assigned_by=current_user.id,
    )

    # Fetch names
    exec_result = await db.execute(select(User).where(User.id == body.executive_id))
    executive = exec_result.scalar_one()
    client_result = await db.execute(select(User).where(User.id == body.client_id))
    client = client_result.scalar_one()

    return ExecutiveAssignmentResponse(
        id=assignment.id,
        executive_id=assignment.executive_id,
        executive_name=executive.full_name,
        client_id=assignment.client_id,
        client_name=client.full_name,
        assigned_at=assignment.assigned_at,
        is_active=assignment.is_active,
    )


# ─── POST /managers/{manager_id}/clients ────────────────────
@router.post("/{manager_id}/clients", response_model=ManagerClientAssignmentResponse, status_code=201)
async def assign_client_to_mgr(
    manager_id: UUID,
    body: ManagerAssignClientRequest,
    current_user: User = Depends(get_current_partner_or_elevated_manager),
    db: AsyncSession = Depends(get_db),
):
    """Assign a client to a manager (Partner or Elevated Manager)."""
    assignment = await assign_client_to_manager(
        db=db,
        manager_id=manager_id,
        client_id=body.client_id,
        assigned_by=current_user.id,
    )

    # Fetch names
    mgr_result = await db.execute(select(User).where(User.id == manager_id))
    manager = mgr_result.scalar_one()
    client_result = await db.execute(select(User).where(User.id == body.client_id))
    client = client_result.scalar_one()

    return ManagerClientAssignmentResponse(
        id=assignment.id,
        manager_id=assignment.manager_id,
        manager_name=manager.full_name,
        client_id=assignment.client_id,
        client_name=client.full_name,
        assigned_at=assignment.assigned_at,
        is_active=assignment.is_active,
    )


# ─── DELETE /managers/{manager_id}/clients/{client_id} ───────
@router.delete("/{manager_id}/clients/{client_id}", status_code=204)
async def remove_client_from_manager(
    manager_id: UUID,
    client_id: UUID,
    current_user: User = Depends(get_current_partner_or_elevated_manager),
    db: AsyncSession = Depends(get_db),
):
    """Unassign a client from a manager (Partner or Elevated Manager)."""
    await unassign_client_from_manager(
        db=db,
        manager_id=manager_id,
        client_id=client_id,
        removed_by=current_user.id,
    )


# ─── GET /managers/me/clients ───────────────────────────────
@router.get("/me/clients", response_model=ManagerClientListResponse)
async def get_my_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manager views their assigned clients."""
    if current_user.role != UserRole.MANAGER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager access required")

    return await _get_manager_clients(db, current_user.id, page, page_size, search, is_elevated=getattr(current_user, "is_elevated", False))


# ─── GET /managers/{manager_id}/clients ─────────────────────
@router.get("/{manager_id}/clients", response_model=ManagerClientListResponse)
async def get_manager_clients(
    manager_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a manager's clients (Manager own / Partner any)."""
    if current_user.role == UserRole.MANAGER and current_user.id != manager_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only view your own clients")

    # Validate manager exists
    mgr_result = await db.execute(select(User).where(User.id == manager_id, User.role == UserRole.MANAGER))
    target_mgr = mgr_result.scalar_one_or_none()
    if not target_mgr:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    return await _get_manager_clients(db, manager_id, page, page_size, search, is_elevated=getattr(target_mgr, "is_elevated", False))


async def _get_manager_clients(
    db: AsyncSession,
    manager_id: UUID,
    page: int,
    page_size: int,
    search: Optional[str],
    is_elevated: bool = False,
) -> ManagerClientListResponse:
    """Internal helper: fetch paginated client list for a manager."""
    if is_elevated:
        # Elevated manager sees ALL clients firm-wide
        query = select(User).where(User.role == UserRole.CLIENT)
    else:
        # Regular manager: only assigned clients
        query = (
            select(User)
            .join(
                ManagerClientAssignment,
                (ManagerClientAssignment.client_id == User.id)
                & (ManagerClientAssignment.manager_id == manager_id)
                & (ManagerClientAssignment.is_active == True),
            )
            .where(User.role == UserRole.CLIENT)
        )

    if search:
        search_filter = f"%{search}%"
        query = query.where(
            (User.full_name.ilike(search_filter)) | (User.email.ilike(search_filter))
        )

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()

    items = []
    for user in users:
        # Check if client has an executive assigned
        exec_assign_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.client_id == user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        exec_assignment = exec_assign_result.scalar_one_or_none()

        exec_name = None
        exec_id = None
        assignment_status = "PENDING_EXECUTIVE"
        if exec_assignment:
            assignment_status = "ASSIGNED"
            exec_user_result = await db.execute(select(User).where(User.id == exec_assignment.executive_id))
            exec_user = exec_user_result.scalar_one_or_none()
            if exec_user:
                exec_name = exec_user.full_name
                exec_id = exec_user.id

        # Active filing info
        filing_result = await db.execute(
            select(ITRFiling.financial_year, ITRFiling.status).where(
                ITRFiling.client_id == user.id,
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            ).limit(1)
        )
        filing_row = filing_result.first()

        items.append(
            ManagerClientListItem(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                phone_number=user.phone_number,
                account_status=user.account_status.value,
                assignment_status=assignment_status,
                assigned_executive_name=exec_name,
                assigned_executive_id=exec_id,
                active_filing_year=filing_row[0] if filing_row else None,
                current_filing_state=filing_row[1].value if filing_row else None,
            )
        )

    return ManagerClientListResponse(items=items, total=total, page=page, page_size=page_size)


# ═══════════════════════════════════════════════════════════════
# MANAGER LOCATION TAGS
# ═══════════════════════════════════════════════════════════════


@router.post("/{manager_id}/tags", response_model=dict, status_code=201)
async def assign_tag_to_mgr(
    manager_id: UUID,
    body: ManagerAssignTagRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign a location tag to a manager (Partner only)."""
    from app.services.tag_service import assign_tag_to_manager

    assignment = await assign_tag_to_manager(
        db=db,
        manager_id=manager_id,
        tag_id=body.tag_id,
        assigned_by=current_user.id,
    )
    return {"message": "Tag assigned to manager", "assignment_id": str(assignment.id)}


@router.delete("/{manager_id}/tags/{tag_id}", status_code=204)
async def remove_tag_from_mgr(
    manager_id: UUID,
    tag_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Remove a location tag from a manager (Partner only)."""
    from app.services.tag_service import remove_tag_from_manager

    await remove_tag_from_manager(db, manager_id, tag_id)


@router.get("/{manager_id}/tags", response_model=list)
async def get_mgr_tags(
    manager_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get location tags assigned to a manager."""
    if current_user.role == UserRole.MANAGER and current_user.id != manager_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Can only view your own tags")

    from app.services.tag_service import get_manager_tags

    return await get_manager_tags(db, manager_id)


# ─── PUT /managers/{manager_id}/elevation ───────────────────
@router.put("/{manager_id}/elevation", response_model=ManagerResponse)
async def toggle_manager_elevation_endpoint(
    manager_id: UUID,
    elevate: bool = Query(..., description="True to elevate, False to de-elevate"),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the elevated status on a Manager (Partner only)."""
    manager = await toggle_manager_elevation(
        db=db, manager_id=manager_id, elevate=elevate, toggled_by=current_user.id
    )

    exec_count_result = await db.execute(
        select(func.count()).select_from(ManagerExecutiveAssignment).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    client_count_result = await db.execute(
        select(func.count()).select_from(ManagerClientAssignment).where(
            ManagerClientAssignment.manager_id == manager_id,
            ManagerClientAssignment.is_active == True,
        )
    )

    return ManagerResponse(
        id=manager.id,
        email=manager.email,
        full_name=manager.full_name,
        account_status=manager.account_status.value,
        is_active=manager.is_active,
        is_elevated=manager.is_elevated,
        team_executive_count=exec_count_result.scalar() or 0,
        team_client_count=client_count_result.scalar() or 0,
        created_at=manager.created_at,
    )
