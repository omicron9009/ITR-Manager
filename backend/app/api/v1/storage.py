"""API v1 — Storage / file management endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.file_validation import sanitize_filename, validate_file_size, validate_file_type
from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_manager_or_partner, get_current_user
from app.database import get_db
from app.enums import CompletedDocStatus, CompletedDocType, FilingStatus, FormFieldType, UserRole
from app.models.filing import ITRFiling
from app.models.filing_completed_doc import FilingCompletedDoc
from app.models.onboarding_form_field import OnboardingFormField
from app.models.stored_file import StoredFile
from app.models.user import User
from app.services.storage_service import (
    generate_onboarding_object_key,
    get_presigned_download_url,
    get_presigned_upload_url,
    validate_object_key_prefix,
)

router = APIRouter()


# ─── POST /storage/onboarding-upload-url ─────────────────────
@router.post("/onboarding-upload-url", response_model=dict)
async def get_onboarding_upload_url(
    field_key: str = Query(..., description="Onboarding form field key (must be a FILE type field)"),
    filename: str = Query(...),
    content_type: str = Query(...),
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Get a pre-signed URL for uploading a file attached to an onboarding form field."""
    filename = sanitize_filename(filename)

    # Validate that the field_key exists and is a FILE type
    field_result = await db.execute(
        select(OnboardingFormField).where(
            OnboardingFormField.field_key == field_key,
            OnboardingFormField.is_active == True,
        )
    )
    field = field_result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Form field '{field_key}' not found")
    if field.field_type != FormFieldType.FILE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Field '{field_key}' is not a FILE type field",
        )

    object_key = generate_onboarding_object_key(
        client_id=str(current_user.id),
        client_name=current_user.full_name,
        field_label=field.field_label,
        filename=filename,
    )

    # Validate file type
    validate_file_type(filename, content_type)

    upload_url = get_presigned_upload_url(object_key, content_type)

    return {
        "upload_url": upload_url,
        "object_key": object_key,
        "field_key": field_key,
    }


# ─── POST /storage/confirm-onboarding-upload ─────────────────
@router.post("/confirm-onboarding-upload", response_model=dict)
async def confirm_onboarding_upload(
    field_key: str = Query(...),
    object_key: str = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    file_size: int = Query(..., gt=0),
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Confirm an onboarding file upload and return a stored_file ID to use in form_data."""
    filename = sanitize_filename(filename)

    # Validate file type and size
    validate_file_type(filename, content_type)
    validate_file_size(file_size)

    from app.config import settings

    try:
        validate_object_key_prefix(object_key, str(current_user.id), current_user.full_name, "onboarding")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

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

    return {
        "message": "Onboarding file uploaded successfully",
        "file_id": str(stored_file.id),
        "field_key": field_key,
    }


# ─── GET /storage/onboarding-files ───────────────────────────
@router.get("/onboarding-files", response_model=list[dict])
async def get_onboarding_files(
    client_id: UUID = Query(None, description="Client ID (for Partner/Executive). Omit for self."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all files uploaded during onboarding for a client.

    - Client: gets their own onboarding files.
    - Executive/Partner: can pass client_id to fetch a specific client's files.
    """
    from app.models.client_profile import ClientProfile

    # Determine target client
    if current_user.role == UserRole.CLIENT:
        target_client_id = current_user.id
    else:
        if not client_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="client_id is required for Partner/Executive",
            )
        target_client_id = client_id

    await enforce_client_access(db, current_user, target_client_id)

    # Fetch client profile to get form_data
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == target_client_id)
    )
    profile = profile_result.scalar_one_or_none()
    if not profile or not profile.form_data:
        return []

    # Get all active FILE-type fields
    fields_result = await db.execute(
        select(OnboardingFormField).where(
            OnboardingFormField.field_type == FormFieldType.FILE,
        )
    )
    file_fields = fields_result.scalars().all()
    file_field_keys = {f.field_key: f.field_label for f in file_fields}

    # Resolve file IDs from form_data
    items = []
    for field_key, field_label in file_field_keys.items():
        value = profile.form_data.get(field_key)
        if not value or not isinstance(value, str):
            continue
        try:
            file_uuid = UUID(value)
        except (ValueError, AttributeError):
            continue

        file_result = await db.execute(
            select(StoredFile).where(StoredFile.id == file_uuid)
        )
        stored_file = file_result.scalar_one_or_none()
        if stored_file:
            download_url = get_presigned_download_url(
                stored_file.object_key, filename=stored_file.original_filename,
            )
            items.append({
                "file_id": str(stored_file.id),
                "field_key": field_key,
                "field_label": field_label,
                "filename": stored_file.original_filename,
                "content_type": stored_file.content_type,
                "file_size_bytes": stored_file.file_size_bytes,
                "uploaded_at": stored_file.uploaded_at.isoformat() if stored_file.uploaded_at else None,
                "download_url": download_url,
            })

    return items


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
    """Get upload URL for ITR Acknowledgement or Invoice (Manager/Executive/Partner)."""
    filename = sanitize_filename(filename)

    if doc_type == CompletedDocType.INVOICE:
        if current_user.role != UserRole.PARTNER:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invoice upload is restricted to Partner")
    elif current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Validate file type (skip for ITR_JSON which has its own validation)
    if doc_type != CompletedDocType.ITR_JSON:
        validate_file_type(filename, content_type)

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
    filename = sanitize_filename(filename)

    if doc_type == CompletedDocType.INVOICE:
        if current_user.role != UserRole.PARTNER:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invoice upload is restricted to Partner")
    elif current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    # Validate file type and size (skip for ITR_JSON which has its own validation)
    if doc_type != CompletedDocType.ITR_JSON:
        validate_file_type(filename, content_type)
        validate_file_size(file_size)
    else:
        validate_file_size(file_size)

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

    # Validate object_key belongs to this client's filed_documents folder
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()
    try:
        validate_object_key_prefix(
            object_key, str(filing.client_id),
            client_user.full_name if client_user else "",
            f"ITR-{filing.financial_year}/filed_documents",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

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

    # Validate ITR_FORM (required)
    if doc_type == CompletedDocType.ITR_FORM:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload ITR Form in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
            )

    # Validate FINANCIAL_STATEMENT (optional)
    if doc_type == CompletedDocType.FINANCIAL_STATEMENT:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload Financial Statement in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
            )

    # Validate TAX_PAID_COMPUTATION (required)
    if doc_type == CompletedDocType.TAX_PAID_COMPUTATION:
        if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot upload Tax Paid Computation in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
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
        existing_doc.status = CompletedDocStatus.UPLOADED
        # Reset approval fields on re-upload
        existing_doc.manager_approved_by = None
        existing_doc.manager_approved_at = None
        existing_doc.manager_rejected_by = None
        existing_doc.manager_rejected_at = None
        existing_doc.rejection_reason = None
        existing_doc.partner_approved_by = None
        existing_doc.partner_approved_at = None
    else:
        completed_doc = FilingCompletedDoc(
            filing_id=filing_id,
            doc_type=doc_type,
            file_id=stored_file.id,
            uploaded_by=current_user.id,
            status=CompletedDocStatus.UPLOADED,
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
    elif doc_type in (CompletedDocType.ITR_FORM, CompletedDocType.FINANCIAL_STATEMENT, CompletedDocType.TAX_PAID_COMPUTATION):
        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_UPLOADED,
            actor_id=current_user.id,
            client_id=filing.client_id,
            filing_id=filing_id,
            details={"doc_type": doc_type.value, "filename": filename},
        )

    # Check if all required completed docs are uploaded — notify manager for approval
    # FILING→PAYMENT transition now requires all docs to be PARTNER_APPROVED (handled in approval endpoints)
    # INVOICE is excluded here: it is uploaded by Partner and approved directly by Partner without manager involvement.
    if filing.status == FilingStatus.FILING:
        required_doc_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.ITR_JSON, CompletedDocType.ITR_FORM, CompletedDocType.TAX_PAID_COMPUTATION}
        existing_docs_result = await db.execute(
            select(FilingCompletedDoc.doc_type).where(FilingCompletedDoc.filing_id == filing_id)
        )
        existing_types = {row[0] for row in existing_docs_result.all()}
        missing_types = required_doc_types - existing_types

        # Notify manager if all required docs uploaded (awaiting approval)
        if not missing_types:
            from app.models.manager_client_assignment import ManagerClientAssignment
            mgr_result = await db.execute(
                select(ManagerClientAssignment).where(
                    ManagerClientAssignment.client_id == filing.client_id,
                    ManagerClientAssignment.is_active == True,
                )
            )
            mgr_assignment = mgr_result.scalar_one_or_none()
            if mgr_assignment:
                await create_notification(
                    db=db,
                    user_id=mgr_assignment.manager_id,
                    title="Filed Documents Ready for Review",
                    message=f"All required filed documents have been uploaded for {client_user.full_name if client_user else 'client'} ({filing.financial_year}). Please review and approve.",
                    related_filing_id=filing_id,
                )

        await db.flush()
        remaining = [t.value for t in (required_doc_types - existing_types)]
        return {
            "message": f"{doc_type.value} uploaded successfully. Awaiting manager/partner approval.",
            "file_id": str(stored_file.id),
            "status": CompletedDocStatus.UPLOADED.value,
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
    Clients can only see PARTNER_APPROVED docs after filing is COMPLETED.
    """
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Clients can only view PARTNER_APPROVED docs after COMPLETED state
    if current_user.role == UserRole.CLIENT:
        if filing.status == FilingStatus.COMPLETED:
            result = await db.execute(
                select(FilingCompletedDoc).where(
                    FilingCompletedDoc.filing_id == filing_id,
                    FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
                    FilingCompletedDoc.doc_type.in_([
                        CompletedDocType.ITR_ACKNOWLEDGEMENT,
                        CompletedDocType.INVOICE,
                        CompletedDocType.ITR_FORM,
                        CompletedDocType.FINANCIAL_STATEMENT,
                        CompletedDocType.TAX_PAID_COMPUTATION,
                    ]),
                )
            )
        else:
            # Before COMPLETED, only show PARTNER_APPROVED Invoice if it exists
            result = await db.execute(
                select(FilingCompletedDoc).where(
                    FilingCompletedDoc.filing_id == filing_id,
                    FilingCompletedDoc.doc_type == CompletedDocType.INVOICE,
                    FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
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
            "status": doc.status.value if doc.status else "UPLOADED",
            "manager_approved_by": str(doc.manager_approved_by) if doc.manager_approved_by else None,
            "manager_approved_at": doc.manager_approved_at.isoformat() if doc.manager_approved_at else None,
            "partner_approved_by": str(doc.partner_approved_by) if doc.partner_approved_by else None,
            "partner_approved_at": doc.partner_approved_at.isoformat() if doc.partner_approved_at else None,
            "rejection_reason": doc.rejection_reason,
        })

    return items


# ─── POST /storage/completed-doc/manager-approve ─────────────
@router.post("/completed-doc/manager-approve", response_model=dict)
async def manager_approve_completed_doc(
    doc_id: UUID = Query(...),
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Manager/Partner approves a completed doc: UPLOADED → MANAGER_APPROVED."""
    from datetime import datetime
    from app.services.audit_service import record_audit_event
    from app.enums import AuditEventType
    from app.services.notification_service import create_notification

    doc_result = await db.execute(select(FilingCompletedDoc).where(FilingCompletedDoc.id == doc_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    if doc.doc_type == CompletedDocType.INVOICE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invoice approval is reserved for Partner only",
        )

    if doc.status != CompletedDocStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve: document is in '{doc.status.value}' status. Only 'UPLOADED' documents can be manager-approved.",
        )

    doc.status = CompletedDocStatus.MANAGER_APPROVED
    doc.manager_approved_by = current_user.id
    doc.manager_approved_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_APPROVED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=doc.id,
        details={"type": "completed_doc", "doc_type": doc.doc_type.value, "level": "manager"},
    )

    # Notify partner
    partner_result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    partner = partner_result.scalar_one_or_none()
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title="Filed Document Approved by Manager",
            message=f"{doc.doc_type.value} for {filing.financial_year} has been approved by {current_user.full_name}. Awaiting your final approval.",
            related_filing_id=filing.id,
        )

    await db.commit()
    return {"message": f"{doc.doc_type.value} manager-approved successfully", "status": doc.status.value}


# ─── POST /storage/completed-doc/partner-approve ─────────────
@router.post("/completed-doc/partner-approve", response_model=dict)
async def partner_approve_completed_doc(
    doc_id: UUID = Query(...),
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Partner approves a completed doc: UPLOADED/MANAGER_APPROVED → PARTNER_APPROVED.
    Partner can bypass manager approval.
    """
    from datetime import datetime
    from app.services.audit_service import record_audit_event
    from app.services.filing_service import transition_filing_status
    from app.services.notification_service import create_notification
    from app.enums import AuditEventType

    if current_user.role != UserRole.PARTNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only Partner can give final approval")

    doc_result = await db.execute(select(FilingCompletedDoc).where(FilingCompletedDoc.id == doc_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    if doc.status not in (CompletedDocStatus.UPLOADED, CompletedDocStatus.MANAGER_APPROVED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot approve: document is in '{doc.status.value}' status. Only 'UPLOADED' or 'MANAGER_APPROVED' documents can be partner-approved.",
        )

    doc.status = CompletedDocStatus.PARTNER_APPROVED
    doc.partner_approved_by = current_user.id
    doc.partner_approved_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_APPROVED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=doc.id,
        details={"type": "completed_doc", "doc_type": doc.doc_type.value, "level": "partner"},
    )

    await db.flush()

    # Check if ALL required docs are now PARTNER_APPROVED → auto-transition to PAYMENT
    if filing.status == FilingStatus.FILING:
        # For no-fee clients, INVOICE is not required
        if filing.no_fees_applicable:
            required_doc_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.ITR_JSON, CompletedDocType.ITR_FORM, CompletedDocType.TAX_PAID_COMPUTATION}
        else:
            required_doc_types = {CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE, CompletedDocType.ITR_JSON, CompletedDocType.ITR_FORM, CompletedDocType.TAX_PAID_COMPUTATION}
        approved_result = await db.execute(
            select(FilingCompletedDoc.doc_type).where(
                FilingCompletedDoc.filing_id == filing.id,
                FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
            )
        )
        approved_types = {row[0] for row in approved_result.all()}

        if required_doc_types.issubset(approved_types):
            await transition_filing_status(
                db=db,
                filing=filing,
                to_status=FilingStatus.PAYMENT,
                changed_by=current_user.id,
                remarks="All required filed documents partner-approved",
            )

            # For no-fee clients, auto-complete immediately (PAYMENT → COMPLETED)
            if filing.no_fees_applicable:
                from datetime import datetime as _dt
                filing.payment_received_at = _dt.utcnow()
                await transition_filing_status(
                    db=db,
                    filing=filing,
                    to_status=FilingStatus.COMPLETED,
                    changed_by=current_user.id,
                    remarks="No fees applicable; auto-completed",
                )
                await create_notification(
                    db=db,
                    user_id=filing.client_id,
                    title="Filing Completed",
                    message=f"Your ITR for {filing.financial_year} has been filed and completed successfully. All documents are now available for download.",
                    related_filing_id=filing.id,
                )
            else:
                await create_notification(
                    db=db,
                    user_id=filing.client_id,
                    title="ITR Filed Successfully",
                    message=f"Your ITR for {filing.financial_year} has been filed. Please complete payment.",
                    related_filing_id=filing.id,
                )

    await db.commit()
    return {"message": f"{doc.doc_type.value} partner-approved successfully", "status": doc.status.value}


# ─── POST /storage/completed-doc/manager-reject ──────────────
@router.post("/completed-doc/manager-reject", response_model=dict)
async def manager_reject_completed_doc(
    doc_id: UUID = Query(...),
    reason: str = Query(..., min_length=1, max_length=1000),
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Manager/Partner rejects a completed doc: UPLOADED → MANAGER_REJECTED (staff re-uploads)."""
    from datetime import datetime
    from app.services.audit_service import record_audit_event
    from app.services.notification_service import create_notification
    from app.enums import AuditEventType

    doc_result = await db.execute(select(FilingCompletedDoc).where(FilingCompletedDoc.id == doc_id))
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Completed document not found")

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    if doc.doc_type == CompletedDocType.INVOICE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invoice management is reserved for Partner only",
        )

    if doc.status != CompletedDocStatus.UPLOADED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot reject: document is in '{doc.status.value}' status. Only 'UPLOADED' documents can be rejected.",
        )

    doc.status = CompletedDocStatus.MANAGER_REJECTED
    doc.manager_rejected_by = current_user.id
    doc.manager_rejected_at = datetime.utcnow()
    doc.rejection_reason = reason

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_REJECTED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing.id,
        document_id=doc.id,
        details={"type": "completed_doc", "doc_type": doc.doc_type.value, "reason": reason},
    )

    # Notify uploader (executive)
    if doc.uploaded_by:
        await create_notification(
            db=db,
            user_id=doc.uploaded_by,
            title="Filed Document Rejected",
            message=f"{doc.doc_type.value} for {filing.financial_year} was rejected. Reason: {reason}. Please re-upload.",
            related_filing_id=filing.id,
        )

    await db.commit()
    return {"message": f"{doc.doc_type.value} rejected", "status": doc.status.value, "reason": reason}


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
            if completed_doc.doc_type == CompletedDocType.INVOICE and current_user.role not in (UserRole.PARTNER, UserRole.CLIENT):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invoice download is restricted to Partner")
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

    # If still not found, try resolving through FilingOtherDoc
    if not stored_file:
        from app.models.filing_other_doc import FilingOtherDoc
        other_result = await db.execute(
            select(FilingOtherDoc).where(FilingOtherDoc.id == file_id)
        )
        other_doc = other_result.scalar_one_or_none()
        if other_doc:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == other_doc.file_id))
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


# ═══════════════════════════════════════════════════════════════
# OTHER DOCUMENTS (multiple misc docs per filing)
# ═══════════════════════════════════════════════════════════════


# ─── POST /storage/other-doc/upload-url ──────────────────────
@router.post("/other-doc/upload-url", response_model=dict)
async def get_other_doc_upload_url(
    filing_id: UUID = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    label: str = Query(None, max_length=255, description="Optional label for the document"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get upload URL for an 'other' document (Manager/Executive/Partner)."""
    filename = sanitize_filename(filename)

    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    validate_file_type(filename, content_type)

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Other docs can be uploaded in FILING, PAYMENT, or COMPLETED states
    if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload other documents in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
        )

    # Fetch client name for readable MinIO path
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()

    from app.services.storage_service import generate_object_key
    object_key = generate_object_key(
        client_id=str(filing.client_id),
        financial_year=filing.financial_year,
        folder="other_documents",
        filename=filename,
        client_name=client_user.full_name if client_user else "",
    )

    upload_url = get_presigned_upload_url(object_key, content_type)

    return {
        "upload_url": upload_url,
        "object_key": object_key,
    }


# ─── POST /storage/other-doc/confirm ────────────────────────
@router.post("/other-doc/confirm", response_model=dict)
async def confirm_other_doc_upload(
    filing_id: UUID = Query(...),
    object_key: str = Query(...),
    filename: str = Query(...),
    content_type: str = Query(...),
    file_size: int = Query(..., gt=0),
    label: str = Query(None, max_length=255, description="Optional label for the document"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm upload of an 'other' document. Multiple docs allowed per filing."""
    filename = sanitize_filename(filename)

    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    validate_file_type(filename, content_type)
    validate_file_size(file_size)

    from app.config import settings
    from app.models.filing_other_doc import FilingOtherDoc
    from app.services.audit_service import record_audit_event
    from app.enums import AuditEventType
    from datetime import datetime

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    if filing.status not in (FilingStatus.FILING, FilingStatus.PAYMENT, FilingStatus.COMPLETED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot upload other documents in {filing.status.value} state. Filing must be in FILING, PAYMENT, or COMPLETED.",
        )

    # Validate object_key belongs to this client's other_documents folder
    client_user_result = await db.execute(select(User).where(User.id == filing.client_id))
    client_user = client_user_result.scalar_one_or_none()
    try:
        validate_object_key_prefix(
            object_key, str(filing.client_id),
            client_user.full_name if client_user else "",
            f"ITR-{filing.financial_year}/other_documents",
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Create StoredFile record
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
        stored_file.uploaded_at = datetime.utcnow()
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

    # Create FilingOtherDoc record (always new — multiple allowed)
    other_doc = FilingOtherDoc(
        filing_id=filing_id,
        file_id=stored_file.id,
        label=label,
        uploaded_by=current_user.id,
    )
    db.add(other_doc)
    await db.flush()

    # Audit
    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_UPLOADED,
        actor_id=current_user.id,
        client_id=filing.client_id,
        filing_id=filing_id,
        details={"doc_type": "OTHER", "filename": filename, "label": label},
    )

    await db.commit()

    return {
        "message": "Other document uploaded successfully",
        "id": str(other_doc.id),
        "file_id": str(stored_file.id),
    }


# ─── GET /storage/other-docs/{filing_id} ────────────────────
@router.get("/other-docs/{filing_id}", response_model=list[dict])
async def get_other_docs(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get other docs for a filing.
    Clients can only see these once filing is COMPLETED.
    """
    from app.models.filing_other_doc import FilingOtherDoc

    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Clients can only view other documents after COMPLETED state
    if current_user.role == UserRole.CLIENT:
        if filing.status != FilingStatus.COMPLETED:
            return []

    result = await db.execute(
        select(FilingOtherDoc).where(FilingOtherDoc.filing_id == filing_id)
        .order_by(FilingOtherDoc.uploaded_at.desc())
    )
    docs = result.scalars().all()

    items = []
    for doc in docs:
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
        stored = file_result.scalar_one_or_none()
        items.append({
            "id": str(doc.id),
            "file_id": str(doc.file_id),
            "label": doc.label,
            "filename": stored.original_filename if stored else None,
            "content_type": stored.content_type if stored else None,
            "file_size": stored.file_size_bytes if stored else None,
            "uploaded_by": str(doc.uploaded_by),
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        })

    return items


# ─── DELETE /storage/other-doc/{doc_id} ──────────────────────
@router.delete("/other-doc/{doc_id}", response_model=dict)
async def delete_other_doc(
    doc_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an 'other' document (Manager/Executive/Partner)."""
    if current_user.role not in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    from app.models.filing_other_doc import FilingOtherDoc

    result = await db.execute(select(FilingOtherDoc).where(FilingOtherDoc.id == doc_id))
    other_doc = result.scalar_one_or_none()
    if not other_doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    # Verify access to the filing's client
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == other_doc.filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    await db.delete(other_doc)
    await db.commit()

    return {"message": "Document deleted successfully"}
