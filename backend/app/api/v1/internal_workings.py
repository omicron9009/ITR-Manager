"""API v1 — Internal Working document endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.file_validation import sanitize_filename, validate_file_size, validate_file_type
from app.core.permissions import enforce_filing_access
from app.core.security import get_current_manager_executive_or_partner
from app.database import get_db
from app.enums import AuditEventType, FilingStatus
from app.models.filing import ITRFiling
from app.models.internal_working_doc import InternalWorkingDoc
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.internal_working import (
    InternalWorkingListResponse,
    InternalWorkingReplaceConfirmRequest,
    InternalWorkingReplaceUploadRequest,
    InternalWorkingResponse,
    InternalWorkingUploadRequest,
    InternalWorkingUploadURLResponse,
)
from app.services.audit_service import record_audit_event
from app.services.storage_service import generate_internal_working_key, get_presigned_download_url, get_presigned_upload_url

router = APIRouter()


# ─── POST /internal-workings/upload-url ──────────────────────────
@router.post("/upload-url", response_model=InternalWorkingUploadURLResponse)
async def get_internal_working_upload_url(
    body: InternalWorkingUploadRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL to upload an internal working document."""
    body.filename = sanitize_filename(body.filename)
    validate_file_type(body.filename, body.content_type)

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == body.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Internal working can only be uploaded in COMPUTATION or FILING state
    if filing.status not in (FilingStatus.COMPUTATION, FilingStatus.FILING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload internal working: Filing is in '{filing.status.value}' state. "
                   f"Internal working documents can only be uploaded during COMPUTATION or FILING phase.",
        )

    # Fetch client name for file naming
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    object_key = generate_internal_working_key(
        client_name=client_user.full_name if client_user else "unknown",
        financial_year=filing.financial_year,
        filename=body.filename,
    )

    upload_url = get_presigned_upload_url(object_key, body.content_type)

    return InternalWorkingUploadURLResponse(
        upload_url=upload_url,
        object_key=object_key,
    )


# ─── POST /internal-workings/confirm-upload ──────────────────────
@router.post("/confirm-upload", response_model=InternalWorkingResponse)
async def confirm_internal_working_upload(
    filing_id: UUID,
    object_key: str,
    filename: str,
    content_type: str,
    file_size: int,
    label: str = None,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Confirm internal working upload after file is in MinIO."""
    filename = sanitize_filename(filename)
    validate_file_type(filename, content_type)
    validate_file_size(file_size)

    from datetime import datetime

    from app.config import settings

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    if filing.status not in (FilingStatus.COMPUTATION, FilingStatus.FILING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload internal working: Filing is in '{filing.status.value}' state.",
        )

    # Validate object key starts with Internal-workings/
    if not object_key.startswith("Internal-workings/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid object key for internal working document.",
        )

    # Create StoredFile record
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

    # Create InternalWorkingDoc record
    doc = InternalWorkingDoc(
        filing_id=filing_id,
        file_id=stored_file.id,
        label=label,
        uploaded_by=current_user.id,
        uploaded_at=datetime.utcnow(),
    )
    db.add(doc)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_UPLOADED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=doc.id,
        details={"type": "internal_working", "filename": filename, "label": label},
    )

    await db.commit()
    await db.refresh(doc)

    return InternalWorkingResponse(
        id=doc.id,
        filing_id=doc.filing_id,
        file_id=doc.file_id,
        label=doc.label,
        original_filename=filename,
        uploaded_by=doc.uploaded_by,
        uploaded_by_name=current_user.full_name,
        uploaded_at=doc.uploaded_at,
        replaces_id=doc.replaces_id,
        superseded_at=doc.superseded_at,
    )


# ─── POST /internal-workings/{doc_id}/replace-upload-url ─────────
@router.post("/{doc_id}/replace-upload-url", response_model=InternalWorkingUploadURLResponse)
async def get_internal_working_replace_upload_url(
    doc_id: UUID,
    body: InternalWorkingReplaceUploadRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL to upload a replacement file for an existing internal working doc.

    The original MinIO object is NEVER deleted — version history is preserved.
    """
    body.filename = sanitize_filename(body.filename)
    validate_file_type(body.filename, body.content_type)

    doc_result = await db.execute(
        select(InternalWorkingDoc).where(InternalWorkingDoc.id == doc_id)
    )
    old_doc = doc_result.scalar_one_or_none()
    if not old_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Internal working document not found")

    if old_doc.superseded_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This internal working document has already been replaced. Replace its latest version instead.",
        )

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == old_doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    if filing.status not in (FilingStatus.COMPUTATION, FilingStatus.FILING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot replace internal working: Filing is in '{filing.status.value}' state. "
                   f"Replacement is allowed only during COMPUTATION or FILING phase.",
        )

    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    object_key = generate_internal_working_key(
        client_name=client_user.full_name if client_user else "unknown",
        financial_year=filing.financial_year,
        filename=body.filename,
    )

    upload_url = get_presigned_upload_url(object_key, body.content_type)

    return InternalWorkingUploadURLResponse(
        upload_url=upload_url,
        object_key=object_key,
    )


# ─── POST /internal-workings/{doc_id}/replace-confirm ────────────
@router.post("/{doc_id}/replace-confirm", response_model=InternalWorkingResponse)
async def confirm_internal_working_replace(
    doc_id: UUID,
    body: InternalWorkingReplaceConfirmRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a replacement upload. Marks old row as superseded and creates a new active row.

    The old row's MinIO object is preserved (no S3 delete) so prior versions remain recoverable.
    The new row inherits the old row's ``label`` (label is not editable on replace).
    """
    filename = sanitize_filename(body.filename)
    validate_file_type(filename, body.content_type)
    validate_file_size(body.file_size)

    from datetime import datetime

    from app.config import settings

    doc_result = await db.execute(
        select(InternalWorkingDoc).where(InternalWorkingDoc.id == doc_id)
    )
    old_doc = doc_result.scalar_one_or_none()
    if not old_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Internal working document not found")

    if old_doc.superseded_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This internal working document has already been replaced.",
        )

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == old_doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    if filing.status not in (FilingStatus.COMPUTATION, FilingStatus.FILING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot replace internal working: Filing is in '{filing.status.value}' state.",
        )

    if not body.object_key.startswith("Internal-workings/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid object key for internal working document.",
        )

    # Create new StoredFile for the replacement upload
    new_stored_file = StoredFile(
        bucket=settings.MINIO_BUCKET_NAME,
        object_key=body.object_key,
        original_filename=filename,
        content_type=body.content_type,
        file_size_bytes=body.file_size,
        uploaded_by=current_user.id,
    )
    db.add(new_stored_file)
    await db.flush()

    now = datetime.utcnow()

    # Mark the old row as superseded (DO NOT delete MinIO object)
    old_doc.superseded_at = now

    # Create the new active row, inheriting the old label
    new_doc = InternalWorkingDoc(
        filing_id=old_doc.filing_id,
        file_id=new_stored_file.id,
        label=old_doc.label,
        uploaded_by=current_user.id,
        uploaded_at=now,
        replaces_id=old_doc.id,
    )
    db.add(new_doc)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_UPLOADED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=new_doc.id,
        details={
            "type": "internal_working",
            "action": "replaced",
            "replaces_id": str(old_doc.id),
            "old_file_id": str(old_doc.file_id),
            "filename": filename,
        },
    )

    await db.commit()
    await db.refresh(new_doc)

    return InternalWorkingResponse(
        id=new_doc.id,
        filing_id=new_doc.filing_id,
        file_id=new_doc.file_id,
        label=new_doc.label,
        original_filename=filename,
        uploaded_by=new_doc.uploaded_by,
        uploaded_by_name=current_user.full_name,
        uploaded_at=new_doc.uploaded_at,
        replaces_id=new_doc.replaces_id,
        superseded_at=new_doc.superseded_at,
    )


# ─── GET /internal-workings/filing/{filing_id} ──────────────────
@router.get("/filing/{filing_id}", response_model=InternalWorkingListResponse)
async def list_internal_workings(
    filing_id: UUID,
    include_history: bool = False,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """List internal working documents for a filing. Not visible to clients.

    By default returns only active (non-superseded) docs. Pass
    ``?include_history=true`` to include all prior (replaced) versions as well.
    """
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    stmt = (
        select(InternalWorkingDoc)
        .where(InternalWorkingDoc.filing_id == filing_id)
        .order_by(InternalWorkingDoc.uploaded_at.desc())
    )
    if not include_history:
        stmt = stmt.where(InternalWorkingDoc.superseded_at.is_(None))

    result = await db.execute(stmt)
    docs = result.scalars().all()

    items = []
    for doc in docs:
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
        stored = file_result.scalar_one_or_none()
        uploader_result = await db.execute(select(User).where(User.id == doc.uploaded_by))
        uploader = uploader_result.scalar_one_or_none()
        items.append(InternalWorkingResponse(
            id=doc.id,
            filing_id=doc.filing_id,
            file_id=doc.file_id,
            label=doc.label,
            original_filename=stored.original_filename if stored else None,
            uploaded_by=doc.uploaded_by,
            uploaded_by_name=uploader.full_name if uploader else None,
            uploaded_at=doc.uploaded_at,
            replaces_id=doc.replaces_id,
            superseded_at=doc.superseded_at,
        ))

    return InternalWorkingListResponse(items=items, count=len(items))


# ─── GET /internal-workings/{id}/download ────────────────────────
@router.get("/{doc_id}/download")
async def download_internal_working(
    doc_id: UUID,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed download URL for an internal working document."""
    doc_result = await db.execute(
        select(InternalWorkingDoc).where(InternalWorkingDoc.id == doc_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Internal working document not found")

    # Verify access to the filing
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Get stored file
    file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
    stored = file_result.scalar_one_or_none()
    if not stored:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found in storage")

    download_url = get_presigned_download_url(stored.object_key, filename=stored.original_filename)

    return {"download_url": download_url, "filename": stored.original_filename}


# ─── DELETE /internal-workings/{id} ──────────────────────────────
@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_internal_working(
    doc_id: UUID,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Delete an internal working document."""
    doc_result = await db.execute(
        select(InternalWorkingDoc).where(InternalWorkingDoc.id == doc_id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Internal working document not found")

    # Verify access to the filing
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_UPLOADED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=doc.id,
        details={"type": "internal_working", "action": "deleted", "file_id": str(doc.file_id)},
    )

    await db.delete(doc)
    await db.commit()
