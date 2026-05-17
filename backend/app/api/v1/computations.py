"""API v1 — Computation workflow endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_filing_access
from app.core.security import get_current_active_client, get_current_executive_or_partner, get_current_user
from app.database import get_db
from app.enums import AuditEventType, ComputationStatus, FilingStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_computation import FilingComputation
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.computation import (
    ComputationApproveRequest,
    ComputationListResponse,
    ComputationRejectRequest,
    ComputationResponse,
    ComputationUploadRequest,
    ComputationUploadURLResponse,
)
from app.services.audit_service import record_audit_event
from app.services.filing_service import transition_filing_status
from app.services.notification_service import create_notification
from app.services.storage_service import generate_object_key, get_presigned_download_url, get_presigned_upload_url

router = APIRouter()


# ─── POST /computations/upload-url ──────────────────────────
@router.post("/upload-url", response_model=ComputationUploadURLResponse)
async def get_computation_upload_url(
    body: ComputationUploadRequest,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL to upload a computation document (Executive/Partner)."""
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
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Confirm computation upload after file is in MinIO."""
    from app.config import settings
    from datetime import datetime

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

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

    # Notify client
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Computation Ready",
        message="Your tax computation is ready for review.",
        related_filing_id=filing_id,
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
            approved_by=comp.approved_by,
            approved_at=comp.approved_at,
            rejected_by=comp.rejected_by,
            rejected_at=comp.rejected_at,
            rejection_reason=comp.rejection_reason,
        )
        items.append(item)

        if comp.status in (ComputationStatus.UPLOADED, ComputationStatus.APPROVED):
            if current_version is None:
                current_version = item

    return ComputationListResponse(items=items, current_version=current_version)


# ─── POST /computations/approve ─────────────────────────────
@router.post("/approve", response_model=dict)
async def approve_computation(
    body: ComputationApproveRequest,
    request: Request,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client approves the computation. Transitions filing to FILING."""
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
            detail=f"Cannot approve computation: Filing is in '{filing.status.value}' state, not COMPUTATION. "
                   f"The computation can only be approved when the filing is in COMPUTATION state.",
        )

    # Validate computation is in UPLOADED status (not already APPROVED or SUPERSEDED)
    if computation.status != ComputationStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve computation: Computation is in '{computation.status.value}' status. "
                   f"Only computations with 'UPLOADED' status can be approved.",
        )

    # Verify there is at least one active (UPLOADED) computation
    active_comps_result = await db.execute(
        select(func.count()).select_from(FilingComputation).where(
            FilingComputation.filing_id == filing.id,
            FilingComputation.status == ComputationStatus.UPLOADED,
        )
    )
    active_count = active_comps_result.scalar() or 0
    if active_count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No active computation found to approve. The Executive/Partner must upload a computation first.",
        )

    # Approve computation
    from datetime import datetime
    computation.status = ComputationStatus.APPROVED
    computation.approved_by = current_user.id
    computation.approved_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.COMPUTATION_APPROVED,
        actor_id=current_user.id,
        client_id=current_user.id,
        filing_id=filing.id,
        document_id=computation.id,
    )

    # Transition filing to FILING
    await transition_filing_status(
        db=db,
        filing=filing,
        to_status=FilingStatus.FILING,
        changed_by=current_user.id,
        remarks="Computation approved by client",
        ip_address=request.client.host if request.client else None,
    )

    # Notify Partner + Executive
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Computation Approved",
            message=f"Computation approved by {current_user.full_name} for {filing.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title="Computation Approved",
            message=f"Computation approved by {current_user.full_name} for {filing.financial_year}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    return {"message": "Computation approved. Filing moved to FILING state."}


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

    # Validate computation is in UPLOADED status
    if computation.status != ComputationStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject computation: Computation is in '{computation.status.value}' status. "
                   f"Only computations with 'UPLOADED' status can be rejected.",
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

    # Notify Partner + assigned Executive
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Computation Rejected",
            message=f"Computation (v{computation.version}) rejected by {current_user.full_name} "
                    f"for {filing.financial_year}. Reason: {body.reason}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    if filing.assigned_executive_id:
        await create_notification(
            db=db,
            user_id=filing.assigned_executive_id,
            title="Computation Rejected",
            message=f"Computation (v{computation.version}) rejected by {current_user.full_name} "
                    f"for {filing.financial_year}. Reason: {body.reason}",
            related_filing_id=filing.id,
            related_client_id=current_user.id,
        )

    return {
        "message": "Computation rejected. The Executive/Partner can upload a revised computation.",
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
