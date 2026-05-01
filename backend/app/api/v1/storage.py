"""API v1 — Storage / file management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_client_access
from app.core.security import get_current_user
from app.database import get_db
from app.enums import CompletedDocType, FilingStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_completed_doc import FilingCompletedDoc
from app.models.stored_file import StoredFile
from app.models.user import User
from app.services.storage_service import generate_pan_object_key, get_presigned_download_url, get_presigned_upload_url

router = APIRouter()


# ─── POST /storage/pan-upload-url ────────────────────────────
@router.post("/pan-upload-url", response_model=dict)
async def get_pan_upload_url(
    filename: str = Query(...),
    content_type: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL for PAN document upload during registration."""
    object_key = generate_pan_object_key(str(current_user.id), filename)
    upload_url = get_presigned_upload_url(object_key, content_type)

    return {
        "upload_url": upload_url,
        "object_key": object_key,
    }


# ─── POST /storage/confirm-pan-upload ────────────────────────
@router.post("/confirm-pan-upload", response_model=dict)
async def confirm_pan_upload(
    object_key: str = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    file_size: int = Query(..., gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm PAN document upload and link it to user record."""
    from app.config import settings

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

    # Link to user
    current_user.pan_document_id = stored_file.id
    await db.flush()

    return {"message": "PAN document uploaded successfully", "file_id": str(stored_file.id)}


# ─── POST /storage/completed-doc/upload-url ──────────────────
@router.post("/completed-doc/upload-url", response_model=dict)
async def get_completed_doc_upload_url(
    filing_id: UUID = Query(...),
    doc_type: CompletedDocType = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get upload URL for ITR Acknowledgement or Invoice (Executive/Partner)."""
    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    from app.services.storage_service import generate_object_key
    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="filed_documents",
        filename=filename,
    )

    upload_url = get_presigned_upload_url(object_key, content_type)

    return {
        "upload_url": upload_url,
        "object_key": object_key,
        "doc_type": doc_type.value,
    }


# ─── POST /storage/completed-doc/confirm ────────────────────
@router.post("/completed-doc/confirm", response_model=dict)
async def confirm_completed_doc_upload(
    filing_id: UUID = Query(...),
    doc_type: CompletedDocType = Query(...),
    object_key: str = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    file_size: int = Query(..., gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm upload of ITR Acknowledgement or Invoice."""
    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    from app.config import settings
    from app.services.filing_service import transition_filing_status
    from app.services.notification_service import create_notification
    from app.services.audit_service import record_audit_event
    from app.enums import AuditEventType

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Create stored file
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

    # Create completed doc record
    completed_doc = FilingCompletedDoc(
        filing_id=filing_id,
        doc_type=doc_type,
        file_id=stored_file.id,
        uploaded_by=current_user.id,
    )
    db.add(completed_doc)

    # If ITR Acknowledgement → transition to PAYMENT
    if doc_type == CompletedDocType.ITR_ACKNOWLEDGEMENT and filing.status == FilingStatus.FILING:
        await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.PAYMENT,
            changed_by=current_user.id,
            remarks="ITR filed — acknowledgement uploaded",
        )

        await record_audit_event(
            db=db,
            event_type=AuditEventType.ITR_FILED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
        )

        await create_notification(
            db=db,
            user_id=filing.client_id,
            title="ITR Filed Successfully",
            message=f"Your ITR for {filing.financial_year} has been filed. Please complete payment.",
            related_filing_id=filing_id,
        )

    elif doc_type == CompletedDocType.INVOICE:
        await record_audit_event(
            db=db,
            event_type=AuditEventType.INVOICE_UPLOADED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
        )

        await create_notification(
            db=db,
            user_id=filing.client_id,
            title="Invoice Available",
            message=f"Your invoice for {filing.financial_year} filing is now available.",
            related_filing_id=filing_id,
        )

    await db.flush()
    return {"message": f"{doc_type.value} uploaded successfully", "file_id": str(stored_file.id)}


# ─── GET /storage/{file_id}/download-url ─────────────────────
@router.get("/{file_id}/download-url", response_model=dict)
async def get_file_download_url(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed download URL for any stored file."""
    result = await db.execute(select(StoredFile).where(StoredFile.id == file_id))
    stored_file = result.scalar_one_or_none()
    if not stored_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    download_url = get_presigned_download_url(stored_file.object_key, filename=stored_file.original_filename)

    return {
        "download_url": download_url,
        "filename": stored_file.original_filename,
        "content_type": stored_file.content_type,
        "file_size": stored_file.file_size_bytes,
    }
