"""Service — Document management (placeholders, upload, review)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status as http_status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditEventType, DocumentStatus, FilingStatus
from app.models.filing import ITRFiling
from app.models.filing_document import FilingDocument
from app.models.master_document_type import MasterDocumentType
from app.models.stored_file import StoredFile
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification


async def assign_document_placeholders(
    db: AsyncSession,
    filing_id: UUID,
    document_type_ids: list[UUID],
    assigned_by: UUID,
) -> list[FilingDocument]:
    """Create/update document placeholders for a filing from the master list.

    Idempotent: re-sending a checklist will:
    - Keep existing placeholders that are already UPLOADED/APPROVED/REJECTED
    - Keep existing PENDING_UPLOAD placeholders whose doc type is still in the new list
    - Remove PENDING_UPLOAD placeholders whose doc type is NOT in the new list
    - Create new placeholders for doc types not yet present
    """
    # Fetch existing placeholders for this filing
    existing_result = await db.execute(
        select(FilingDocument).where(FilingDocument.filing_id == filing_id)
    )
    existing_docs = existing_result.scalars().all()
    existing_by_type: dict[UUID, FilingDocument] = {doc.document_type_id: doc for doc in existing_docs}

    new_type_ids_set = set(document_type_ids)

    # Remove PENDING_UPLOAD placeholders not in the new list
    for doc in existing_docs:
        if doc.document_type_id not in new_type_ids_set and doc.status == DocumentStatus.PENDING_UPLOAD:
            await db.delete(doc)

    # Create placeholders for new doc types (skip if already exists)
    placeholders = []
    for doc_type_id in document_type_ids:
        if doc_type_id in existing_by_type:
            # Already exists — keep it as-is
            placeholders.append(existing_by_type[doc_type_id])
            continue

        placeholder = FilingDocument(
            filing_id=filing_id,
            document_type_id=doc_type_id,
            status=DocumentStatus.PENDING_UPLOAD,
            assigned_by=assigned_by,
        )
        db.add(placeholder)
        placeholders.append(placeholder)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_PLACEHOLDER_CREATED,
            actor_id=assigned_by,
            filing_id=filing_id,
            document_id=placeholder.id,
            details={"document_type_id": str(doc_type_id)},
        )

    await db.flush()
    return placeholders


async def record_document_upload(
    db: AsyncSession,
    document_id: UUID,
    file_id: UUID,
    uploaded_by: UUID,
) -> FilingDocument:
    """Record that a client has uploaded a file to a placeholder."""
    result = await db.execute(select(FilingDocument).where(FilingDocument.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        from app.core.exceptions import DocumentNotFoundError
        raise DocumentNotFoundError()

    doc.file_id = file_id
    doc.status = DocumentStatus.UPLOADED
    doc.uploaded_at = datetime.utcnow()

    await record_audit_event(
        db=db,
        event_type=AuditEventType.DOCUMENT_UPLOADED,
        actor_id=uploaded_by,
        filing_id=doc.filing_id,
        document_id=document_id,
        details={"file_id": str(file_id)},
    )

    await db.flush()
    return doc


async def approve_documents(
    db: AsyncSession,
    document_ids: list[UUID],
    reviewed_by: UUID,
) -> list[FilingDocument]:
    """Approve one or more documents. Only UPLOADED documents can be approved."""
    approved = []
    for doc_id in document_ids:
        result = await db.execute(select(FilingDocument).where(FilingDocument.id == doc_id))
        doc = result.scalar_one_or_none()
        if not doc:
            continue

        # Verify filing is in DOCUMENT_UPLOAD or PROCESSING state
        filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
        filing = filing_result.scalar_one_or_none()
        if filing and filing.status not in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Cannot approve documents: Filing is in '{filing.status.value}' state. "
                       f"Documents can only be approved when the filing is in DOCUMENT_UPLOAD or PROCESSING state.",
            )

        # Only UPLOADED docs can be approved
        if doc.status != DocumentStatus.UPLOADED:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Cannot approve document: Document is in '{doc.status.value}' status. "
                       f"Only documents with 'UPLOADED' status can be approved.",
            )

        doc.status = DocumentStatus.APPROVED
        doc.reviewed_by = reviewed_by
        doc.reviewed_at = datetime.utcnow()
        approved.append(doc)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_APPROVED,
            actor_id=reviewed_by,
            filing_id=doc.filing_id,
            document_id=doc_id,
        )

    await db.flush()
    return approved


async def reject_documents(
    db: AsyncSession,
    rejections: list[dict],  # [{"document_id": UUID, "reason": str}]
    reviewed_by: UUID,
    filing_id: UUID,
    client_id: UUID,
) -> list[FilingDocument]:
    """Reject one or more documents with reasons."""
    rejected = []
    rejected_names = []

    for item in rejections:
        result = await db.execute(
            select(FilingDocument).where(FilingDocument.id == item["document_id"])
        )
        doc = result.scalar_one_or_none()
        if not doc:
            continue

        # Verify filing is in DOCUMENT_UPLOAD or PROCESSING state
        filing_check = await db.execute(select(ITRFiling).where(ITRFiling.id == doc.filing_id))
        filing_obj = filing_check.scalar_one_or_none()
        if filing_obj and filing_obj.status not in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Cannot reject documents: Filing is in '{filing_obj.status.value}' state. "
                       f"Documents can only be rejected when the filing is in DOCUMENT_UPLOAD or PROCESSING state.",
            )

        # Only UPLOADED docs can be rejected
        if doc.status != DocumentStatus.UPLOADED:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=f"Cannot reject document: Document is in '{doc.status.value}' status. "
                       f"Only documents with 'UPLOADED' status can be rejected.",
            )

        doc.status = DocumentStatus.REJECTED
        doc.rejection_reason = item["reason"]
        doc.reviewed_by = reviewed_by
        doc.reviewed_at = datetime.utcnow()
        # Reset file_id so client must re-upload
        doc.file_id = None
        doc.uploaded_at = None
        rejected.append(doc)

        # Get document type name for notification
        dt_result = await db.execute(
            select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id)
        )
        dt = dt_result.scalar_one_or_none()
        if dt:
            rejected_names.append(dt.name)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.DOCUMENT_REJECTED,
            actor_id=reviewed_by,
            filing_id=doc.filing_id,
            document_id=doc.id,
            details={"reason": item["reason"]},
        )

    # Notify client about rejections
    if rejected_names:
        await create_notification(
            db=db,
            user_id=client_id,
            title="Documents Need Correction",
            message=f"The following documents need to be re-uploaded: {', '.join(rejected_names)}",
            related_filing_id=filing_id,
        )

    await db.flush()
    return rejected


async def check_all_documents_approved(db: AsyncSession, filing_id: UUID) -> bool:
    """Check if all documents for a filing are approved."""
    result = await db.execute(
        select(func.count()).select_from(FilingDocument).where(
            FilingDocument.filing_id == filing_id,
            FilingDocument.status != DocumentStatus.APPROVED,
        )
    )
    non_approved_count = result.scalar() or 0
    return non_approved_count == 0


async def get_filing_documents_summary(db: AsyncSession, filing_id: UUID) -> dict:
    """Get document status counts for a filing."""
    result = await db.execute(
        select(
            FilingDocument.status,
            func.count(FilingDocument.id),
        )
        .where(FilingDocument.filing_id == filing_id)
        .group_by(FilingDocument.status)
    )
    summary = {status.value: 0 for status in DocumentStatus}
    for row in result.all():
        summary[row[0].value] = row[1]
    return summary
