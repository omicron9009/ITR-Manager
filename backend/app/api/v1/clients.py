"""API v1 — Client management endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import enforce_client_access
from app.core.security import get_current_executive_or_partner, get_current_partner, get_current_user, hash_password
from app.database import get_db
from app.enums import AccountStatus, UserRole
from app.models.client_profile import ClientProfile
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.user import User
from app.schemas.user import (
    ClientActivationRequest,
    ClientListItem,
    ClientListResponse,
    ClientProfileResponse,
    ClientProfileUpdate,
    ClientRegistrationRequest,
    ClientRegistrationResponse,
    ClientRejectionRequest,
)
from app.services.client_service import activate_client, register_client, reject_client

router = APIRouter()


# ─── POST /clients/register ─────────────────────────────────
@router.post("/register", response_model=ClientRegistrationResponse, status_code=201)
async def register_new_client(
    request_data: ClientRegistrationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new client. Account will be in PENDING_VERIFICATION state."""
    user = await register_client(
        db=db,
        email=request_data.email,
        full_name=request_data.full_name,
        password_hash=hash_password(request_data.password),
        phone_number=request_data.phone_number,
    )
    return ClientRegistrationResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        account_status=user.account_status.value,
    )


# ─── POST /clients/activate ─────────────────────────────────
@router.post("/activate", response_model=dict)
async def activate_client_account(
    body: ClientActivationRequest,
    request: Request,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Activate a client account (Partner or Executive)."""
    client = await activate_client(
        db=db,
        client_id=body.client_id,
        activated_by=current_user.id,
        ip_address=request.client.host if request.client else None,
    )
    return {"message": f"Client {client.full_name} has been activated", "client_id": str(client.id)}


# ─── POST /clients/reject ───────────────────────────────────
@router.post("/reject", response_model=dict)
async def reject_client_account(
    body: ClientRejectionRequest,
    request: Request,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reject a client registration (Partner or Executive)."""
    client = await reject_client(
        db=db,
        client_id=body.client_id,
        rejected_by=current_user.id,
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
    )
    return {"message": f"Client {client.full_name} registration rejected", "client_id": str(client.id)}


# ─── GET /clients ───────────────────────────────────────────
@router.get("", response_model=ClientListResponse)
async def list_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    account_status: Optional[AccountStatus] = Query(None),
    financial_year: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List clients with search and filtering.
    - Partner: sees all clients
    - Executive: sees only assigned clients
    - Client: not permitted
    """
    if current_user.role == UserRole.CLIENT:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Base query
    query = select(User).where(User.role == UserRole.CLIENT)

    # Executive scope: only assigned clients
    if current_user.role == UserRole.EXECUTIVE:
        query = query.join(
            ExecutiveClientAssignment,
            (ExecutiveClientAssignment.client_id == User.id)
            & (ExecutiveClientAssignment.executive_id == current_user.id)
            & (ExecutiveClientAssignment.is_active == True),
        )

    # Filters
    if account_status:
        query = query.where(User.account_status == account_status)

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

    # Build response items
    items = []
    for user in users:
        # Get assignment
        assign_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.client_id == user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        assignment = assign_result.scalar_one_or_none()

        exec_name = None
        exec_id = None
        if assignment:
            exec_result = await db.execute(select(User).where(User.id == assignment.executive_id))
            exec_user = exec_result.scalar_one_or_none()
            if exec_user:
                exec_name = exec_user.full_name
                exec_id = exec_user.id

        # Get active filing years
        fy_result = await db.execute(
            select(ITRFiling.financial_year, ITRFiling.status).where(
                ITRFiling.client_id == user.id,
                ITRFiling.status.notin_(["COMPLETED", "HALTED"]),
            )
        )
        filings = fy_result.all()
        active_years = [f[0] for f in filings]
        current_state = filings[0][1].value if filings else None

        items.append(
            ClientListItem(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                phone_number=user.phone_number,
                account_status=user.account_status.value,
                assigned_executive_name=exec_name,
                assigned_executive_id=exec_id,
                active_filing_years=active_years,
                current_state=current_state,
                last_updated=user.updated_at,
            )
        )

    return ClientListResponse(items=items, total=total, page=page, page_size=page_size)


# ─── GET /clients/{client_id} ───────────────────────────────
@router.get("/{client_id}", response_model=ClientProfileResponse)
async def get_client_profile(
    client_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a client's profile. Access enforced by role."""
    await enforce_client_access(db, current_user, client_id)

    result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    # Fetch user details for phone_number, full_name, email
    user_result = await db.execute(select(User).where(User.id == client_id))
    user = user_result.scalar_one_or_none()

    # Fetch assigned executive
    from app.models.executive_assignment import ExecutiveClientAssignment
    exec_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    assignment = exec_result.scalar_one_or_none()
    exec_id = None
    exec_name = None
    if assignment:
        exec_user_result = await db.execute(select(User).where(User.id == assignment.executive_id))
        exec_user = exec_user_result.scalar_one_or_none()
        if exec_user:
            exec_id = exec_user.id
            exec_name = exec_user.full_name

    from app.schemas.user import ClientProfileResponse
    return ClientProfileResponse(
        id=profile.id,
        user_id=profile.user_id,
        full_name=user.full_name if user else None,
        email=user.email if user else None,
        phone_number=user.phone_number if user else None,
        pan_number=profile.pan_number,
        aadhaar_number=profile.aadhaar_number,
        date_of_birth=profile.date_of_birth,
        contact_number=profile.contact_number,
        address=profile.address,
        income_type=profile.income_type,
        bank_account_details=profile.bank_account_details,
        form_data=profile.form_data or {},
        form_submitted_at=profile.form_submitted_at,
        assigned_executive_id=exec_id,
        assigned_executive_name=exec_name,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


# ─── PUT /clients/{client_id}/profile ────────────────────────
@router.put("/{client_id}/profile", response_model=ClientProfileResponse)
async def update_client_profile(
    client_id: UUID,
    body: ClientProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a client's profile (onboarding data)."""
    await enforce_client_access(db, current_user, client_id)

    result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(profile, key, value)

    await db.flush()
    return profile
