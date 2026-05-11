"""API v1 — Document management endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_filing_access
from app.core.security import get_current_executive_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AuditEventType, DocumentStatus, FilingStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_document import FilingDocument
from app.models.master_document_type import MasterDocumentType
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.document import (
    DocumentApproveRequest,
    DocumentDownloadURLResponse,
    DocumentPlaceholderAssignRequest,
    DocumentRejectRequest,
    DocumentUploadURLRequest,
    DocumentUploadURLResponse,
    FilingDocumentListResponse,
    FilingDocumentResponse,
    MasterDocTypeCreateRequest,
    MasterDocTypeListResponse,
    MasterDocTypeResponse,
    MasterDocTypeUpdateRequest,
)
from app.services.audit_service import record_audit_event
from app.services.document_service import (
    approve_documents,
    assign_document_placeholders,
    check_all_documents_approved,
    get_filing_documents_summary,
    record_document_upload,
    reject_documents,
)
from app.services.notification_service import create_notification
from app.services.storage_service import generate_object_key, get_presigned_download_url, get_presigned_upload_url

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# MASTER DOCUMENT TYPES (Partner Only)
# ═══════════════════════════════════════════════════════════════


@router.get("/types", response_model=MasterDocTypeListResponse)
async def list_document_types(
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all document types from master list."""
    query = select(MasterDocumentType)
    if not include_inactive:
        query = query.where(MasterDocumentType.is_active == True)
    query = query.order_by(MasterDocumentType.display_order)

    result = await db.execute(query)
    items = result.scalars().all()
    return MasterDocTypeListResponse(
        items=[MasterDocTypeResponse.model_validate(i) for i in items],
        total=len(items),
    )


@router.post("/types", response_model=MasterDocTypeResponse, status_code=201)
async def create_document_type(
    body: MasterDocTypeCreateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Add a new document type to the master list (Partner only)."""
    doc_type = MasterDocumentType(
        name=body.name,
        description=body.description,
        display_order=body.display_order,
        created_by=current_user.id,
    )
    db.add(doc_type)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MASTER_DOC_TYPE_ADDED,
        actor_id=current_user.id,
        details={"name": body.name},
    )

    await db.flush()
    return MasterDocTypeResponse.model_validate(doc_type)


@router.put("/types/{type_id}", response_model=MasterDocTypeResponse)
async def update_document_type(
    type_id: UUID,
    body: MasterDocTypeUpdateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a document type (Partner only)."""
    result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == type_id))
    doc_type = result.scalar_one_or_none()
    if not doc_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document type not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(doc_type, key, value)
    doc_type.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MASTER_DOC_TYPE_UPDATED,
        actor_id=current_user.id,
        details={"type_id": str(type_id), "changes": update_data},
    )

    await db.flush()
    return MasterDocTypeResponse.model_validate(doc_type)


# ═══════════════════════════════════════════════════════════════
# FILING DOCUMENT PLACEHOLDERS
# ═══════════════════════════════════════════════════════════════


@router.post("/filings/{filing_id}/assign", response_model=dict)
async def assign_documents_to_filing(
    filing_id: UUID,
    body: DocumentPlaceholderAssignRequest,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign document placeholders to a filing (Executive/Partner).

    Idempotent: can be called multiple times to update the checklist.
    Works in INITIATED or ON_BOARDING state.
    """
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Allow assigning/re-assigning documents in INITIATED, ON_BOARDING, or PROCESSING
    # (PROCESSING is needed after COMPUTATION → PROCESSING backward transition to add new docs)
    if filing.status not in (FilingStatus.INITIATED, FilingStatus.ON_BOARDING, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot assign documents when filing is in {filing.status.value} state",
        )

    # Check executive is assigned before moving to ON_BOARDING
    if filing.status == FilingStatus.INITIATED:
        if not filing.assigned_executive_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="An Executive must be assigned to this client before document placeholders can be assigned. "
                       "Please assign an Executive first via the Executive Management page.",
            )

    placeholders = await assign_document_placeholders(
        db=db,
        filing_id=filing_id,
        document_type_ids=body.document_type_ids,
        assigned_by=current_user.id,
    )

    # Transition to ON_BOARDING if currently INITIATED
    if filing.status == FilingStatus.INITIATED:
        from app.services.filing_service import transition_filing_status
        await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.ON_BOARDING,
            changed_by=current_user.id,
            remarks="Document placeholders assigned",
        )

    # Notify client
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Document Checklist Ready",
        message="Your document checklist is ready. Please upload the required documents.",
        related_filing_id=filing_id,
    )

    return {"message": f"{len(placeholders)} document placeholders assigned", "count": len(placeholders)}


@router.get("/filings/{filing_id}", response_model=FilingDocumentListResponse)
async def get_filing_documents(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all document placeholders for a filing."""
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    docs_result = await db.execute(
        select(FilingDocument).where(FilingDocument.filing_id == filing_id).order_by(FilingDocument.assigned_at)
    )
    docs = docs_result.scalars().all()

    items = []
    summary = {"PENDING_UPLOAD": 0, "UPLOADED": 0, "REJECTED": 0, "APPROVED": 0}
    for doc in docs:
        # Get type name
        type_result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id))
        doc_type = type_result.scalar_one_or_none()

        # Get filename
        filename = None
        if doc.file_id:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
            stored = file_result.scalar_one_or_none()
            if stored:
                filename = stored.original_filename

        items.append(
            FilingDocumentResponse(
                id=doc.id,
                filing_id=doc.filing_id,
                document_type_id=doc.document_type_id,
                document_type_name=doc_type.name if doc_type else None,
                status=doc.status,
                file_id=doc.file_id,
                original_filename=filename,
                rejection_reason=doc.rejection_reason,
                uploaded_at=doc.uploaded_at,
                reviewed_by=doc.reviewed_by,
                reviewed_at=doc.reviewed_at,
                assigned_at=doc.assigned_at,
            )
        )
        summary[doc.status.value] += 1

    all_approved = summary["APPROVED"] == len(docs) and len(docs) > 0

    return FilingDocumentListResponse(
        items=items,
        total=len(docs),
        all_approved=all_approved,
        pending_count=summary["PENDING_UPLOAD"],
        uploaded_count=summary["UPLOADED"],
        rejected_count=summary["REJECTED"],
        approved_count=summary["APPROVED"],
    )


# ═══════════════════════════════════════════════════════════════
# DOCUMENT UPLOAD / DOWNLOAD (Pre-signed URLs)
# ═══════════════════════════════════════════════════════════════


@router.post("/upload-url", response_model=DocumentUploadURLResponse)
async def get_document_upload_url(
    body: DocumentUploadURLRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed upload URL for a document placeholder."""
    doc_result = await db.execute(select(FilingDocument).where(FilingDocument.id == body.document_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document placeholder not found")

    # Get filing for path generation
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()

    await enforce_filing_access(db, current_user, filing.client_id)

    # Fetch client name for readable MinIO path
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="documents_required",
        filename=body.filename,
        client_name=client_user.full_name if client_user else "",
    )

    upload_url = get_presigned_upload_url(object_key, body.content_type)

    return DocumentUploadURLResponse(
        upload_url=upload_url,
        document_id=body.document_id,
        object_key=object_key,
    )


@router.post("/confirm-upload", response_model=FilingDocumentResponse)
async def confirm_document_upload(
    document_id: UUID = Query(...),
    object_key: str = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    file_size: int = Query(..., gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a document upload after the client uploads to MinIO."""
    from app.config import settings

    # Verify the document placeholder exists and check filing state
    doc_check = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc_placeholder = doc_check.scalar_one_or_none()
    if not doc_placeholder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document placeholder not found")

    filing_check = await db.execute(select(ITRFiling).where(ITRFiling.id == doc_placeholder.filing_id))
    filing_for_doc = filing_check.scalar_one_or_none()

    # Documents can only be uploaded in ON_BOARDING or PROCESSING states
    if filing_for_doc and filing_for_doc.status not in (FilingStatus.ON_BOARDING, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload documents: Filing is in '{filing_for_doc.status.value}' state. "
                   f"Documents can only be uploaded when the filing is in ON_BOARDING or PROCESSING state.",
        )

    # Only PENDING_UPLOAD or REJECTED docs can be uploaded to
    if doc_placeholder.status not in (DocumentStatus.PENDING_UPLOAD, DocumentStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload to this document: it is already '{doc_placeholder.status.value}'. "
                   f"Only documents with 'PENDING_UPLOAD' or 'REJECTED' status accept uploads.",
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
        stored_file.original_filename = filename
        stored_file.content_type = content_type
        stored_file.file_size_bytes = file_size
        stored_file.uploaded_by = current_user.id
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

    # Update document placeholder
    doc = await record_document_upload(db, document_id, stored_file.id, current_user.id)

    # Get type name
    type_result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id))
    doc_type = type_result.scalar_one_or_none()

    return FilingDocumentResponse(
        id=doc.id,
        filing_id=doc.filing_id,
        document_type_id=doc.document_type_id,
        document_type_name=doc_type.name if doc_type else None,
        status=doc.status,
        file_id=doc.file_id,
        original_filename=filename,
        uploaded_at=doc.uploaded_at,
        assigned_at=doc.assigned_at,
    )


@router.get("/{document_id}/download-url", response_model=DocumentDownloadURLResponse)
async def get_document_download_url(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed download URL for a document."""
    doc_result = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()

    await enforce_filing_access(db, current_user, filing.client_id)

    if not doc.file_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file uploaded yet")

    file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
    stored_file = file_result.scalar_one_or_none()
    if not stored_file:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File record not found")

    download_url = get_presigned_download_url(stored_file.object_key, filename=stored_file.original_filename)

    # Audit download
    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_DOWNLOADED,
        actor_id=current_user.id,
        filing_id=doc.filing_id,
        document_id=document_id,
    )

    return DocumentDownloadURLResponse(
        download_url=download_url,
        filename=stored_file.original_filename,
        content_type=stored_file.content_type,
    )


# ═══════════════════════════════════════════════════════════════
# DOCUMENT REVIEW (Approve / Reject)
# ═══════════════════════════════════════════════════════════════


@router.post("/approve", response_model=dict)
async def approve_filing_documents(
    body: DocumentApproveRequest,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Approve one or more documents (Executive/Partner)."""
    approved = await approve_documents(db, body.document_ids, current_user.id)
    if not approved:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No documents found to approve")

    # Check if all docs are now approved for the filing
    filing_id = approved[0].filing_id
    all_approved = await check_all_documents_approved(db, filing_id)

    result_msg = f"{len(approved)} document(s) approved"
    if all_approved:
        # All documents approved — auto-transition to COMPUTATION
        filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
        filing = filing_result.scalar_one_or_none()
        if filing and filing.status in (FilingStatus.ON_BOARDING, FilingStatus.PROCESSING):
            from app.services.filing_service import transition_filing_status

            # If still in ON_BOARDING, step through PROCESSING first
            if filing.status == FilingStatus.ON_BOARDING:
                filing = await transition_filing_status(
                    db=db,
                    filing=filing,
                    to_status=FilingStatus.PROCESSING,
                    changed_by=current_user.id,
                    remarks="All documents approved — auto-advancing",
                )

            # PROCESSING → COMPUTATION
            await transition_filing_status(
                db=db,
                filing=filing,
                to_status=FilingStatus.COMPUTATION,
                changed_by=current_user.id,
                remarks="All documents approved",
            )
            # Notify client
            await create_notification(
                db=db,
                user_id=filing.client_id,
                title="Documents Approved",
                message="All your documents have been approved. Computation will be prepared.",
                related_filing_id=filing_id,
            )
            result_msg += ". All documents approved - filing moved to COMPUTATION."

    return {"message": result_msg, "all_approved": all_approved}


@router.post("/reject", response_model=dict)
async def reject_filing_documents(
    body: DocumentRejectRequest,
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reject one or more documents with reasons (Executive/Partner)."""
    if not body.rejections:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No rejections provided")

    # Get filing info from first document
    first_doc_result = await db.execute(
        select(FilingDocument).where(FilingDocument.id == body.rejections[0].document_id)
    )
    first_doc = first_doc_result.scalar_one_or_none()
    if not first_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == first_doc.filing_id))
    filing = filing_result.scalar_one_or_none()

    rejections_data = [{"document_id": r.document_id, "reason": r.reason} for r in body.rejections]

    rejected = await reject_documents(
        db=db,
        rejections=rejections_data,
        reviewed_by=current_user.id,
        filing_id=filing.id,
        client_id=filing.client_id,
    )

    # BRD: Filing stays in PROCESSING — rejected placeholders go RED,
    # client re-uploads the rejected docs and re-submits.
    # Notify client about the rejections.
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Documents Require Correction",
        message=f"{len(rejected)} document(s) need to be re-uploaded for {filing.financial_year}.",
        related_filing_id=filing.id,
    )

    return {"message": f"{len(rejected)} document(s) rejected", "count": len(rejected)}
