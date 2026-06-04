"""API v1 — Filing lifecycle endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AccountNotActiveError, DuplicateFilingError, OnboardingFormNotSubmittedError
from app.core.permissions import enforce_client_access, enforce_filing_access
from app.core.security import get_current_active_client, get_current_executive_or_partner, get_current_manager_executive_or_partner, get_current_user,get_current_manager_or_partner
from app.database import get_db
from app.enums import AccountStatus, AuditEventType, FilingStatus, UserRole
from app.models.client_profile import ClientProfile
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
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate a new ITR filing for a financial year.
    Client must be ACTIVE. Only one filing per FY allowed.
    """
    # Check for duplicate
    await check_duplicate_filing(db, current_user.id, body.financial_year)

    # Check onboarding form submission — BRD §7 STATE 1 requires form before filing
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()

    if not profile or not profile.form_submitted_at:
        raise OnboardingFormNotSubmittedError()

    # Ensure professional fee has been set by Partner before client can file
    if not profile.professional_fee:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Professional fee has not been set by the firm. Please contact support.",
        )

    # Generate engagement letter PDF and upload to MinIO
    from datetime import datetime
    from app.services.engagement_letter_service import generate_engagement_letter_pdf, upload_engagement_letter

    now = datetime.utcnow()
    pdf_bytes = generate_engagement_letter_pdf(
        client_name=current_user.full_name,
        financial_year=body.financial_year,
        professional_fee=profile.professional_fee,
        accepted_at=now,
    )
    engagement_key = upload_engagement_letter(
        client_id=str(current_user.id),
        client_name=current_user.full_name,
        financial_year=body.financial_year,
        pdf_bytes=pdf_bytes,
    )

    # Create filing
    filing = ITRFiling(
        client_id=current_user.id,
        financial_year=body.financial_year,
        status=FilingStatus.INITIATED,
        created_by=current_user.id,
        professional_fee=profile.professional_fee,
        engagement_accepted_at=now,
        engagement_letter_key=engagement_key,
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
            message=f"A new ITR Filing has been initiated by {current_user.full_name} for FY {body.financial_year}.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=body.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}",
            cta_label="View Filing",
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
            message=f"A new ITR Filing has been initiated by {current_user.full_name} for FY {body.financial_year}. Please send the document checklist.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=body.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/documents/assign",
            cta_label="Send Document Checklist",
        )

    # Client confirmation notification
    await create_notification(
        db=db,
        user_id=current_user.id,
        title="Filing Initiated Successfully",
        message=f"Your ITR Filing for FY {body.financial_year} has been initiated successfully. You will be notified when your document checklist is ready.",
        related_filing_id=filing.id,
        financial_year=body.financial_year,
        action_url_path=f"/filings/{filing.id}",
        cta_label="View My Filing",
    )

    # Email engagement letter PDF to client (async background task)
    background_tasks.add_task(
        _send_engagement_letter_email,
        client_email=current_user.email,
        client_name=current_user.full_name,
        financial_year=body.financial_year,
        pdf_bytes=pdf_bytes,
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
        is_tax_paid=filing.is_tax_paid,
        tax_paid_at=filing.tax_paid_at,
        professional_fee=filing.professional_fee,
        engagement_accepted_at=filing.engagement_accepted_at,
        created_at=filing.created_at,
        updated_at=filing.updated_at,
    )


async def _send_engagement_letter_email(
    client_email: str,
    client_name: str,
    financial_year: str,
    pdf_bytes: bytes,
):
    """Background task: email engagement letter PDF to client."""
    import html

    from app.database import AsyncSessionLocal
    from app.services.email_service import send_email_with_attachment
    from app.config import settings

    safe_name = html.escape(client_name)
    safe_fy = html.escape(financial_year)
    subject = f"Engagement Letter - ITR Filing {financial_year}"
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a56db; padding: 20px; color: white; text-align: center;">
            <h2>{html.escape(settings.APP_NAME)}</h2>
        </div>
        <div style="padding: 20px; border: 1px solid #e5e7eb;">
            <h3>Engagement Letter - {safe_fy}</h3>
            <p>Dear {safe_name},</p>
            <p>Thank you for initiating your ITR filing for the financial year {safe_fy}.</p>
            <p>Please find attached your signed Engagement Letter for Income Tax Return Filing Services
            with P G Joshi and Co LLP.</p>
            <p>This document confirms your acceptance of the terms of engagement.</p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <p style="color: #6b7280; font-size: 12px;">
                This is an automated email from {html.escape(settings.APP_NAME)}. Please retain this for your records.
            </p>
        </div>
    </body>
    </html>
    """

    try:
        async with AsyncSessionLocal() as db:
            await send_email_with_attachment(
                to_email=client_email,
                subject=subject,
                body_html=html_body,
                attachment_bytes=pdf_bytes,
                attachment_filename=f"Engagement_Letter_{financial_year}.pdf",
                db=db,
            )
            await db.commit()
    except Exception as e:
        import logging
        logging.getLogger("app").error(f"Failed to email engagement letter to {client_email}: {e}")


# ─── POST /filings/{filing_id}/update-fee ───────────────────
@router.post("/{filing_id}/update-fee", response_model=dict)
async def update_filing_fee(
    filing_id: UUID,
    fee: float = Query(..., gt=0, description="Proposed professional fee in rupees"),
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Propose a fee change for a filing. Partner only. Client must approve before it takes effect."""
    from decimal import Decimal
    from datetime import datetime

    if current_user.role != UserRole.PARTNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Partner can update professional fee")

    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.proposed_fee is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A fee change is already pending client approval. Wait for client response first.",
        )

    new_fee = Decimal(str(fee))
    filing.proposed_fee = new_fee
    filing.fee_proposed_at = datetime.utcnow()
    filing.fee_proposed_by = current_user.id

    # Notify client about fee change
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Professional Fee Change Proposed",
        message=f"A revised professional fee of Rs. {fee:.2f} has been proposed for your ITR filing (FY {filing.financial_year}). Please review and approve or reject the proposed fee.",
        related_filing_id=filing.id,
        related_client_id=filing.client_id,
        financial_year=filing.financial_year,
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}/fee",
        cta_label="Review Fee Change",
        extra_details={"Proposed Fee": f"Rs. {fee:.2f}", "Current Fee": f"Rs. {filing.professional_fee:.2f}" if filing.professional_fee else "Not set"},
    )

    await db.flush()
    await db.commit()

    return {
        "message": f"Fee change of Rs. {fee:.2f} proposed. Awaiting client approval.",
        "filing_id": str(filing_id),
        "proposed_fee": float(new_fee),
    }


# ─── POST /filings/{filing_id}/approve-fee ──────────────────
@router.post("/{filing_id}/approve-fee", response_model=dict)
async def approve_fee_change(
    filing_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client approves a proposed fee change. Updates fee and regenerates engagement letter."""
    from decimal import Decimal
    from datetime import datetime
    from app.services.engagement_letter_service import generate_engagement_letter_pdf, upload_engagement_letter

    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    if filing.proposed_fee is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No pending fee change to approve")

    # Apply the proposed fee
    old_fee = filing.professional_fee
    filing.professional_fee = filing.proposed_fee
    filing.proposed_fee = None
    filing.fee_proposed_at = None
    filing.fee_proposed_by = None

    # Also update the client profile's base fee
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile:
        profile.professional_fee = filing.professional_fee

    # Regenerate engagement letter PDF with updated fee
    accepted_at = filing.engagement_accepted_at or datetime.utcnow()
    pdf_bytes = generate_engagement_letter_pdf(
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        professional_fee=filing.professional_fee,
        accepted_at=accepted_at,
    )
    engagement_key = upload_engagement_letter(
        client_id=str(current_user.id),
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        pdf_bytes=pdf_bytes,
    )
    filing.engagement_letter_key = engagement_key

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
            title="Fee Change Approved",
            message=f"{current_user.full_name} has approved the revised professional fee for FY {filing.financial_year}.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}",
            cta_label="View Filing",
            extra_details={"Approved Fee": f"Rs. {filing.professional_fee:.2f}"},
        )

    # Email updated engagement letter to client (background)
    background_tasks.add_task(
        _send_engagement_letter_email,
        client_email=current_user.email,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        pdf_bytes=pdf_bytes,
    )

    await db.flush()
    await db.commit()

    return {
        "message": f"Fee change approved. Updated to Rs. {filing.professional_fee:.2f}. Updated engagement letter has been emailed.",
        "filing_id": str(filing_id),
        "professional_fee": float(filing.professional_fee),
    }


# ─── POST /filings/{filing_id}/reject-fee ───────────────────
@router.post("/{filing_id}/reject-fee", response_model=dict)
async def reject_fee_change(
    filing_id: UUID,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client rejects a proposed fee change. Fee stays unchanged."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    if filing.proposed_fee is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No pending fee change to reject")

    rejected_fee = filing.proposed_fee
    filing.proposed_fee = None
    filing.fee_proposed_at = None
    filing.fee_proposed_by = None

    # Notify Partner about rejection
    from app.models.user import User as UserModel
    partner_result = await db.execute(
        select(UserModel).where(UserModel.role == UserRole.PARTNER, UserModel.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Fee Change Rejected",
            message=f"{current_user.full_name} has rejected the proposed fee change for FY {filing.financial_year}. The current fee remains unchanged.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}",
            cta_label="View Filing",
            extra_details={"Rejected Fee": f"Rs. {rejected_fee:.2f}", "Current Fee": f"Rs. {filing.professional_fee:.2f}" if filing.professional_fee else "Not set"},
        )

    await db.flush()
    await db.commit()

    return {
        "message": f"Fee change rejected. Current fee remains Rs. {filing.professional_fee:.2f}.",
        "filing_id": str(filing_id),
    }


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
                is_tax_paid=filing.is_tax_paid,
                tax_paid_at=filing.tax_paid_at,
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

    # Check if internal working docs exist for this filing
    from app.models.internal_working_doc import InternalWorkingDoc
    iw_count = await db.scalar(
        select(func.count()).select_from(InternalWorkingDoc)
        .where(InternalWorkingDoc.filing_id == filing.id)
    )

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
        is_tax_paid=filing.is_tax_paid,
        tax_paid_at=filing.tax_paid_at,
        filed_at=filing.filed_at,
        payment_received_at=filing.payment_received_at,
        completed_at=filing.completed_at,
        halted_at=filing.halted_at,
        halt_reason=filing.halt_reason,
        has_internal_workings=bool(iw_count and iw_count > 0),
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
    #   - INITIATED → DOCUMENT_UPLOAD (assign documents handles this, but allow here for idempotency)
    #   - COMPUTATION → PROCESSING (request more docs)
    # Blocked (must use dedicated endpoint):
    #   - DOCUMENT_UPLOAD → PROCESSING (manual: use /move-to-computation)
    #   - PROCESSING → COMPUTATION (manual: use /move-to-computation)
    #   - COMPUTATION → FILING (use: client approves computation)
    #   - FILING → PAYMENT (auto: when all required completed docs uploaded)
    #   - PAYMENT → COMPLETED (use: mark payment received)
    is_halt = body.to_status == FilingStatus.HALTED
    is_resume = filing.status == FilingStatus.HALTED
    is_already_in_target = filing.status == body.to_status
    # Generic transition only allows transitions that DON'T have dedicated endpoints.
    # FILING→PAYMENT uses confirm_completed_doc_upload (required-doc gate).
    # PAYMENT→COMPLETED uses mark_payment_received (required-doc + payment check).
    allowed_forward = {
        (FilingStatus.INITIATED, FilingStatus.DOCUMENT_UPLOAD),
        (FilingStatus.COMPUTATION, FilingStatus.PROCESSING),  # Allow requesting more docs
        (FilingStatus.COMPUTATION, FilingStatus.FILING),  # Requires computation approved + tax paid
    }
    is_allowed_forward = (filing.status, body.to_status) in allowed_forward

    # If already in target state, return current filing (idempotent)
    if is_already_in_target:
        client_result = await db.execute(select(User).where(User.id == filing.client_id))
        client = client_result.scalar_one_or_none()
        return FilingResponse(
            id=filing.id,
            client_id=filing.client_id,
            client_name=client.full_name if client else None,
            financial_year=filing.financial_year,
            status=filing.status,
            assigned_executive_id=filing.assigned_executive_id,
            initiated_at=filing.initiated_at,
            onboarding_completed_at=filing.onboarding_completed_at,
            documents_submitted_at=filing.documents_submitted_at,
            documents_approved_at=filing.documents_approved_at,
            computation_uploaded_at=filing.computation_uploaded_at,
            computation_approved_at=filing.computation_approved_at,
            is_tax_paid=filing.is_tax_paid,
            tax_paid_at=filing.tax_paid_at,
            filed_at=filing.filed_at,
            payment_received_at=filing.payment_received_at,
            completed_at=filing.completed_at,
            halted_at=filing.halted_at,
            halt_reason=filing.halt_reason,
            created_at=filing.created_at,
            updated_at=filing.updated_at,
        )

    if not (is_halt or is_resume or is_allowed_forward):
        # Build a helpful message based on what the user tried to do
        transition_hints = {
            (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING): "Use 'Move to Computation' endpoint — Executive/Partner must manually advance when all documents are approved.",
            (FilingStatus.PROCESSING, FilingStatus.COMPUTATION): "Use 'Move to Computation' endpoint — Executive/Partner must manually advance when all documents are approved.",
            (FilingStatus.FILING, FilingStatus.PAYMENT): "Upload all required documents (Acknowledgement, Invoice, ITR JSON, ITR Form) via the completed docs upload.",
            (FilingStatus.PAYMENT, FilingStatus.COMPLETED): "Use 'Mark Payment Received' to complete the filing.",
        }
        hint = transition_hints.get((filing.status, body.to_status), "")
        detail = f"Cannot transition from {filing.status.value} to {body.to_status.value} via the generic endpoint."
        if hint:
            detail += f" {hint}"
        else:
            detail += " This transition must use its dedicated endpoint."
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )

    # ── Prerequisite check: COMPUTATION → FILING requires approved computation + tax paid ──
    if filing.status == FilingStatus.COMPUTATION and body.to_status == FilingStatus.FILING:
        from app.enums import ComputationStatus
        from app.models.filing_computation import FilingComputation

        # Check for a client-approved computation
        approved_comp_result = await db.execute(
            select(FilingComputation).where(
                FilingComputation.filing_id == filing.id,
                FilingComputation.status.in_([ComputationStatus.CLIENT_APPROVED, ComputationStatus.APPROVED]),
            )
        )
        approved_comp = approved_comp_result.scalar_one_or_none()
        if not approved_comp:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot advance to FILING: No approved computation found. "
                       "The client must approve the computation first.",
            )

        # Check tax payment confirmation
        if not filing.is_tax_paid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot advance to FILING: Tax payment has not been confirmed by the client. "
                       "The client must confirm tax payment before the filing can advance.",
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
        is_tax_paid=filing.is_tax_paid,
        tax_paid_at=filing.tax_paid_at,
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
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Halt a filing (Partner, Manager, or Executive action)."""
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
        is_tax_paid=filing.is_tax_paid,
        tax_paid_at=filing.tax_paid_at,
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

    # BRD: Client submits from DOCUMENT_UPLOAD (first time) or DOCUMENT_UPLOAD again (after rejection loop)
    if filing.status not in {FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Documents can only be submitted while the filing is in DOCUMENT_UPLOAD or PROCESSING, not {filing.status.value}. "
                   f"Please wait for the appropriate stage before submitting documents.",
        )

    # Enforce: at least one document placeholder must exist
    from app.models.filing_document import FilingDocument
    from app.enums import DocumentStatus
    doc_counts_result = await db.execute(
        select(
            func.count(FilingDocument.id).label("total"),
            func.count(FilingDocument.id).filter(
                FilingDocument.status.in_([DocumentStatus.UPLOADED, DocumentStatus.APPROVED])
            ).label("ready"),
            func.count(FilingDocument.id).filter(
                FilingDocument.status == DocumentStatus.PENDING_UPLOAD
            ).label("pending"),
        ).where(FilingDocument.filing_id == filing_id)
    )
    doc_counts = doc_counts_result.one()

    if (doc_counts.total or 0) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No document placeholders have been assigned yet. The Executive/Partner must assign documents before you can submit.",
        )

    if (doc_counts.ready or 0) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No documents have been uploaded yet. Please upload your documents before submitting. "
                   f"{doc_counts.pending} document(s) are still pending upload.",
        )

    if (doc_counts.pending or 0) > 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot submit: {doc_counts.pending} document(s) are still pending upload. "
                   f"Please upload all required documents before submitting.",
        )

    from datetime import datetime
    filing.documents_submitted_at = datetime.utcnow()

    # Filing stays in DOCUMENT_UPLOAD — it only moves to PROCESSING once
    # the Executive/Partner has approved ALL documents.

    # Notify partner + executive on every submission (initial or re-submission after rejection)
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Documents Submitted for Review",
            message=f"{current_user.full_name} has submitted documents for FY {filing.financial_year}. Please review and approve.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/documents",
            cta_label="Review Documents",
        )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title="Documents Submitted for Review",
            message=f"{current_user.full_name} has submitted documents for FY {filing.financial_year}. Please review and approve.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/documents",
            cta_label="Review Documents",
        )

    return {"message": "Documents submitted for review", "status": filing.status.value}


# ─── POST /filings/{filing_id}/mark-payment ─────────────────
@router.post("/{filing_id}/mark-payment", response_model=dict)
async def mark_payment_received(
    filing_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Mark payment as received (Manager/Partner action). Transitions to COMPLETED."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Must be in PAYMENT state
    if filing.status != FilingStatus.PAYMENT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot mark payment received: Filing is in '{filing.status.value}' state, not PAYMENT. "
                   f"The filing must be in PAYMENT state before payment can be marked as received.",
        )

    # Verify all required completed docs exist before allowing COMPLETED
    from app.enums import CompletedDocType
    from app.models.filing_completed_doc import FilingCompletedDoc

    required_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE, CompletedDocType.ITR_JSON, CompletedDocType.ITR_FORM}
    existing_result = await db.execute(
        select(FilingCompletedDoc.doc_type).where(FilingCompletedDoc.filing_id == filing_id)
    )
    existing_types = {row[0] for row in existing_result.all()}
    missing = required_types - existing_types
    if missing:
        missing_names = [t.value for t in missing]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot mark payment as completed. The following documents are still missing: {', '.join(missing_names)}",
        )

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
        message=f"Congratulations! Your ITR filing for FY {filing.financial_year} has been completed successfully. All documents including acknowledgement and invoice are now available for download.",
        related_filing_id=filing.id,
        financial_year=filing.financial_year,
        filing_status="COMPLETED",
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}",
        cta_label="View Filing & Download Documents",
    )

    return {"message": "Payment received. Filing marked as completed.", "status": filing.status.value}


# ─── POST /filings/{filing_id}/move-to-computation ───────────
@router.post("/{filing_id}/move-to-computation", response_model=dict)
async def move_to_computation(
    filing_id: UUID,
    request: Request,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Manually advance filing from Document Upload phase to Computation (Manager/Executive/Partner action)."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Must be in DOCUMENT_UPLOAD or PROCESSING state
    if filing.status not in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot move to computation: Filing is in '{filing.status.value}' state. "
                   f"The filing must be in DOCUMENT_UPLOAD or PROCESSING state.",
        )

    # Validate all documents are approved
    from app.services.document_service import check_all_documents_approved

    all_approved = await check_all_documents_approved(db, filing_id)
    if not all_approved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot move to computation: Not all documents are approved yet. "
                   "Please approve all documents before advancing.",
        )

    # Transition DOCUMENT_UPLOAD → PROCESSING → COMPUTATION
    if filing.status == FilingStatus.DOCUMENT_UPLOAD:
        filing = await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.PROCESSING,
            changed_by=current_user.id,
            remarks="Executive advanced to processing",
            ip_address=request.client.host if request.client else None,
        )

    # PROCESSING → COMPUTATION
    filing = await transition_filing_status(
        db=db,
        filing=filing,
        to_status=FilingStatus.COMPUTATION,
        changed_by=current_user.id,
        remarks="Executive moved filing to computation",
        ip_address=request.client.host if request.client else None,
    )

    # Notify client
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Filing Advanced to Computation",
        message=f"Your ITR filing for FY {filing.financial_year} has advanced to the computation phase. Our team is now preparing your tax computation. You will be notified once it is ready for your review.",
        related_filing_id=filing.id,
        financial_year=filing.financial_year,
        filing_status="COMPUTATION",
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}",
        cta_label="View Filing Progress",
    )

    return {"message": "Filing moved to computation.", "status": filing.status.value}


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
