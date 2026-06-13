"""API v1 — Computation workflow endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.file_validation import sanitize_filename, validate_file_size, validate_file_type
from app.core.permissions import enforce_filing_access
from app.core.security import (
    get_current_active_client,
    get_current_manager_executive_or_partner,
    get_current_manager_or_partner,
    get_current_user,
)
from app.database import get_db
from app.enums import AuditEventType, ComputationStatus, FilingStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_computation import FilingComputation
from app.models.manager_executive_assignment import ManagerExecutiveAssignment
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.computation import (
    ComputationApproveRequest,
    ComputationListResponse,
    ComputationManagerApproveRequest,
    ComputationManagerRejectRequest,
    ComputationPartnerApproveRequest,
    ComputationPartnerRejectRequest,
    ComputationRejectRequest,
    ComputationResponse,
    ComputationUploadRequest,
    ComputationUploadURLResponse,
)
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification, notify_partner_and_manager
from app.services.storage_service import generate_object_key, get_presigned_download_url, get_presigned_upload_url, validate_object_key_prefix

router = APIRouter()


# ─── POST /computations/upload-url ──────────────────────────
@router.post("/upload-url", response_model=ComputationUploadURLResponse)
async def get_computation_upload_url(
    body: ComputationUploadRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL to upload a computation document (Executive/Partner)."""
    # Validate file type
    body.filename = sanitize_filename(body.filename)
    validate_file_type(body.filename, body.content_type)

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == body.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Computation can only be uploaded in COMPUTATION state (or FILING for revisions)
    if filing.status not in (FilingStatus.COMPUTATION, FilingStatus.FILING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload computation: Filing is in '{filing.status.value}' state. "
                   f"Computation can only be uploaded when the filing is in COMPUTATION state "
                   f"(all documents must be approved first).",
        )

    # Fetch client name for readable MinIO path
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    # Determine next version number
    version_result = await db.execute(
        select(FilingComputation)
        .where(FilingComputation.filing_id == body.filing_id)
        .order_by(FilingComputation.version.desc())
    )
    latest = version_result.scalars().first()
    next_version = (latest.version + 1) if latest else 1

    # Mark previous versions as SUPERSEDED
    if latest and latest.status == ComputationStatus.UPLOADED:
        latest.status = ComputationStatus.SUPERSEDED
        await record_audit_event(
            db=db,
            event_type=AuditEventType.COMPUTATION_SUPERSEDED,
            actor_id=current_user.id,
            filing_id=body.filing_id,
            document_id=latest.id,
        )

    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="computation",
        filename=body.filename,
        client_name=client_user.full_name if client_user else "",
    )

    upload_url = get_presigned_upload_url(object_key, body.content_type)

    return ComputationUploadURLResponse(
        upload_url=upload_url,
        computation_id=None,  # Created after the upload is confirmed
        version=next_version,
        object_key=object_key,
    )


# ─── POST /computations/confirm-upload ──────────────────────
@router.post("/confirm-upload", response_model=ComputationResponse)
async def confirm_computation_upload(
    filing_id: UUID,
    object_key: str,
    filename: str,
    content_type: str,
    file_size: int,
    version: int,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Confirm computation upload after file is in MinIO."""
    # Validate file type and size
    filename = sanitize_filename(filename)
    validate_file_type(filename, content_type)
    validate_file_size(file_size)

    from app.config import settings
    from datetime import datetime

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Validate object_key belongs to this client's computation folder
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()
    try:
        validate_object_key_prefix(
            object_key, str(filing.client_id),
            client_user.full_name if client_user else "",
            f"ITR-{filing.financial_year}/computation",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Create stored file record
    stored_file = StoredFile(
        bucket=settings.MINIO_BUCKET_NAME,
        object_key=object_key,
        original_filename=filename,
        content_type=content_type,
        file_size_bytes=file_size,
        uploaded_by=current_user.id,
    )
    db.add(stored_file)
    await db.flush()

    # Create computation record
    computation = FilingComputation(
        filing_id=filing_id,
        version=version,
        file_id=stored_file.id,
        status=ComputationStatus.UPLOADED,
        uploaded_by=current_user.id,
    )
    db.add(computation)

    await db.flush()

    # Update filing timestamp
    filing.computation_uploaded_at = datetime.utcnow()
    filing.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_UPLOADED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing_id,
        document_id=computation.id,
        details={"version": version, "filename": filename},
    )

    # Notify manager for approval (if executive uploaded, notify their manager)
    # If manager or partner uploaded, notify partner directly
    if current_user.role == UserRole.EXECUTIVE:
        # Find the manager for this executive
        mgr_assignment_result = await db.execute(
            select(ManagerExecutiveAssignment).where(
                ManagerExecutiveAssignment.executive_id == current_user.id,
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        mgr_assignment = mgr_assignment_result.scalar_one_or_none()
        if mgr_assignment:
            _client_label = client_user.full_name if client_user else 'Unknown'
            await create_notification(
                db=db,
                user_id=mgr_assignment.manager_id,
                title=f"{_client_label} — Computation Uploaded — Review Required",
                message=f"A computation (v{version}) has been uploaded by {current_user.full_name} for client {_client_label}, FY {filing.financial_year}. Please review and approve or reject.",
                related_filing_id=filing_id,
                client_name=client_user.full_name if client_user else None,
                financial_year=filing.financial_year,
                action_by=current_user.full_name,
                action_url_path=f"/filings/{filing_id}/computation",
                cta_label="Review Computation",
                extra_details={"Version": version, "Filename": filename},
            )
        # No manager — notification skipped (per platform policy)
    elif current_user.role == UserRole.MANAGER:
        # Manager uploaded — notify partner
        partner_result = await db.execute(
            select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
        )
        partner = partner_result.scalars().first()
        if partner:
            _client_label = client_user.full_name if client_user else 'Unknown'
            await create_notification(
                db=db,
                user_id=partner.id,
                title=f"{_client_label} — Computation Uploaded — Review Required",
                message=f"A computation (v{version}) has been uploaded by Manager {current_user.full_name} for client {_client_label}, FY {filing.financial_year}. Please review and approve.",
                related_filing_id=filing_id,
                client_name=client_user.full_name if client_user else None,
                financial_year=filing.financial_year,
                action_by=current_user.full_name,
                action_url_path=f"/filings/{filing_id}/computation",
                cta_label="Review Computation",
                extra_details={"Version": version, "Filename": filename},
            )

    return ComputationResponse(
        id=computation.id,
        filing_id=computation.filing_id,
        version=computation.version,
        file_id=computation.file_id,
        original_filename=filename,
        status=computation.status,
        uploaded_by=computation.uploaded_by,
        uploaded_by_name=current_user.full_name,
        uploaded_at=computation.uploaded_at,
    )


# ─── GET /computations/filing/{filing_id} ───────────────────
@router.get("/filing/{filing_id}", response_model=ComputationListResponse)
async def get_filing_computations(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all computation versions for a filing."""
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    result = await db.execute(
        select(FilingComputation)
        .where(FilingComputation.filing_id == filing_id)
        .order_by(FilingComputation.version.desc())
    )
    computations = result.scalars().all()

    items = []
    current_version = None
    for comp in computations:
        # Get filename
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == comp.file_id))
        stored = file_result.scalar_one_or_none()

        # Get uploader name
        uploader_result = await db.execute(select(User).where(User.id == comp.uploaded_by))
        uploader = uploader_result.scalar_one_or_none()

        item = ComputationResponse(
            id=comp.id,
            filing_id=comp.filing_id,
            version=comp.version,
            file_id=comp.file_id,
            original_filename=stored.original_filename if stored else None,
            status=comp.status,
            uploaded_by=comp.uploaded_by,
            uploaded_by_name=uploader.full_name if uploader else None,
            uploaded_at=comp.uploaded_at,
            manager_approved_by=comp.manager_approved_by,
            manager_approved_at=comp.manager_approved_at,
            manager_rejected_by=comp.manager_rejected_by,
            manager_rejected_at=comp.manager_rejected_at,
            manager_rejection_reason=comp.manager_rejection_reason,
            partner_approved_by=comp.partner_approved_by,
            partner_approved_at=comp.partner_approved_at,
            approved_by=comp.approved_by,
            approved_at=comp.approved_at,
            rejected_by=comp.rejected_by,
            rejected_at=comp.rejected_at,
            rejection_reason=comp.rejection_reason,
        )
        items.append(item)

        if comp.status in (
            ComputationStatus.UPLOADED,
            ComputationStatus.MANAGER_APPROVED,
            ComputationStatus.PARTNER_APPROVED,
            ComputationStatus.CLIENT_APPROVED,
            ComputationStatus.APPROVED,
        ):
            if current_version is None:
                current_version = item

    # Check if internal working docs exist for this filing (active only — exclude superseded versions)
    from app.models.internal_working_doc import InternalWorkingDoc
    iw_count = await db.scalar(
        select(func.count()).select_from(InternalWorkingDoc)
        .where(
            InternalWorkingDoc.filing_id == filing_id,
            InternalWorkingDoc.superseded_at.is_(None),
        )
    )

    return ComputationListResponse(
        items=items,
        current_version=current_version,
        has_internal_workings=bool(iw_count and iw_count > 0),
    )


# ─── POST /computations/approve ─────────────────────────────
@router.post("/approve", response_model=dict)
async def approve_computation(
    body: ComputationApproveRequest,
    request: Request,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """
    Client approves the computation and/or confirms tax payment.

    Two scenarios:
    - Computation is UPLOADED: approve it + optionally confirm tax paid.
    - Computation is already APPROVED but is_tax_paid is False on the filing:
      allow client to call again just to confirm tax payment.

    If both computation is approved AND tax is paid, filing auto-transitions to FILING.
    """
    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    from datetime import datetime

    # ── Scenario B: Computation already approved, client is confirming tax payment ──
    if computation.status == ComputationStatus.CLIENT_APPROVED:
        if filing.is_tax_paid:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Computation is already approved and tax payment is already confirmed. "
                       "No further action needed from you.",
            )
        if not body.is_tax_paid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Computation is already approved. Please set is_tax_paid to true to confirm tax payment.",
            )

        # Confirm tax payment
        filing.is_tax_paid = True
        filing.tax_paid_at = datetime.utcnow()
        filing.updated_by = current_user.id

        await record_audit_event(
            db=db,
            event_type=AuditEventType.COMPUTATION_APPROVED,
            actor_id=current_user.id,
            client_id=current_user.id,
            filing_id=filing.id,
            document_id=computation.id,
            details={"action": "tax_payment_confirmed"},
        )

        # Auto-transition filing to FILING state
        if filing.status == FilingStatus.COMPUTATION:
            # Check mandatory internal working docs (active only)
            from app.models.internal_working_doc import InternalWorkingDoc
            iw_count = await db.scalar(
                select(func.count()).select_from(InternalWorkingDoc)
                .where(
                    InternalWorkingDoc.filing_id == filing.id,
                    InternalWorkingDoc.superseded_at.is_(None),
                )
            )
            if not iw_count:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot advance to FILING: At least one Internal Working document must be uploaded before filing can proceed.",
                )
            from app.services.filing_service import transition_filing_status
            filing = await transition_filing_status(
                db=db,
                filing=filing,
                to_status=FilingStatus.FILING,
                changed_by=current_user.id,
            )

        # Notify Partner + Executive that filing has advanced
        # Notify Partner + Manager — Scenario B (tax paid on already-approved computation)
        await notify_partner_and_manager(
            db=db,
            client_id=current_user.id,
            title="Tax Payment Confirmed — Filing Advanced",
            message=f"Tax payment has been confirmed by {current_user.full_name} for FY {filing.financial_year}. Filing has automatically advanced to FILING state.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            filing_status="FILING",
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}",
            cta_label="View Filing",
        )

        if filing.assigned_executive_id:
            await create_notification(
                db=db,
                user_id=filing.assigned_executive_id,
                title=f"{current_user.full_name} — Tax Payment Confirmed — Filing Advanced",
                message=f"Tax payment has been confirmed by {current_user.full_name} for FY {filing.financial_year}. Filing has automatically advanced to FILING state. Please upload the required filed documents.",
                related_filing_id=filing.id,
                related_client_id=current_user.id,
                client_name=current_user.full_name,
                financial_year=filing.financial_year,
                filing_status="FILING",
                action_by=current_user.full_name,
                action_url_path=f"/filings/{filing.id}/completed-docs",
                cta_label="Upload Filed Documents",
            )

        return {
            "message": "Tax payment confirmed. Filing has automatically advanced to FILING state.",
            "is_tax_paid": True,
            "filing_status": filing.status.value,
        }

    # ── Scenario A: Normal approval flow (computation is PARTNER_APPROVED) ──

    # Validate filing is in COMPUTATION state
    if filing.status != FilingStatus.COMPUTATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve computation: Filing is in '{filing.status.value}' state, not COMPUTATION. "
                   f"The computation can only be approved when the filing is in COMPUTATION state.",
        )

    # Validate computation is in PARTNER_APPROVED status (internal approvals done)
    if computation.status != ComputationStatus.PARTNER_APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve computation: Computation is in '{computation.status.value}' status. "
                   f"Only computations with 'PARTNER_APPROVED' status can be approved by client.",
        )

    # Approve computation (client final approval)
    computation.status = ComputationStatus.CLIENT_APPROVED
    computation.approved_by = current_user.id
    computation.approved_at = datetime.utcnow()

    # Set computation_approved_at milestone on filing
    filing.computation_approved_at = datetime.utcnow()
    filing.updated_by = current_user.id

    # Handle tax payment confirmation
    if body.is_tax_paid:
        filing.is_tax_paid = True
        filing.tax_paid_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_APPROVED,
        actor_id=current_user.id,
        client_id=current_user.id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"is_tax_paid": body.is_tax_paid},
    )

    # Auto-transition filing to FILING state if tax is paid
    if body.is_tax_paid and filing.status == FilingStatus.COMPUTATION:
        # Check mandatory internal working docs (active only)
        from app.models.internal_working_doc import InternalWorkingDoc
        iw_count = await db.scalar(
            select(func.count()).select_from(InternalWorkingDoc)
            .where(
                InternalWorkingDoc.filing_id == filing.id,
                InternalWorkingDoc.superseded_at.is_(None),
            )
        )
        if not iw_count:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot advance to FILING: At least one Internal Working document must be uploaded before filing can proceed.",
            )
        from app.services.filing_service import transition_filing_status
        filing = await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.FILING,
            changed_by=current_user.id,
        )

    # Notify Partner + Executive
    if body.is_tax_paid:
        notif_title = "Computation Approved & Tax Paid — Filing Advanced"
        notif_message = (
            f"Computation approved and tax payment confirmed by {current_user.full_name} "
            f"for {filing.financial_year}. Filing has automatically advanced to FILING state."
        )
    else:
        notif_title = "Computation Approved"
        notif_message = (
            f"Computation approved by {current_user.full_name} for {filing.financial_year}. "
            f"Awaiting tax payment confirmation from client before filing can advance."
        )

    # Notify Partner + Manager — Scenario A (normal client approval)
    await notify_partner_and_manager(
        db=db,
        client_id=current_user.id,
        title=notif_title,
        message=notif_message,
        related_filing_id=filing.id,
        related_client_id=current_user.id,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        filing_status=filing.status.value,
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}",
        cta_label="View Filing",
    )

    # If professional fee is not yet set, send a dedicated notification to Partner
    if not filing.professional_fee and not filing.no_fees_applicable:
        from app.models.user import User as UserModel
        partner_result = await db.execute(
            select(UserModel).where(UserModel.role == UserRole.PARTNER, UserModel.is_active == True).limit(1)
        )
        partner_user = partner_result.scalar_one_or_none()
        if partner_user:
            await create_notification(
                db=db,
                user_id=partner_user.id,
                title=f"{current_user.full_name} — Set Professional Fee",
                message=f"Computation approved by {current_user.full_name} for FY {filing.financial_year}. "
                        f"Professional fee has not been set yet. Please set the fee to generate the revised engagement letter.",
                related_filing_id=filing.id,
                related_client_id=current_user.id,
                client_name=current_user.full_name,
                financial_year=filing.financial_year,
                action_by=current_user.full_name,
                action_url_path=f"/filings/{filing.id}",
                cta_label="Set Fee",
            )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title=f"{current_user.full_name} — {notif_title}",
            message=notif_message,
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            filing_status=filing.status.value,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}",
            cta_label="View Filing",
        )

    if body.is_tax_paid:
        return {
            "message": "Computation approved and tax payment confirmed. "
                       "Filing has automatically advanced to FILING state.",
            "is_tax_paid": True,
            "filing_status": filing.status.value,
        }
    else:
        return {
            "message": "Computation approved. Please confirm tax payment to allow filing to advance.",
            "is_tax_paid": False,
            "filing_status": filing.status.value,
        }


# ─── POST /computations/reject ──────────────────────────────
@router.post("/reject", response_model=dict)
async def reject_computation(
    body: ComputationRejectRequest,
    request: Request,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client rejects the computation. Filing stays in COMPUTATION state for re-upload."""
    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    if filing.client_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your filing")

    # Validate filing is in COMPUTATION state
    if filing.status != FilingStatus.COMPUTATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject computation: Filing is in '{filing.status.value}' state, not COMPUTATION.",
        )

    # Validate computation is in PARTNER_APPROVED status (client can only reject after partner approved)
    if computation.status != ComputationStatus.PARTNER_APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject computation: Computation is in '{computation.status.value}' status. "
                   f"Only computations with 'PARTNER_APPROVED' status can be rejected by client.",
        )

    # Reject computation
    from datetime import datetime
    computation.status = ComputationStatus.REJECTED
    computation.rejected_by = current_user.id
    computation.rejected_at = datetime.utcnow()
    computation.rejection_reason = body.reason

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_REJECTED,
        actor_id=current_user.id,
        client_id=current_user.id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"version": computation.version, "reason": body.reason},
    )

    # Notify Partner + Manager about client rejection
    await notify_partner_and_manager(
        db=db,
        client_id=current_user.id,
        title="Computation Rejected by Client",
        message=f"Computation (v{computation.version}) has been rejected by {current_user.full_name} for FY {filing.financial_year}. A revised computation needs to be uploaded.",
        related_filing_id=filing.id,
        related_client_id=current_user.id,
        client_name=current_user.full_name,
        financial_year=filing.financial_year,
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}/computation/upload",
        cta_label="Upload Revised Computation",
        extra_details={"Version Rejected": computation.version, "Reason": body.reason},
    )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title=f"{current_user.full_name} — Computation Rejected by Client",
            message=f"Computation (v{computation.version}) has been rejected by {current_user.full_name} for FY {filing.financial_year}. Please upload a revised computation.",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
            client_name=current_user.full_name,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/computation/upload",
            cta_label="Upload Revised Computation",
            extra_details={"Version Rejected": computation.version, "Reason": body.reason},
        )

    return {
        "message": "Computation rejected. The Executive/Partner can upload a revised computation.",
        "version_rejected": computation.version,
    }


# ─── POST /computations/manager-approve ─────────────────────
@router.post("/manager-approve", response_model=dict)
async def manager_approve_computation(
    body: ComputationManagerApproveRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Manager approves computation (first level of internal approval).
    Moves computation from UPLOADED → MANAGER_APPROVED.
    After this, Partner must still approve before client sees it.
    """
    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    # Validate access — Manager must manage the executive assigned to this filing
    await enforce_filing_access(db, current_user, filing.client_id)

    if computation.status != ComputationStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve: Computation is in '{computation.status.value}' status. "
                   f"Only 'UPLOADED' computations can be manager-approved.",
        )

    from datetime import datetime
    computation.status = ComputationStatus.MANAGER_APPROVED
    computation.manager_approved_by = current_user.id
    computation.manager_approved_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_MANAGER_APPROVED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"version": computation.version},
    )

    # Notify Partner that computation is ready for final approval
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalars().first()
    if partner:
        # Fetch client name for notification title
        _client_result = await db.execute(select(User.full_name).where(User.id == filing.client_id))
        _client_name = _client_result.scalar() or "Client"
        await create_notification(
            db=db,
            user_id=partner.id,
            title=f"{_client_name} — Computation Manager-Approved — Partner Review Needed",
            message=f"Computation (v{computation.version}) has been approved by Manager {current_user.full_name} for FY {filing.financial_year}. Please review and approve to send to client.",
            related_filing_id=filing.id,
            related_client_id=filing.client_id,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/computation",
            cta_label="Review & Approve",
            extra_details={"Version": computation.version},
        )

    return {
        "message": "Computation approved by manager. Awaiting partner approval before sending to client.",
        "status": ComputationStatus.MANAGER_APPROVED.value,
    }


# ─── POST /computations/manager-reject ──────────────────────
@router.post("/manager-reject", response_model=dict)
async def manager_reject_computation(
    body: ComputationManagerRejectRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Manager rejects computation back to executive for revision.
    Moves computation from UPLOADED → MANAGER_REJECTED.
    """
    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    await enforce_filing_access(db, current_user, filing.client_id)

    if computation.status != ComputationStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject: Computation is in '{computation.status.value}' status. "
                   f"Only 'UPLOADED' computations can be manager-rejected.",
        )

    from datetime import datetime
    computation.status = ComputationStatus.MANAGER_REJECTED
    computation.manager_rejected_by = current_user.id
    computation.manager_rejected_at = datetime.utcnow()
    computation.manager_rejection_reason = body.reason

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_MANAGER_REJECTED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"version": computation.version, "reason": body.reason},
    )

    # Notify the executive who uploaded
    # Fetch client name for notification titles
    _client_result = await db.execute(select(User.full_name).where(User.id == filing.client_id))
    _client_name = _client_result.scalar() or "Client"

    if computation.uploaded_by:
        await create_notification(
            db=db,
            user_id=computation.uploaded_by,
            title=f"{_client_name} — Computation Rejected by Manager",
            message=f"Computation (v{computation.version}) for FY {filing.financial_year} has been rejected by Manager {current_user.full_name}. Please review the feedback and upload a revised version.",
            related_filing_id=filing.id,
            related_client_id=filing.client_id,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/computation/upload",
            cta_label="Upload Revised Computation",
            extra_details={"Version Rejected": computation.version, "Reason": body.reason},
        )

    # Notify partner about the rejection
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalars().first()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title=f"{_client_name} — Computation Rejected by Manager",
            message=f"Computation (v{computation.version}) for FY {filing.financial_year} has been rejected by Manager {current_user.full_name}. Executive will upload a revised version.",
            related_filing_id=filing.id,
            related_client_id=filing.client_id,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/computation",
            cta_label="View Computation",
            extra_details={"Version Rejected": computation.version, "Reason": body.reason},
        )

    return {
        "message": "Computation rejected by manager. Executive can upload a revised version.",
        "version_rejected": computation.version,
    }


# ─── POST /computations/partner-approve ─────────────────────
@router.post("/partner-approve", response_model=dict)
async def partner_approve_computation(
    body: ComputationPartnerApproveRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Partner approves computation (final internal approval).
    Can approve from UPLOADED (bypassing manager) or from MANAGER_APPROVED.
    After this, computation becomes visible to client for their approval.
    """
    if current_user.role != UserRole.PARTNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Partner access required")

    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    # Partner can approve from UPLOADED (bypass) or MANAGER_APPROVED
    if computation.status not in (ComputationStatus.UPLOADED, ComputationStatus.MANAGER_APPROVED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve: Computation is in '{computation.status.value}' status. "
                   f"Only 'UPLOADED' or 'MANAGER_APPROVED' computations can be partner-approved.",
        )

    from datetime import datetime
    computation.status = ComputationStatus.PARTNER_APPROVED
    computation.partner_approved_by = current_user.id
    computation.partner_approved_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_PARTNER_APPROVED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"version": computation.version, "bypassed_manager": computation.manager_approved_by is None},
    )

    # Notify client that computation is ready for their review
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Computation Ready for Review",
        message=f"Your tax computation for FY {filing.financial_year} is ready for review. Please review and approve or reject it. If tax payment is required, you will be asked to confirm payment after approval.",
        related_filing_id=filing.id,
        financial_year=filing.financial_year,
        action_by=current_user.full_name,
        action_url_path=f"/filings/{filing.id}/computation",
        cta_label="Review Computation",
    )

    return {
        "message": "Computation approved by partner. Client has been notified to review.",
        "status": ComputationStatus.PARTNER_APPROVED.value,
    }


# ─── POST /computations/partner-reject ──────────────────────
@router.post("/partner-reject", response_model=dict)
async def partner_reject_computation(
    body: ComputationPartnerRejectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Partner rejects computation back for revision.
    Can reject from UPLOADED (bypassing manager) or MANAGER_APPROVED.
    Notifies both the manager and the executive who uploaded.
    """
    if current_user.role != UserRole.PARTNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Partner access required")

    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == body.computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    if computation.status not in (ComputationStatus.UPLOADED, ComputationStatus.MANAGER_APPROVED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject: Computation is in '{computation.status.value}' status. "
                   f"Only 'UPLOADED' or 'MANAGER_APPROVED' computations can be partner-rejected.",
        )

    from datetime import datetime
    computation.status = ComputationStatus.REJECTED
    computation.rejected_by = current_user.id
    computation.rejected_at = datetime.utcnow()
    computation.rejection_reason = body.reason

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_REJECTED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=computation.id,
        details={"version": computation.version, "reason": body.reason, "rejected_by_role": "PARTNER"},
    )

    # Notify the executive who uploaded
    # Fetch client name for notification titles
    _client_result = await db.execute(select(User.full_name).where(User.id == filing.client_id))
    _client_name = _client_result.scalar() or "Client"

    if computation.uploaded_by:
        await create_notification(
            db=db,
            user_id=computation.uploaded_by,
            title=f"{_client_name} — Computation Rejected by Partner",
            message=f"Computation (v{computation.version}) for FY {filing.financial_year} has been rejected by Partner. Please review the feedback and upload a revised computation.",
            related_filing_id=filing.id,
            related_client_id=filing.client_id,
            financial_year=filing.financial_year,
            action_by=current_user.full_name,
            action_url_path=f"/filings/{filing.id}/computation/upload",
            cta_label="Upload Revised Computation",
            extra_details={"Version Rejected": computation.version, "Reason": body.reason},
        )

    # Notify the manager (if executive is under a manager)
    if filing.assigned_executive_id:
        mgr_result = await db.execute(
            select(ManagerExecutiveAssignment.manager_id).where(
                ManagerExecutiveAssignment.executive_id == filing.assigned_executive_id,
                ManagerExecutiveAssignment.is_active == True,
            )
        )
        mgr_row = mgr_result.first()
        if mgr_row:
            await create_notification(
                db=db,
                user_id=mgr_row[0],
                title=f"{_client_name} — Computation Rejected by Partner",
                message=f"Computation (v{computation.version}) for FY {filing.financial_year} has been rejected by Partner. Executive needs to upload a revised version.",
                related_filing_id=filing.id,
                related_client_id=filing.client_id,
                financial_year=filing.financial_year,
                action_by=current_user.full_name,
                action_url_path=f"/filings/{filing.id}/computation",
                cta_label="View Computation",
                extra_details={"Version Rejected": computation.version, "Reason": body.reason},
            )

    return {
        "message": "Computation rejected by partner. Executive and manager have been notified.",
        "version_rejected": computation.version,
    }


# ─── GET /computations/{computation_id}/download-url ─────────
@router.get("/{computation_id}/download-url")
async def get_computation_download_url(
    computation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get download URL for a computation document."""
    comp_result = await db.execute(
        select(FilingComputation).where(FilingComputation.id == computation_id)
    )
    computation = comp_result.scalar_one_or_none()
    if not computation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Computation not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == computation.filing_id))
    filing = filing_result.scalar_one_or_none()

    await enforce_filing_access(db, current_user, filing.client_id)

    file_result = await db.execute(select(StoredFile).where(StoredFile.id == computation.file_id))
    stored_file = file_result.scalar_one_or_none()
    if not stored_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    download_url = get_presigned_download_url(stored_file.object_key, filename=stored_file.original_filename)

    return {
        "download_url": download_url,
        "filename": stored_file.original_filename,
        "content_type": stored_file.content_type,
    }
