"""API v1 — Client management endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_executive_or_partner, get_current_manager_executive_or_partner, get_current_partner, get_current_user, hash_password
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
    IncomeHeadsResponse,
    IncomeHeadsUpdateRequest,
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
        income_heads={
            "salary": request_data.salary,
            "esop": request_data.esop,
            "rental_income": request_data.rental_income,
            "more_than_2_properties": request_data.more_than_2_properties,
            "capital_gain_shares": request_data.capital_gain_shares,
            "capital_gain_land": request_data.capital_gain_land,
            "business_profession": request_data.business_profession,
            "interest_dividend": request_data.interest_dividend,
            "foreign_assets": request_data.foreign_assets,
            "any_other": request_data.any_other,
            "any_other_text": request_data.any_other_text if request_data.any_other else None,
        },
        referral_source=request_data.referral_source,
        referral_source_other=request_data.referral_source_other,
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
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Activate a client account (Partner, Manager, or Executive)."""
    client = await activate_client(
        db=db,
        client_id=body.client_id,
        activated_by=current_user.id,
        ip_address=request.client.host if request.client else None,
        professional_fee=body.professional_fee,
        no_fees_applicable=body.no_fees_applicable,
    )
    return {"message": f"Client {client.full_name} has been activated", "client_id": str(client.id)}


# ─── POST /clients/reject ───────────────────────────────────
@router.post("/reject", response_model=dict)
async def reject_client_account(
    body: ClientRejectionRequest,
    request: Request,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reject a client registration (Partner, Manager, or Executive)."""
    client = await reject_client(
        db=db,
        client_id=body.client_id,
        rejected_by=current_user.id,
        reason=body.reason,
        ip_address=request.client.host if request.client else None,
    )
    return {"message": f"Client {client.full_name} registration rejected", "client_id": str(client.id)}


# ─── POST /clients/{client_id}/set-fee ──────────────────────
@router.post("/{client_id}/set-fee", response_model=dict)
async def set_client_fee(
    client_id: UUID,
    fee: float = Query(..., gt=0, description="Professional fee in rupees"),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Set or update the professional fee for a client. Partner only."""
    from fastapi import HTTPException, status
    from decimal import Decimal
    from app.services.notification_service import create_notification

    result = await db.execute(select(User).where(User.id == client_id, User.role == UserRole.CLIENT))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    profile.professional_fee = Decimal(str(fee))
    # Setting a fee implicitly clears no_fees_applicable
    if profile.no_fees_applicable:
        profile.no_fees_applicable = False
    await db.flush()

    # Notify client that fee is set and they can proceed with filing
    await create_notification(
        db=db,
        user_id=client_id,
        title="Engagement Fee Set",
        message=f"Your professional fee has been set. You may now initiate your ITR filing and accept the Engagement Letter.",
        related_client_id=client_id,
    )

    await db.commit()
    return {"message": f"Professional fee set to ₹{fee:.2f} for {client.full_name}", "client_id": str(client_id)}


# ─── POST /clients/{client_id}/toggle-no-fees ───────────────
@router.post("/{client_id}/toggle-no-fees", response_model=dict)
async def toggle_no_fees(
    client_id: UUID,
    no_fees: bool = Query(..., description="Set to true to mark client as no-fees-applicable"),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Toggle no_fees_applicable for a client. Partner only."""
    from fastapi import HTTPException, status

    result = await db.execute(select(User).where(User.id == client_id, User.role == UserRole.CLIENT))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    profile.no_fees_applicable = no_fees
    if no_fees:
        profile.professional_fee = None  # Clear fee when no fees applicable

    await db.commit()

    status_label = "No Fees Applicable" if no_fees else "Fees Applicable"
    return {
        "message": f"Client {client.full_name} marked as '{status_label}'",
        "client_id": str(client_id),
        "no_fees_applicable": no_fees,
    }


# ─── POST /clients/{client_id}/set-partner-tag ──────────────
@router.post("/{client_id}/set-partner-tag", response_model=dict)
async def set_client_partner_tag(
    client_id: UUID,
    tag_id: UUID = Query(..., description="Partner tag ID to assign"),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign or change the partner tag for a client. Partner only."""
    from fastapi import HTTPException, status
    from app.models.tag import Tag
    from app.enums import TagType

    result = await db.execute(select(User).where(User.id == client_id, User.role == UserRole.CLIENT))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    # Verify tag exists, is active, and is of type PARTNER
    tag_result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.is_active == True, Tag.tag_type == TagType.PARTNER)
    )
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner tag not found or inactive")

    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    profile.partner_tag_id = tag_id
    await db.commit()

    from app.core.cache import NS, bump_version
    await bump_version(NS.CLIENT_LIST)

    return {
        "message": f"Partner tag '{tag.name}' assigned to {client.full_name}",
        "client_id": str(client_id),
        "partner_tag_id": str(tag_id),
        "partner_tag_name": tag.name,
    }


# ─── DELETE /clients/{client_id}/partner-tag ─────────────────
@router.delete("/{client_id}/partner-tag", response_model=dict)
async def remove_client_partner_tag(
    client_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Remove the partner tag from a client. Partner only."""
    from fastapi import HTTPException, status

    result = await db.execute(select(User).where(User.id == client_id, User.role == UserRole.CLIENT))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")

    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    if profile.partner_tag_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Client has no partner tag assigned")

    profile.partner_tag_id = None
    await db.commit()

    from app.core.cache import NS, bump_version
    await bump_version(NS.CLIENT_LIST)

    return {
        "message": f"Partner tag removed from {client.full_name}",
        "client_id": str(client_id),
    }


# ─── GET /clients ───────────────────────────────────────────
@router.get("", response_model=ClientListResponse)
async def list_clients(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    account_status: Optional[AccountStatus] = Query(None),
    financial_year: Optional[str] = Query(None),
    partner_tag_id: Optional[UUID] = Query(None, description="Filter by partner tag"),
    onboarded_pending_filing: bool = Query(
        False,
        description="Only ACTIVE clients who submitted onboarding but have not initiated any filing",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List clients with search and filtering.
    - Partner: sees all clients
    - Manager: sees clients of their team's executives
    - Executive: sees only assigned clients
    - Client: not permitted
    """
    from app.config import settings as _settings
    from app.core.cache import NS, cache_get, cache_set, _MISS

    if current_user.role == UserRole.CLIENT:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # When onboarded_pending_filing is requested, force account_status=ACTIVE
    # (a non-ACTIVE client cannot meaningfully be "onboarded & pending filing").
    if onboarded_pending_filing:
        account_status = AccountStatus.ACTIVE

    # Cache key encodes the full filter + user scope
    cache_key = f"{current_user.id}:{page}:{page_size}:{search}:{account_status}:{financial_year}:{partner_tag_id}:{onboarded_pending_filing}"
    cached = await cache_get(NS.CLIENT_LIST, cache_key)
    if cached is not _MISS:
        return cached

    # Base query
    query = select(User).where(User.role == UserRole.CLIENT)

    # Manager scope: clients directly assigned to this manager
    if current_user.role == UserRole.MANAGER:
        from app.models.manager_client_assignment import ManagerClientAssignment
        query = query.join(
            ManagerClientAssignment,
            (ManagerClientAssignment.client_id == User.id)
            & (ManagerClientAssignment.manager_id == current_user.id)
            & (ManagerClientAssignment.is_active == True),
        )

    # Executive scope: only assigned clients
    elif current_user.role == UserRole.EXECUTIVE:
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

    # Financial year filter — subquery to avoid row duplication from multi-filing clients
    if financial_year:
        fy_sub = select(ITRFiling.client_id).where(
            ITRFiling.financial_year == financial_year
        ).scalar_subquery()
        query = query.where(User.id.in_(fy_sub))

    # Partner tag filter
    if partner_tag_id:
        pt_sub = select(ClientProfile.user_id).where(
            ClientProfile.partner_tag_id == partner_tag_id
        ).scalar_subquery()
        query = query.where(User.id.in_(pt_sub))

    # Onboarded-but-no-filing filter:
    #   1) account_status already forced to ACTIVE above
    #   2) form_submitted_at must be set (onboarding submitted)
    #   3) NOT EXISTS any itr_filings row for this client (anti-join on indexed FK)
    if onboarded_pending_filing:
        submitted_sub = select(ClientProfile.user_id).where(
            ClientProfile.form_submitted_at.isnot(None)
        ).scalar_subquery()
        no_filing_sub = select(ITRFiling.client_id).scalar_subquery()
        query = query.where(
            User.id.in_(submitted_sub),
            User.id.notin_(no_filing_sub),
        )

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = result.scalars().all()

    if not users:
        response = ClientListResponse(items=[], total=total, page=page, page_size=page_size)
        await cache_set(NS.CLIENT_LIST, cache_key, response, _settings.CACHE_TTL_CLIENT_LIST)
        return response

    user_ids = [u.id for u in users]

    # ── Batch-fetch active executive assignments for the page (was N+1) ──
    assign_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id.in_(user_ids),
            ExecutiveClientAssignment.is_active == True,
        )
    )
    assignments_by_client = {a.client_id: a for a in assign_result.scalars().all()}

    # ── Batch-fetch executive names for those assignments ──
    exec_ids = list({a.executive_id for a in assignments_by_client.values()})
    exec_name_by_id: dict = {}
    if exec_ids:
        exec_rows = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(exec_ids))
        )
        exec_name_by_id = {row[0]: row[1] for row in exec_rows.all()}

    # ── Batch-fetch manager assignments (executive → manager) ──
    from app.models.manager_executive_assignment import ManagerExecutiveAssignment
    mgr_by_exec: dict = {}
    if exec_ids:
        mgr_assign_result = await db.execute(
            select(ManagerExecutiveAssignment.executive_id, ManagerExecutiveAssignment.manager_id).where(
                ManagerExecutiveAssignment.executive_id.in_(exec_ids),
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        mgr_by_exec = {row[0]: row[1] for row in mgr_assign_result.all()}

    mgr_ids = list(set(mgr_by_exec.values()))
    mgr_name_by_id: dict = {}
    if mgr_ids:
        mgr_rows = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(mgr_ids))
        )
        mgr_name_by_id = {row[0]: row[1] for row in mgr_rows.all()}

    # ── Batch-fetch active filings for the page (was N+1) ──
    # ORDER BY created_at DESC ensures [0] is always the most recent filing
    filings_result = await db.execute(
        select(ITRFiling.client_id, ITRFiling.financial_year, ITRFiling.status).where(
            ITRFiling.client_id.in_(user_ids),
            ITRFiling.status.notin_(["COMPLETED", "HALTED"]),
        ).order_by(ITRFiling.created_at.desc())
    )
    filings_by_client: dict = {}
    for cid, fy, st in filings_result.all():
        filings_by_client.setdefault(cid, []).append((fy, st))

    # ── Batch-fetch partner tags for the page ──
    from app.models.tag import Tag
    profile_tag_result = await db.execute(
        select(ClientProfile.user_id, ClientProfile.partner_tag_id).where(
            ClientProfile.user_id.in_(user_ids),
            ClientProfile.partner_tag_id.isnot(None),
        )
    )
    partner_tag_by_client = {row[0]: row[1] for row in profile_tag_result.all()}
    tag_ids = list(set(partner_tag_by_client.values()))
    tag_name_by_id: dict = {}
    if tag_ids:
        tag_rows = await db.execute(
            select(Tag.id, Tag.name).where(Tag.id.in_(tag_ids))
        )
        tag_name_by_id = {row[0]: row[1] for row in tag_rows.all()}

    # Build response items
    items = []
    for user in users:
        assignment = assignments_by_client.get(user.id)
        exec_name = None
        exec_id = None
        mgr_id = None
        mgr_name = None
        if assignment:
            exec_id = assignment.executive_id
            exec_name = exec_name_by_id.get(exec_id)
            # Resolve manager from executive
            _mgr_id = mgr_by_exec.get(exec_id)
            if _mgr_id:
                mgr_id = _mgr_id
                mgr_name = mgr_name_by_id.get(_mgr_id)

        user_filings = filings_by_client.get(user.id, [])
        active_years = [f[0] for f in user_filings]
        current_state = user_filings[0][1].value if user_filings else None

        items.append(
            ClientListItem(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                phone_number=user.phone_number,
                account_status=user.account_status.value,
                assigned_executive_name=exec_name,
                assigned_executive_id=exec_id,
                assigned_manager_id=mgr_id,
                assigned_manager_name=mgr_name,
                partner_tag_id=partner_tag_by_client.get(user.id),
                partner_tag_name=tag_name_by_id.get(partner_tag_by_client.get(user.id)),
                active_filing_years=active_years,
                current_state=current_state,
                last_updated=user.updated_at,
            )
        )

    response = ClientListResponse(items=items, total=total, page=page, page_size=page_size)
    await cache_set(NS.CLIENT_LIST, cache_key, response, _settings.CACHE_TTL_CLIENT_LIST)
    return response


# ─── PUT /clients/me/income-heads ────────────────────────────
@router.put("/me/income-heads", response_model=IncomeHeadsResponse)
async def update_my_income_heads(
    body: IncomeHeadsUpdateRequest,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client updates their own income heads (partial update)."""
    from app.models.client_income_heads import ClientIncomeHeads

    result = await db.execute(
        select(ClientIncomeHeads).where(ClientIncomeHeads.user_id == current_user.id)
    )
    heads = result.scalar_one_or_none()

    if not heads:
        # Create if not exists (edge case: legacy accounts)
        heads = ClientIncomeHeads(user_id=current_user.id)
        db.add(heads)

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(heads, key, value)

    # Clear any_other_text if any_other is set to False
    if "any_other" in update_data and not update_data["any_other"]:
        heads.any_other_text = None

    await db.commit()
    await db.refresh(heads)
    return heads


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
    from app.models.client_income_heads import ClientIncomeHeads
    exec_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    assignment = exec_result.scalar_one_or_none()
    exec_id = None
    exec_name = None
    mgr_id = None
    mgr_name = None
    if assignment:
        exec_user_result = await db.execute(select(User).where(User.id == assignment.executive_id))
        exec_user = exec_user_result.scalar_one_or_none()
        if exec_user:
            exec_id = exec_user.id
            exec_name = exec_user.full_name

        # Resolve manager via executive → manager assignment chain
        from app.models.manager_executive_assignment import ManagerExecutiveAssignment
        mgr_assign_result = await db.execute(
            select(ManagerExecutiveAssignment).where(
                ManagerExecutiveAssignment.executive_id == assignment.executive_id,
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        mgr_assign = mgr_assign_result.scalar_one_or_none()
        if mgr_assign:
            mgr_user_result = await db.execute(select(User).where(User.id == mgr_assign.manager_id))
            mgr_user = mgr_user_result.scalar_one_or_none()
            if mgr_user:
                mgr_id = mgr_user.id
                mgr_name = mgr_user.full_name

    from app.schemas.user import ClientProfileResponse, IncomeHeadsResponse

    # Fetch income heads
    heads_result = await db.execute(
        select(ClientIncomeHeads).where(ClientIncomeHeads.user_id == client_id)
    )
    heads = heads_result.scalar_one_or_none()
    income_heads_data = IncomeHeadsResponse.model_validate(heads) if heads else None

    # Fetch partner tag name if assigned
    partner_tag_name = None
    if profile.partner_tag_id:
        from app.models.tag import Tag
        tag_result = await db.execute(select(Tag.name).where(Tag.id == profile.partner_tag_id))
        partner_tag_name = tag_result.scalar()

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
        assigned_manager_id=mgr_id,
        assigned_manager_name=mgr_name,
        income_heads=income_heads_data,
        referral_source=profile.referral_source,
        referral_source_other=profile.referral_source_other,
        professional_fee=profile.professional_fee,
        partner_tag_id=profile.partner_tag_id,
        partner_tag_name=partner_tag_name,
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
