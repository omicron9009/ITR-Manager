"""API v1 — Executive management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_partner, hash_password
from app.database import get_db
from app.enums import FilingStatus, UserRole
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.user import User
from app.schemas.executive import (
    ExecutiveAssignmentResponse,
    ExecutiveAssignRequest,
    ExecutiveCreateRequest,
    ExecutiveListResponse,
    ExecutiveResponse,
)
from app.services.executive_service import (
    assign_executive_to_client,
    create_executive,
    deactivate_executive,
    reactivate_executive,
)

router = APIRouter()


# ─── POST /executives ───────────────────────────────────────
@router.post("", response_model=ExecutiveResponse, status_code=201)
async def create_new_executive(
    body: ExecutiveCreateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Create a new Executive account (Partner only)."""
    executive = await create_executive(
        db=db,
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        created_by=current_user.id,
    )
    return ExecutiveResponse(
        id=executive.id,
        email=executive.email,
        full_name=executive.full_name,
        account_status=executive.account_status.value,
        is_active=executive.is_active,
        assigned_client_count=0,
        active_filing_count=0,
        created_at=executive.created_at,
    )


# ─── GET /executives ────────────────────────────────────────
@router.get("", response_model=ExecutiveListResponse)
async def list_executives(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """List all executives with workload info (Partner only)."""
    result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE).order_by(User.full_name)
    )
    executives = result.scalars().all()

    items = []
    for exec_user in executives:
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

        items.append(
            ExecutiveResponse(
                id=exec_user.id,
                email=exec_user.email,
                full_name=exec_user.full_name,
                account_status=exec_user.account_status.value,
                is_active=exec_user.is_active,
                assigned_client_count=client_count,
                active_filing_count=filing_count,
                created_at=exec_user.created_at,
            )
        )

    return ExecutiveListResponse(items=items, total=len(items))


# ─── POST /executives/assign ────────────────────────────────
@router.post("/assign", response_model=ExecutiveAssignmentResponse)
async def assign_executive(
    body: ExecutiveAssignRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign an Executive to a Client (Partner only)."""
    # Verify executive exists and is active
    exec_result = await db.execute(
        select(User).where(User.id == body.executive_id, User.role == UserRole.EXECUTIVE, User.is_active == True)
    )
    executive = exec_result.scalar_one_or_none()
    if not executive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found or inactive")

    # Verify client exists
    client_result = await db.execute(
        select(User).where(User.id == body.client_id, User.role == UserRole.CLIENT)
    )
    client = client_result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    assignment = await assign_executive_to_client(
        db=db,
        executive_id=body.executive_id,
        client_id=body.client_id,
        assigned_by=current_user.id,
    )

    return ExecutiveAssignmentResponse(
        id=assignment.id,
        executive_id=assignment.executive_id,
        executive_name=executive.full_name,
        client_id=assignment.client_id,
        client_name=client.full_name,
        assigned_at=assignment.assigned_at,
        is_active=assignment.is_active,
    )


# ─── POST /executives/{id}/deactivate ───────────────────────
@router.post("/{executive_id}/deactivate", response_model=dict)
async def deactivate_executive_account(
    executive_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an Executive account (Partner only)."""
    executive = await deactivate_executive(db, executive_id, current_user.id)
    return {"message": f"Executive {executive.full_name} has been deactivated"}


# ─── POST /executives/{id}/reactivate ───────────────────────
@router.post("/{executive_id}/reactivate", response_model=dict)
async def reactivate_executive_account(
    executive_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reactivate a deactivated Executive account (Partner only)."""
    executive = await reactivate_executive(db, executive_id, current_user.id)
    return {"message": f"Executive {executive.full_name} has been reactivated"}


# ─── GET /executives/{id}/clients ────────────────────────────
@router.get("/{executive_id}/clients", response_model=dict)
async def get_executive_clients(
    executive_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get list of clients assigned to a specific executive."""
    assignments_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    assignments = assignments_result.scalars().all()

    clients = []
    for assignment in assignments:
        client_result = await db.execute(select(User).where(User.id == assignment.client_id))
        client = client_result.scalar_one_or_none()
        if client:
            clients.append({
                "client_id": str(client.id),
                "full_name": client.full_name,
                "email": client.email,
                "account_status": client.account_status.value,
                "assigned_at": assignment.assigned_at.isoformat(),
            })

    return {"executive_id": str(executive_id), "clients": clients, "total": len(clients)}
