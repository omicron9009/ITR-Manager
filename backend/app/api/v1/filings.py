"""API v1 — Filing lifecycle endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccountNotActiveError, DuplicateFilingError
from app.core.permissions import enforce_client_access, enforce_filing_access
from app.core.security import get_current_active_client, get_current_executive_or_partner, get_current_user
from app.database import get_db
from app.enums import AccountStatus, AuditEventType, FilingStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_state_history import FilingStateHistory
from app.models.user import User
from app.schemas.filing import (
    FilingHaltRequest,
    FilingInitiateRequest,
    FilingListResponse,
    FilingResponse,
    FilingStateChangeRequest,
    FilingStateHistoryItem,
    FilingTrackingItem,
    FilingTrackingResponse,
)
from app.services.audit_service import record_audit_event
from app.services.filing_service import (
    calculate_progress_percentage,
    check_duplicate_filing,
    transition_filing_status,
)
from app.services.notification_service import create_notification

router = APIRouter()


# ─── POST /filings/initiate ─────────────────────────────────
@router.post("/initiate", response_model=FilingResponse, status_code=201)
async def initiate_filing(
    body: FilingInitiateRequest,
    request: Request,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate a new ITR filing for a financial year.
    Client must be ACTIVE. Only one filing per FY allowed.
    """
    # Check for duplicate
    await check_duplicate_filing(db, current_user.id, body.financial_year)

    # Create filing
    filing = ITRFiling(
        client_id=current_user.id,
        financial_year=body.financial_year,
        status=FilingStatus.INITIATED,
        created_by=current_user.id,
    )
    db.add(filing)
    await db.flush()

    # Record state history (initial state)
    history = FilingStateHistory(
        filing_id=filing.id,
        from_status=None,
        to_status=FilingStatus.INITIATED,
        changed_by=current_user.id,
        remarks="Filing initiated by client",
    )
    db.add(history)

    # Audit log
    await record_audit_event(
        db=db,
        event_type=AuditEventType.FILING_INITIATED,
        actor_id=current_user.id,
        client_id=current_user.id,
        filing_id=filing.id,
        details={"financial_year": body.financial_year},
        ip_address=request.client.host if request.client else None,
    )

    # Notify Partner
    from app.models.user import User as UserModel
    partner_result = await db.execute(
        select(UserModel).where(UserModel.role == UserRole.PARTNER, UserModel.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="New ITR Filing Initiated",
            message=f"New ITR Filing Initiated — {current_user.full_name}, {body.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    # Notify assigned Executive if any
    from app.models.executive_assignment import ExecutiveClientAssignment
    exec_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.client_id == current_user.id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    exec_assignment = exec_result.scalar_one_or_none()
    if exec_assignment:
        filing.assigned_executive_id = exec_assignment.executive_id
        await create_notification(
            db=db,
            user_id=exec_assignment.executive_id,
            title="New ITR Filing Initiated",
            message=f"New ITR Filing Initiated — {current_user.full_name}, {body.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    # Client confirmation notification
    await create_notification(
        db=db,
        user_id=current_user.id,
        title="Filing Initiated Successfully",
        message=f"Your ITR Filing for {body.financial_year} has been initiated successfully.",
        related_filing_id=filing.id,
    )

    await db.flush()
    return FilingResponse(
        id=filing.id,
        client_id=filing.client_id,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        status=filing.status,
        assigned_executive_id=filing.assigned_executive_id,
        initiated_at=filing.initiated_at,
        created_at=filing.created_at,
        updated_at=filing.updated_at,
    )


# ─── GET /filings ───────────────────────────────────────────
@router.get("", response_model=FilingListResponse)
async def list_filings(
    client_id: Optional[UUID] = Query(None),
    status_filter: Optional[FilingStatus] = Query(None, alias="status"),
    financial_year: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List filings with filters. Scoped by role."""
    query = select(ITRFiling)

    # Scope by role
    if current_user.role == UserRole.CLIENT:
        query = query.where(ITRFiling.client_id == current_user.id)
    elif current_user.role == UserRole.EXECUTIVE:
        query = query.where(ITRFiling.assigned_executive_id == current_user.id)
    # Partner sees all

    # Filters
    if client_id:
        if current_user.role != UserRole.CLIENT:
            query = query.where(ITRFiling.client_id == client_id)
    if status_filter:
        query = query.where(ITRFiling.status == status_filter)
    if financial_year:
        query = query.where(ITRFiling.financial_year == financial_year)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(ITRFiling.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    filings = result.scalars().all()

    items = []
    for filing in filings:
        # Get client name
        client_result = await db.execute(select(User).where(User.id == filing.client_id))
        client = client_result.scalar_one_or_none()

        # Get executive name
        exec_name = None
        if filing.assigned_executive_id:
            exec_result = await db.execute(select(User).where(User.id == filing.assigned_executive_id))
            exec_user = exec_result.scalar_one_or_none()
            if exec_user:
                exec_name = exec_user.full_name

        items.append(
            FilingResponse(
                id=filing.id,
                client_id=filing.client_id,
                client_name=client.full_name if client else None,
                financial_year=filing.financial_year,
                status=filing.status,
                assigned_executive_id=filing.assigned_executive_id,
                assigned_executive_name=exec_name,
                initiated_at=filing.initiated_at,
                onboarding_completed_at=filing.onboarding_completed_at,
                documents_submitted_at=filing.documents_submitted_at,
                documents_approved_at=filing.documents_approved_at,
                computation_uploaded_at=filing.computation_uploaded_at,
                computation_approved_at=filing.computation_approved_at,
                filed_at=filing.filed_at,
                payment_received_at=filing.payment_received_at,
                completed_at=filing.completed_at,
                halted_at=filing.halted_at,
                halt_reason=filing.halt_reason,
                created_at=filing.created_at,
                updated_at=filing.updated_at,
            )
        )

    return FilingListResponse(items=items, total=total)


# ─── GET /filings/{filing_id} ───────────────────────────────
@router.get("/{filing_id}", response_model=FilingResponse)
async def get_filing(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific filing's details."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    client_result = await db.execute(select(User).where(User.id == filing.client_id))
    client = client_result.scalar_one_or_none()

    exec_name = None
    if filing.assigned_executive_id:
        exec_result = await db.execute(select(User).where(User.id == filing.assigned_executive_id))
        exec_user = exec_result.scalar_one_or_none()
        if exec_user:
            exec_name = exec_user.full_name

    return FilingResponse(
        id=filing.id,
        client_id=filing.client_id,
        client_name=client.full_name if client else None,
        financial_year=filing.financial_year,
        status=filing.status,
        assigned_executive_id=filing.assigned_executive_id,
        assigned_executive_name=exec_name,
        initiated_at=filing.initiated_at,
        onboarding_completed_at=filing.onboarding_completed_at,
        documents_submitted_at=filing.documents_submitted_at,
        documents_approved_at=filing.documents_approved_at,
        computation_uploaded_at=filing.computation_uploaded_at,
        computation_approved_at=filing.computation_approved_at,
        filed_at=filing.filed_at,
        payment_received_at=filing.payment_received_at,
        completed_at=filing.completed_at,
        halted_at=filing.halted_at,
        halt_reason=filing.halt_reason,
        created_at=filing.created_at,
        updated_at=filing.updated_at,
    )


# ─── POST /filings/{filing_id}/transition ───────────────────
@router.post("/{filing_id}/transition", response_model=FilingResponse)
async def transition_filing(
    filing_id: UUID,
    body: FilingStateChangeRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transition a filing to a new state."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Only Partner/Executive can use the generic transition endpoint
    if current_user.role == UserRole.CLIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clients cannot use the generic transition endpoint",
        )

    # Enforce BRD: certain forward transitions must go through dedicated endpoints.
    # This generic endpoint allows:
    #   - any state → HALTED (halt)
    #   - HALTED → any valid state (resume)
    #   - FILING → PAYMENT (exec/partner marks ITR as filed)
    #   - PAYMENT → COMPLETED (exec/partner marks payment received)
    # Blocked (must use dedicated endpoint):
    #   - INITIATED → ON_BOARDING (use: assign documents)
    #   - ON_BOARDING → PROCESSING (use: submit documents)
    #   - PROCESSING → COMPUTATION (use: approve all documents)
    #   - COMPUTATION → FILING (use: client approves computation)
    is_halt = body.to_status == FilingStatus.HALTED
    is_resume = filing.status == FilingStatus.HALTED
    allowed_forward = {
        (FilingStatus.FILING, FilingStatus.PAYMENT),
        (FilingStatus.PAYMENT, FilingStatus.COMPLETED),
    }
    is_allowed_forward = (filing.status, body.to_status) in allowed_forward

    if not (is_halt or is_resume or is_allowed_forward):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This transition must use its dedicated endpoint: "
                "assign documents (→ON_BOARDING), submit documents (→PROCESSING), "
                "approve documents (→COMPUTATION), approve computation (→FILING)."
            ),
        )

    filing = await transition_filing_status(
        db=db,
        filing=filing,
        to_status=body.to_status,
        changed_by=current_user.id,
        remarks=body.remarks,
        ip_address=request.client.host if request.client else None,
    )

    return FilingResponse(
        id=filing.id,
        client_id=filing.client_id,
        financial_year=filing.financial_year,
        status=filing.status,
        assigned_executive_id=filing.assigned_executive_id,
        initiated_at=filing.initiated_at,
        onboarding_completed_at=filing.onboarding_completed_at,
        documents_submitted_at=filing.documents_submitted_at,
        documents_approved_at=filing.documents_approved_at,
        computation_uploaded_at=filing.computation_uploaded_at,
        computation_approved_at=filing.computation_approved_at,
        filed_at=filing.filed_at,
        payment_received_at=filing.payment_received_at,
        completed_at=filing.completed_at,
        halted_at=filing.halted_at,
        halt_reason=filing.halt_reason,
        created_at=filing.created_at,
        updated_at=filing.updated_at,
    )


# ─── POST /filings/{filing_id}/halt ─────────────────────────
@router.post("/{filing_id}/halt", response_model=FilingResponse)
async def halt_filing(
    filing_id: UUID,
    body: FilingHaltRequest,
    request: Request,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Halt a filing (Partner or Executive action)."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    filing.halt_reason = body.reason
    filing = await transition_filing_status(
        db=db,
        filing=filing,
        to_status=FilingStatus.HALTED,
        changed_by=current_user.id,
        remarks=body.reason,
        ip_address=request.client.host if request.client else None,
    )

    return FilingResponse(
        id=filing.id,
        client_id=filing.client_id,
        financial_year=filing.financial_year,
        status=filing.status,
        assigned_executive_id=filing.assigned_executive_id,
        initiated_at=filing.initiated_at,
        halted_at=filing.halted_at,
        halt_reason=filing.halt_reason,
        created_at=filing.created_at,
        updated_at=filing.updated_at,
    )


# ─── POST /filings/{filing_id}/submit-documents ─────────────
@router.post("/{filing_id}/submit-documents", response_model=dict)
async def submit_documents(
    filing_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client submits documents for review. Transitions to PROCESSING."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    # BRD: Client submits from ON_BOARDING (first time) or ON_BOARDING again (after rejection loop)
    if filing.status not in {FilingStatus.ON_BOARDING, FilingStatus.PROCESSING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Documents can only be submitted while the filing is in ON_BOARDING or PROCESSING, not {filing.status.value}",
        )

    from datetime import datetime
    filing.documents_submitted_at = datetime.utcnow()

    # Transition to PROCESSING if currently in ON_BOARDING
    if filing.status == FilingStatus.ON_BOARDING:
        filing = await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.PROCESSING,
            changed_by=current_user.id,
            remarks="Documents submitted by client",
            ip_address=request.client.host if request.client else None,
        )

    # Notify partner + executive on every submission (initial or re-submission after rejection)
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Documents Submitted",
            message=f"Documents submitted by {current_user.full_name} for {filing.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title="Documents Submitted",
            message=f"Documents submitted by {current_user.full_name} for {filing.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    return {"message": "Documents submitted for review", "status": filing.status.value}


# ─── POST /filings/{filing_id}/mark-payment ─────────────────
@router.post("/{filing_id}/mark-payment", response_model=dict)
async def mark_payment_received(
    filing_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Mark payment as received (Executive/Partner action). Transitions to COMPLETED."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    filing = await transition_filing_status(
        db=db,
        filing=filing,
        to_status=FilingStatus.COMPLETED,
        changed_by=current_user.id,
        remarks="Payment received; filing completed",
        ip_address=request.client.host if request.client else None,
    )

    # Notify client
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Filing Completed",
        message=f"Your ITR filing for {filing.financial_year} is complete. Documents and invoice are now accessible.",
        related_filing_id=filing.id,
    )

    return {"message": "Payment received. Filing marked as completed.", "status": filing.status.value}


# ─── GET /filings/{filing_id}/history ────────────────────────
@router.get("/{filing_id}/history", response_model=list[FilingStateHistoryItem])
async def get_filing_history(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the state transition history of a filing."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    history_result = await db.execute(
        select(FilingStateHistory)
        .where(FilingStateHistory.filing_id == filing_id)
        .order_by(FilingStateHistory.changed_at.asc())
    )
    history_items = history_result.scalars().all()

    items = []
    for h in history_items:
        actor_result = await db.execute(select(User).where(User.id == h.changed_by))
        actor = actor_result.scalar_one_or_none()
        items.append(
            FilingStateHistoryItem(
                id=h.id,
                from_status=h.from_status,
                to_status=h.to_status,
                changed_by=h.changed_by,
                changed_by_name=actor.full_name if actor else None,
                changed_at=h.changed_at,
                remarks=h.remarks,
            )
        )

    return items


# ─── GET /filings/tracking (Client view) ────────────────────
@router.get("/my/tracking", response_model=FilingTrackingResponse)
async def get_my_filing_tracking(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tracking overview of all filings for the current client."""
    if current_user.role == UserRole.CLIENT:
        client_id = current_user.id
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use /filings endpoint for admin view")

    result = await db.execute(
        select(ITRFiling)
        .where(ITRFiling.client_id == client_id)
        .order_by(ITRFiling.financial_year.desc())
    )
    filings = result.scalars().all()

    items = [
        FilingTrackingItem(
            id=f.id,
            financial_year=f.financial_year,
            status=f.status,
            initiated_at=f.initiated_at,
            completed_at=f.completed_at,
            progress_percentage=calculate_progress_percentage(f.status),
        )
        for f in filings
    ]

    return FilingTrackingResponse(items=items)
