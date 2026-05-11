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
    object_key = generate_pan_object_key(str(current_user.id), filename, client_name=current_user.full_name)
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

    # Fetch client name for readable MinIO path
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    from app.services.storage_service import generate_object_key
    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="filed_documents",
        filename=filename,
        client_name=client_user.full_name if client_user else "",
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
    import logging
    logger = logging.getLogger("app")

    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    from app.config import settings
    from app.services.filing_service import transition_filing_status
    from app.services.notification_service import create_notification
    from app.services.audit_service import record_audit_event
    from app.enums import AuditEventType
    from datetime import datetime

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Validate filing state for the doc type
    if doc_type == CompletedDocType.ITR_ACKNOWLEDGEMENT:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload ITR Acknowledgement in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
            )

    if doc_type == CompletedDocType.INVOICE:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload invoice in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
            )

    # Validate ITR_JSON file type
    if doc_type == CompletedDocType.ITR_JSON:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload ITR JSON in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
            )
        if not filename.lower().endswith('.json'):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ITR JSON file must have a .json extension.",
            )
        if content_type not in ('application/json', 'text/json'):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ITR JSON file must have content type 'application/json'.",
            )

    # Reuse existing StoredFile if same bucket/object_key (idempotent retry)
    existing_file_result = await db.execute(
        select(StoredFile).where(
            StoredFile.bucket == settings.MINIO_BUCKET_NAME,
            StoredFile.object_key == object_key,
        )
    )
    stored_file = existing_file_result.scalar_one_or_none()

    if stored_file:
        # Update metadata in case it changed
        stored_file.original_filename = filename
        stored_file.content_type = content_type
        stored_file.file_size_bytes = file_size
        stored_file.uploaded_by = current_user.id
        stored_file.uploaded_at = datetime.utcnow()
        logger.info(f"Reusing existing StoredFile {stored_file.id} for object_key={object_key}")
    else:
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

    # Upsert: replace existing doc if one already exists for this filing + doc_type
    existing_result = await db.execute(
        select(FilingCompletedDoc).where(
            FilingCompletedDoc.filing_id == filing_id,
            FilingCompletedDoc.doc_type == doc_type,
        )
    )
    existing_doc = existing_result.scalar_one_or_none()

    if existing_doc:
        existing_doc.file_id = stored_file.id
        existing_doc.uploaded_by = current_user.id
        existing_doc.uploaded_at = datetime.utcnow()
    else:
        completed_doc = FilingCompletedDoc(
            filing_id=filing_id,
            doc_type=doc_type,
            file_id=stored_file.id,
            uploaded_by=current_user.id,
        )
        db.add(completed_doc)

    await db.flush()

    # Record audit event per doc type
    if doc_type == CompletedDocType.ITR_ACKNOWLEDGEMENT:
        await record_audit_event(
            db=db,
            event_type=AuditEventType.ITR_FILED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
            details={"note": f"Acknowledgement uploaded in {filing.status.value} state"},
        )
    elif doc_type == CompletedDocType.INVOICE:
        await record_audit_event(
            db=db,
            event_type=AuditEventType.INVOICE_UPLOADED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
        )
    elif doc_type == CompletedDocType.ITR_JSON:
        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
            details={"doc_type": "ITR_JSON", "filename": filename},
        )

    # Check if all 3 required completed docs are uploaded — only then transition to PAYMENT
    if filing.status == FilingStatus.FILING:
        required_doc_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE, CompletedDocType.ITR_JSON}
        existing_docs_result = await db.execute(
            select(FilingCompletedDoc.doc_type).where(FilingCompletedDoc.filing_id == filing_id)
        )
        existing_types = {row[0] for row in existing_docs_result.all()}
        missing_types = required_doc_types - existing_types

        if not missing_types:
            # All 3 docs uploaded → transition to PAYMENT
            await transition_filing_status(
                db=db,
                filing=filing,
                to_status=FilingStatus.PAYMENT,
                changed_by=current_user.id,
                remarks="ITR filed - all required documents uploaded (Acknowledgement, Invoice, ITR JSON)",
            )

            await create_notification(
                db=db,
                user_id=filing.client_id,
                title="ITR Filed Successfully",
                message=f"Your ITR for {filing.financial_year} has been filed. Please complete payment.",
                related_filing_id=filing_id,
            )

        await db.flush()
        remaining = [t.value for t in (required_doc_types - existing_types)]
        return {
            "message": f"{doc_type.value} uploaded successfully",
            "file_id": str(stored_file.id),
            "remaining_docs": remaining,
            "all_docs_uploaded": len(remaining) == 0,
        }

    await db.flush()
    return {"message": f"{doc_type.value} uploaded successfully", "file_id": str(stored_file.id)}


# ─── GET /storage/completed-docs/{filing_id} ─────────────────
@router.get("/completed-docs/{filing_id}", response_model=list[dict])
async def get_completed_docs(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get completed docs (acknowledgement, invoice) for a filing.
    Clients can only see these once filing is COMPLETED.
    """
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Clients can only view filed documents after COMPLETED state
    if current_user.role == UserRole.CLIENT:
        if filing.status != FilingStatus.COMPLETED:
            return []
        # Only show Acknowledgement and Invoice to client, not ITR JSON (internal)
        result = await db.execute(
            select(FilingCompletedDoc).where(
                FilingCompletedDoc.filing_id == filing_id,
                FilingCompletedDoc.doc_type.in_([CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE]),
            )
        )
    else:
        result = await db.execute(
            select(FilingCompletedDoc).where(FilingCompletedDoc.filing_id == filing_id)
        )
    docs = result.scalars().all()

    items = []
    for doc in docs:
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
        stored = file_result.scalar_one_or_none()
        items.append({
            "id": str(doc.id),
            "doc_type": doc.doc_type.value,
            "file_id": str(doc.file_id),
            "filename": stored.original_filename if stored else None,
            "content_type": stored.content_type if stored else None,
            "file_size": stored.file_size_bytes if stored else None,
            "uploaded_by": str(doc.uploaded_by),
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        })

    return items


# ─── GET /storage/{file_id}/download-url ─────────────────────
@router.get("/{file_id}/download-url", response_model=dict)
async def get_file_download_url(
    file_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed download URL for any stored file.
    Accepts a StoredFile.id directly, or a FilingCompletedDoc.id / FilingComputation.id
    and resolves to the underlying StoredFile.
    """
    from app.models.filing_computation import FilingComputation

    result = await db.execute(select(StoredFile).where(StoredFile.id == file_id))
    stored_file = result.scalar_one_or_none()

    # If not found directly, try resolving through FilingCompletedDoc
    if not stored_file:
        doc_result = await db.execute(
            select(FilingCompletedDoc).where(FilingCompletedDoc.id == file_id)
        )
        completed_doc = doc_result.scalar_one_or_none()
        if completed_doc:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == completed_doc.file_id))
            stored_file = file_result.scalar_one_or_none()

    # If still not found, try resolving through FilingComputation
    if not stored_file:
        comp_result = await db.execute(
            select(FilingComputation).where(FilingComputation.id == file_id)
        )
        computation = comp_result.scalar_one_or_none()
        if computation:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == computation.file_id))
            stored_file = file_result.scalar_one_or_none()

    if not stored_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    download_url = get_presigned_download_url(stored_file.object_key, filename=stored_file.original_filename)

    return {
        "download_url": download_url,
        "filename": stored_file.original_filename,
        "content_type": stored_file.content_type,
        "file_size": stored_file.file_size_bytes,
    }
