"""API v1 — Document management endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.file_validation import sanitize_filename, validate_file_size, validate_file_type
from app.core.permissions import enforce_filing_access
from app.core.security import get_current_executive_or_partner, get_current_manager_executive_or_partner, get_current_manager_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AuditEventType, DocSubCategory, DocumentStatus, FilingStatus, IncomeHeadCategory, INCOME_HEAD_LABELS, UserRole
from app.models.filing import ITRFiling
from app.models.filing_document import FilingDocument
from app.models.master_doc_type_income_head import MasterDocTypeIncomeHead
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
    FilingDocumentGroupResponse,
    FilingDocumentListResponse,
    FilingDocumentResponse,
    IncomeHeadCatalogItem,
    IncomeHeadCatalogResponse,
    IncomeHeadMappingItem,
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
from app.services.storage_service import generate_object_key, get_presigned_download_url, get_presigned_upload_url, validate_object_key_prefix

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# MASTER DOCUMENT TYPES (Manager / Partner)
# ═══════════════════════════════════════════════════════════════


def _serialize_doc_type(doc_type: MasterDocumentType) -> MasterDocTypeResponse:
    """Build a response with eagerly-loaded income_head_mappings."""
    mappings = [
        IncomeHeadMappingItem(
            income_head=m.income_head,
            sub_category=m.sub_category,
        )
        for m in (doc_type.income_head_mappings or [])
    ]
    return MasterDocTypeResponse(
        id=doc_type.id,
        name=doc_type.name,
        description=doc_type.description,
        is_active=doc_type.is_active,
        display_order=doc_type.display_order,
        created_at=doc_type.created_at,
        income_head_mappings=mappings,
    )


async def _replace_mappings(
    db: AsyncSession,
    doc_type_id: UUID,
    mappings: list[IncomeHeadMappingItem],
) -> None:
    """Delete existing mappings for a doc type and recreate from list (full-replace)."""
    from sqlalchemy import delete as sql_delete

    await db.execute(
        sql_delete(MasterDocTypeIncomeHead).where(
            MasterDocTypeIncomeHead.doc_type_id == doc_type_id
        )
    )

    seen: set[IncomeHeadCategory] = set()
    for m in mappings:
        if m.income_head in seen:
            continue  # silently dedupe
        seen.add(m.income_head)
        db.add(
            MasterDocTypeIncomeHead(
                doc_type_id=doc_type_id,
                income_head=m.income_head,
                sub_category=m.sub_category,
            )
        )
    await db.flush()


@router.get("/income-heads/catalog", response_model=IncomeHeadCatalogResponse)
async def income_head_catalog(
    current_user: User = Depends(get_current_user),
):
    """Static catalog of all income head categories (11 values incl. OTHERS)."""
    items = [
        IncomeHeadCatalogItem(value=head, label=INCOME_HEAD_LABELS[head])
        for head in IncomeHeadCategory
    ]
    return IncomeHeadCatalogResponse(items=items)


@router.get("/types", response_model=MasterDocTypeListResponse)
async def list_document_types(
    include_inactive: bool = Query(False),
    income_head: Optional[IncomeHeadCategory] = Query(None, description="Filter by income head"),
    sub_category: Optional[DocSubCategory] = Query(None, description="Filter by sub-category"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all document types from master list, with optional income-head/sub-category filters."""
    query = select(MasterDocumentType)
    if not include_inactive:
        query = query.where(MasterDocumentType.is_active == True)

    if income_head is not None or sub_category is not None:
        join_q = select(MasterDocTypeIncomeHead.doc_type_id).distinct()
        if income_head is not None:
            join_q = join_q.where(MasterDocTypeIncomeHead.income_head == income_head)
        if sub_category is not None:
            join_q = join_q.where(MasterDocTypeIncomeHead.sub_category == sub_category)
        query = query.where(MasterDocumentType.id.in_(join_q))

    query = query.order_by(MasterDocumentType.display_order)
    result = await db.execute(query)
    items = result.scalars().unique().all()
    return MasterDocTypeListResponse(
        items=[_serialize_doc_type(i) for i in items],
        total=len(items),
    )


@router.get("/types/by-income-heads", response_model=MasterDocTypeListResponse)
async def list_document_types_by_income_heads(
    heads: list[IncomeHeadCategory] = Query(..., description="Income heads to match"),
    sub_category: Optional[DocSubCategory] = Query(None),
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List doc types that map to ANY of the given income heads (optionally filtered by sub-category)."""
    join_q = select(MasterDocTypeIncomeHead.doc_type_id).distinct().where(
        MasterDocTypeIncomeHead.income_head.in_(heads)
    )
    if sub_category is not None:
        join_q = join_q.where(MasterDocTypeIncomeHead.sub_category == sub_category)

    query = select(MasterDocumentType).where(MasterDocumentType.id.in_(join_q))
    if not include_inactive:
        query = query.where(MasterDocumentType.is_active == True)
    query = query.order_by(MasterDocumentType.display_order)

    result = await db.execute(query)
    items = result.scalars().unique().all()
    return MasterDocTypeListResponse(
        items=[_serialize_doc_type(i) for i in items],
        total=len(items),
    )


@router.post("/types", response_model=MasterDocTypeResponse, status_code=201)
async def create_document_type(
    body: MasterDocTypeCreateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Add a new document type to the master list (Manager/Partner)."""
    doc_type = MasterDocumentType(
        name=body.name,
        description=body.description,
        display_order=body.display_order,
        created_by=current_user.id,
    )
    db.add(doc_type)
    await db.flush()  # need doc_type.id for mappings

    if body.income_head_mappings:
        await _replace_mappings(db, doc_type.id, body.income_head_mappings)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MASTER_DOC_TYPE_ADDED,
        actor_id=current_user.id,
        details={
            "name": body.name,
            "income_head_mappings": [
                {"income_head": m.income_head.value, "sub_category": m.sub_category.value}
                for m in body.income_head_mappings
            ],
        },
    )

    await db.flush()
    await db.refresh(doc_type, attribute_names=["income_head_mappings"])
    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_DOC_TYPES)
    return _serialize_doc_type(doc_type)


@router.put("/types/{type_id}", response_model=MasterDocTypeResponse)
async def update_document_type(
    type_id: UUID,
    body: MasterDocTypeUpdateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a document type (Manager/Partner). Pass `income_head_mappings: []` to clear."""
    result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == type_id))
    doc_type = result.scalar_one_or_none()
    if not doc_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document type not found")

    update_data = body.model_dump(exclude_unset=True)
    mappings_payload = update_data.pop("income_head_mappings", None)

    for key, value in update_data.items():
        setattr(doc_type, key, value)
    doc_type.updated_by = current_user.id

    if mappings_payload is not None:
        # body.income_head_mappings preserves enum types; rebuild from validated body
        await _replace_mappings(db, doc_type.id, body.income_head_mappings or [])

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MASTER_DOC_TYPE_UPDATED,
        actor_id=current_user.id,
        details={"type_id": str(type_id), "changes": update_data, "mappings_replaced": mappings_payload is not None},
    )

    await db.flush()
    await db.refresh(doc_type, attribute_names=["income_head_mappings"])
    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_DOC_TYPES)
    return _serialize_doc_type(doc_type)


@router.delete("/types/{type_id}", status_code=204)
async def delete_document_type(
    type_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document type by marking it inactive (Manager/Partner).

    Existing FilingDocument rows referencing this type are preserved.
    """
    result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == type_id))
    doc_type = result.scalar_one_or_none()
    if not doc_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document type not found")

    if not doc_type.is_active:
        # Idempotent: already inactive
        return None

    doc_type.is_active = False
    doc_type.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.MASTER_DOC_TYPE_REMOVED,
        actor_id=current_user.id,
        details={"type_id": str(type_id), "name": doc_type.name},
    )

    await db.flush()
    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_DOC_TYPES)
    return None


# ═══════════════════════════════════════════════════════════════
# FILING DOCUMENT PLACEHOLDERS
# ═══════════════════════════════════════════════════════════════


@router.post("/filings/{filing_id}/assign", response_model=dict)
async def assign_documents_to_filing(
    filing_id: UUID,
    body: DocumentPlaceholderAssignRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign document placeholders to a filing (Manager/Executive/Partner).

    Idempotent: can be called multiple times to update the checklist.
    Works in INITIATED or DOCUMENT_UPLOAD state.
    """
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    # Allow assigning/re-assigning documents in INITIATED, DOCUMENT_UPLOAD, or PROCESSING
    # (PROCESSING is needed after COMPUTATION → PROCESSING backward transition to add new docs)
    if filing.status not in (FilingStatus.INITIATED, FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot assign documents when filing is in {filing.status.value} state",
        )

    # Check manager and executive are assigned before moving to DOCUMENT_UPLOAD
    if filing.status == FilingStatus.INITIATED:
        from app.models.manager_client_assignment import ManagerClientAssignment
        from app.models.executive_assignment import ExecutiveClientAssignment

        mgr_result = await db.execute(
            select(ManagerClientAssignment).where(
                ManagerClientAssignment.client_id == filing.client_id,
                ManagerClientAssignment.is_active == True,
            )
        )
        if not mgr_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A Manager must be assigned to this client before the filing can progress. "
                       "Partner must assign the client to a manager via POST /managers/{id}/clients.",
            )

        exec_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.client_id == filing.client_id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        exec_assignment = exec_result.scalar_one_or_none()
        if not exec_assignment:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="An Executive must be assigned to this client before the filing can progress. "
                       "Please assign an Executive first via the Executive Management page.",
            )

        # Set assigned_executive on the filing if not already set
        if not filing.assigned_executive_id:
            filing.assigned_executive_id = exec_assignment.executive_id

    placeholders = await assign_document_placeholders(
        db=db,
        filing_id=filing_id,
        document_type_ids=body.document_type_ids,
        assigned_by=current_user.id,
    )

    # Transition to DOCUMENT_UPLOAD if currently INITIATED
    if filing.status == FilingStatus.INITIATED:
        from app.services.filing_service import transition_filing_status
        await transition_filing_status(
            db=db,
            filing=filing,
            to_status=FilingStatus.DOCUMENT_UPLOAD,
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
                document_type_description=doc_type.description if doc_type else None,
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

    # Build grouped response
    from collections import defaultdict
    groups_map = defaultdict(list)
    type_names = {}
    type_descriptions: dict = {}
    for item in items:
        groups_map[item.document_type_id].append(item)
        if item.document_type_name:
            type_names[item.document_type_id] = item.document_type_name
        if item.document_type_description is not None:
            type_descriptions[item.document_type_id] = item.document_type_description

    groups = [
        FilingDocumentGroupResponse(
            document_type_id=type_id,
            document_type_name=type_names.get(type_id, "Unknown"),
            document_type_description=type_descriptions.get(type_id),
            files=file_list,
        )
        for type_id, file_list in groups_map.items()
    ]

    return FilingDocumentListResponse(
        items=items,
        groups=groups,
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
    """Get a pre-signed upload URL for a document placeholder.

    Supports two modes:
    1. document_id — upload to an existing placeholder (re-upload / replace)
    2. filing_id + document_type_id — create a NEW placeholder (additional file)
    """
    if body.document_id:
        # Mode 1: existing placeholder — clients or staff (Partner/Manager/Executive)
        # can upload. Staff uploading on behalf of client is gated by enforce_filing_access below.
        doc_result = await db.execute(select(FilingDocument).where(FilingDocument.id == body.document_id))
        doc = doc_result.scalar_one_or_none()
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document placeholder not found")

        filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
        filing = filing_result.scalar_one_or_none()
    else:
        # Mode 2: new placeholder for additional file
        filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == body.filing_id))
        filing = filing_result.scalar_one_or_none()
        if not filing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

        # Validate doc type exists
        dt_result = await db.execute(
            select(MasterDocumentType).where(MasterDocumentType.id == body.document_type_id)
        )
        if not dt_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document type not found")

        # Create new placeholder
        doc = FilingDocument(
            filing_id=filing.id,
            document_type_id=body.document_type_id,
            status=DocumentStatus.PENDING_UPLOAD,
            assigned_by=current_user.id,
        )
        db.add(doc)
        await db.flush()

    await enforce_filing_access(db, current_user, filing.client_id)

    # Fetch client name for readable MinIO path
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    # Validate file type
    body.filename = sanitize_filename(body.filename)
    validate_file_type(body.filename, body.content_type)

    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="documents_required",
        filename=body.filename,
        client_name=client_user.full_name if client_user else "",
    )

    upload_url = get_presigned_upload_url(object_key, body.content_type)

    await db.commit()

    return DocumentUploadURLResponse(
        upload_url=upload_url,
        document_id=doc.id,
        object_key=object_key,
    )


@router.post("/{document_id}/replace-url", response_model=DocumentUploadURLResponse)
async def get_document_replace_url(
    document_id: UUID,
    filename: str = Query(...),
    content_type: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return a pre-signed PUT URL so a user can replace an existing document.

    Available for any document that has not yet been approved (PENDING_UPLOAD,
    UPLOADED, or REJECTED). Once approved the slot is locked — 403 is returned.
    Works in both DOCUMENT_UPLOAD and PROCESSING filing phases.
    Clients upload their own docs; Partner/Manager/Executive can upload on behalf of client.
    """

    doc_result = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document placeholder not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    if filing.status not in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Document replacement is only allowed while the filing is in "
                f"Document Upload or Processing phase. Current status: {filing.status.value}"
            ),
        )

    if doc.status == DocumentStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This document has been approved and cannot be replaced.",
        )

    filename = sanitize_filename(filename)
    validate_file_type(filename, content_type)

    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="documents_required",
        filename=filename,
        client_name=client_user.full_name if client_user else "",
    )

    upload_url = get_presigned_upload_url(object_key, content_type)

    return DocumentUploadURLResponse(
        upload_url=upload_url,
        document_id=doc.id,
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
    filename = sanitize_filename(filename)

    # Validate file type and size
    validate_file_type(filename, content_type)
    validate_file_size(file_size)

    from app.config import settings

    # Verify the document placeholder exists and check filing state
    doc_check = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc_placeholder = doc_check.scalar_one_or_none()
    if not doc_placeholder:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document placeholder not found")

    filing_check = await db.execute(select(ITRFiling).where(ITRFiling.id == doc_placeholder.filing_id))
    filing_for_doc = filing_check.scalar_one_or_none()

    # Validate object_key belongs to this client's documents_required folder
    if filing_for_doc:
        client_user_result = await db.execute(select(User).where(User.id == filing_for_doc.client_id))
        client_user = client_user_result.scalar_one_or_none()
        try:
            validate_object_key_prefix(
                object_key, str(filing_for_doc.client_id),
                client_user.full_name if client_user else "",
                f"ITR-{filing_for_doc.financial_year}/documents_required",
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Documents can only be uploaded in DOCUMENT_UPLOAD or PROCESSING states
    if filing_for_doc and filing_for_doc.status not in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload documents: Filing is in '{filing_for_doc.status.value}' state. "
                   f"Documents can only be uploaded when the filing is in DOCUMENT_UPLOAD or PROCESSING state.",
        )

    # APPROVED documents are permanently locked.
    # Allow replacing UPLOADED documents (pre-approval replacement) for all roles.
    allowed_upload_statuses = {DocumentStatus.PENDING_UPLOAD, DocumentStatus.REJECTED, DocumentStatus.UPLOADED}
    if doc_placeholder.status not in allowed_upload_statuses:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot upload to this document: it is '{doc_placeholder.status.value}'. "
                f"Approved documents cannot be replaced."
            ),
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

    # Clear stale rejection reason when client replaces a previously rejected doc
    if doc.rejection_reason:
        doc.rejection_reason = None

    # If staff uploaded on behalf of client, notify the client
    if filing_for_doc and current_user.role in (UserRole.PARTNER, UserRole.MANAGER, UserRole.EXECUTIVE):
        # Get document type name for notification
        _doc_type_result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id))
        _doc_type_obj = _doc_type_result.scalar_one_or_none()
        _doc_type_name = _doc_type_obj.name if _doc_type_obj else "a document"
        await create_notification(
            db=db,
            user_id=filing_for_doc.client_id,
            title="Document Uploaded on Your Behalf",
            message=f"{current_user.full_name} has uploaded '{_doc_type_name}' on your behalf for FY {filing_for_doc.financial_year}.",
            related_filing_id=filing_for_doc.id,
            related_client_id=filing_for_doc.client_id,
        )
        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            actor_id=current_user.id,
            client_id=filing_for_doc.client_id,
            filing_id=filing_for_doc.id,
            details={
                "uploaded_on_behalf": True,
                "document_id": str(doc.id),
                "document_type": _doc_type_name,
                "filename": filename,
            },
        )

    # If the client is uploading but no Executive is assigned yet, alert
    # Partner + active Manager so they can staff the engagement. The filing
    # cannot move to Computation until an Executive is assigned, so this is
    # the trigger that gets the practice to act. Sent only on the FIRST
    # client upload for the filing to avoid notification spam.
    if filing_for_doc and current_user.role == UserRole.CLIENT:
        from app.models.executive_assignment import ExecutiveClientAssignment
        from app.models.manager_client_assignment import ManagerClientAssignment

        exec_assigned_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.client_id == filing_for_doc.client_id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        no_executive_assigned = exec_assigned_result.scalar_one_or_none() is None

        # Count other docs on this filing already in UPLOADED / APPROVED /
        # REJECTED state — if zero, this is the first upload.
        other_uploaded_count = await db.scalar(
            select(func.count()).select_from(FilingDocument).where(
                FilingDocument.filing_id == filing_for_doc.id,
                FilingDocument.id != doc.id,
                FilingDocument.status.in_((
                    DocumentStatus.UPLOADED,
                    DocumentStatus.APPROVED,
                    DocumentStatus.REJECTED,
                )),
            )
        )

        if no_executive_assigned and (other_uploaded_count or 0) == 0:
            client_user_for_notif_result = await db.execute(
                select(User).where(User.id == filing_for_doc.client_id)
            )
            client_user_for_notif = client_user_for_notif_result.scalar_one_or_none()
            client_display_name = (
                client_user_for_notif.full_name if client_user_for_notif else "Client"
            )

            notif_title = f"{client_display_name} — Documents being uploaded, Executive needed"
            notif_message = (
                f"{client_display_name} has started uploading documents for FY "
                f"{filing_for_doc.financial_year}, but no Executive is assigned yet. "
                "Please assign an Executive so the filing can progress to Computation."
            )

            # Notify all active Partners
            partners_result = await db.execute(
                select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
            )
            for partner in partners_result.scalars().all():
                await create_notification(
                    db=db,
                    user_id=partner.id,
                    title=notif_title,
                    message=notif_message,
                    related_filing_id=filing_for_doc.id,
                    related_client_id=filing_for_doc.client_id,
                    client_name=client_display_name,
                    financial_year=filing_for_doc.financial_year,
                    action_url_path=f"/clients/{filing_for_doc.client_id}",
                    cta_label="Assign Executive",
                )

            # Notify the active Manager for this client (if any)
            mgr_assign_result = await db.execute(
                select(ManagerClientAssignment).where(
                    ManagerClientAssignment.client_id == filing_for_doc.client_id,
                    ManagerClientAssignment.is_active == True,
                )
            )
            mgr_assign = mgr_assign_result.scalar_one_or_none()
            if mgr_assign:
                await create_notification(
                    db=db,
                    user_id=mgr_assign.manager_id,
                    title=notif_title,
                    message=notif_message,
                    related_filing_id=filing_for_doc.id,
                    related_client_id=filing_for_doc.client_id,
                    client_name=client_display_name,
                    financial_year=filing_for_doc.financial_year,
                    action_url_path=f"/clients/{filing_for_doc.client_id}",
                    cta_label="Assign Executive",
                )

    # Get type name
    type_result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id))
    doc_type = type_result.scalar_one_or_none()

    return FilingDocumentResponse(
        id=doc.id,
        filing_id=doc.filing_id,
        document_type_id=doc.document_type_id,
        document_type_name=doc_type.name if doc_type else None,
        document_type_description=doc_type.description if doc_type else None,
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


@router.delete("/{document_id}", status_code=status.HTTP_200_OK)
async def delete_document(
    document_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a document placeholder.

    Allowed only when the filing is in DOCUMENT_UPLOAD, PROCESSING, or HALTED.

    Permissions:
    - **Manager / Executive / Partner**: may remove a placeholder only if it
      is still PENDING_UPLOAD (empty). Once any file is attached, the
      placeholder is preserved as a record.
    - **Client (own filing)**: may remove their own UPLOADED or REJECTED
      placeholder only — never APPROVED, never an empty PENDING_UPLOAD slot.
      At least one *other* placeholder of the same document type must still
      have a file attached (UPLOADED / REJECTED / APPROVED).
    """
    doc_result = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_filing_access(db, current_user, filing.client_id)

    if filing.status not in (
        FilingStatus.DOCUMENT_UPLOAD,
        FilingStatus.PROCESSING,
        FilingStatus.HALTED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Placeholders can only be removed while the filing is in "
                "DOCUMENT_UPLOAD, PROCESSING, or HALTED state."
            ),
        )

    is_client = current_user.role == UserRole.CLIENT
    is_staff = current_user.role in (UserRole.MANAGER, UserRole.EXECUTIVE, UserRole.PARTNER)

    if is_staff:
        # Staff may only remove empty placeholders.
        if doc.status != DocumentStatus.PENDING_UPLOAD:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove a placeholder that already has an uploaded file.",
            )
    elif is_client:
        # Client may only remove their own non-approved uploads, never empty slots.
        if doc.status not in (DocumentStatus.UPLOADED, DocumentStatus.REJECTED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You can only remove an extra file you uploaded that has not been approved.",
            )

        # Ensure at least one OTHER placeholder of the same type still has a file attached.
        siblings_result = await db.execute(
            select(FilingDocument).where(
                FilingDocument.filing_id == doc.filing_id,
                FilingDocument.document_type_id == doc.document_type_id,
                FilingDocument.id != doc.id,
            )
        )
        sibling_docs = siblings_result.scalars().all()
        sibling_with_file_exists = any(
            s.status in (DocumentStatus.UPLOADED, DocumentStatus.REJECTED, DocumentStatus.APPROVED)
            for s in sibling_docs
        )
        if not sibling_with_file_exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "At least one uploaded file must remain for this document type. "
                    "Upload another file before removing this one."
                ),
            )
    else:
        # Defensive: any other role (e.g. DASHBOARD_USER) is rejected.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    document_type_id = doc.document_type_id
    filing_id = doc.filing_id
    prev_status = doc.status.value

    await db.delete(doc)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_PLACEHOLDER_REMOVED,
        actor_id=current_user.id,
        filing_id=filing_id,
        document_id=document_id,
        details={
            "document_type_id": str(document_type_id),
            "previous_status": prev_status,
            "removed_by_role": current_user.role.value,
        },
    )

    await db.commit()

    return {"message": "Document placeholder removed"}


# ═══════════════════════════════════════════════════════════════
# DOCUMENT REVIEW (Approve / Reject)
# ═══════════════════════════════════════════════════════════════


@router.post("/approve", response_model=dict)
async def approve_filing_documents(
    body: DocumentApproveRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Approve one or more documents (Manager/Executive/Partner)."""
    approved = await approve_documents(db, body.document_ids, current_user.id)
    if not approved:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No documents found to approve")

    # Check if all docs are now approved for the filing
    filing_id = approved[0].filing_id
    all_approved = await check_all_documents_approved(db, filing_id)

    result_msg = f"{len(approved)} document(s) approved"
    if all_approved:
        # Notify client that all documents are approved (state transition is manual)
        filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
        filing = filing_result.scalar_one_or_none()
        if filing and filing.status in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
            await create_notification(
                db=db,
                user_id=filing.client_id,
                title="Documents Approved",
                message="All your documents have been approved. Your filing will advance once the executive proceeds.",
                related_filing_id=filing_id,
            )
            result_msg += ". All documents approved — awaiting executive action to move to computation."

    return {"message": result_msg, "all_approved": all_approved}


@router.post("/reject", response_model=dict)
async def reject_filing_documents(
    body: DocumentRejectRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reject one or more documents with reasons (Manager/Executive/Partner)."""
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
