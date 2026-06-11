"""Service — Text-field placeholder management (assign, fill, approve, reject)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditEventType, TextFieldStatus
from app.models.filing_text_field import FilingTextField
from app.models.master_text_field_type import MasterTextFieldType
from app.services.audit_service import record_audit_event


async def assign_text_field_placeholders(
    db: AsyncSession,
    filing_id: UUID,
    field_type_ids: list[UUID],
    assigned_by: UUID,
) -> list[FilingTextField]:
    """Create text-field placeholders for a filing.

    Idempotent: skips field types that already have at least one placeholder.
    """
    existing_result = await db.execute(
        select(FilingTextField).where(FilingTextField.filing_id == filing_id)
    )
    existing = existing_result.scalars().all()
    existing_type_ids: set[UUID] = {f.field_type_id for f in existing}

    placeholders = list(existing)
    for ftype_id in field_type_ids:
        if ftype_id in existing_type_ids:
            continue
        placeholder = FilingTextField(
            filing_id=filing_id,
            field_type_id=ftype_id,
            status=TextFieldStatus.PENDING,
            assigned_by=assigned_by,
        )
        db.add(placeholder)
        placeholders.append(placeholder)
        existing_type_ids.add(ftype_id)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.TEXT_FIELD_PLACEHOLDER_CREATED,
            actor_id=assigned_by,
            filing_id=filing_id,
            details={"field_type_id": str(ftype_id)},
        )

    await db.flush()
    return placeholders


async def set_text_field_value(
    db: AsyncSession,
    field: FilingTextField,
    value: str,
    actor_id: UUID,
) -> FilingTextField:
    """Set/update the value on a text-field placeholder.

    - PENDING -> FILLED.
    - FILLED / REJECTED / APPROVED -> FILLED (re-edit). APPROVED reverts to FILLED.
    """
    field.value = value
    field.filled_at = datetime.utcnow()
    field.filled_by = actor_id
    field.status = TextFieldStatus.FILLED
    field.rejection_reason = None
    field.reviewed_at = None
    field.reviewed_by = None

    await record_audit_event(
        db=db,
        event_type=AuditEventType.TEXT_FIELD_FILLED,
        actor_id=actor_id,
        filing_id=field.filing_id,
        details={"field_id": str(field.id), "field_type_id": str(field.field_type_id)},
    )

    await db.flush()
    return field


async def approve_text_fields(
    db: AsyncSession,
    field_ids: list[UUID],
    reviewer_id: UUID,
) -> list[FilingTextField]:
    """Approve one or more FILLED text fields. Skips fields not in FILLED state."""
    result = await db.execute(
        select(FilingTextField).where(FilingTextField.id.in_(field_ids))
    )
    fields = result.scalars().all()
    approved: list[FilingTextField] = []
    now = datetime.utcnow()
    for f in fields:
        if f.status != TextFieldStatus.FILLED:
            continue
        f.status = TextFieldStatus.APPROVED
        f.reviewed_at = now
        f.reviewed_by = reviewer_id
        f.rejection_reason = None
        approved.append(f)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.TEXT_FIELD_APPROVED,
            actor_id=reviewer_id,
            filing_id=f.filing_id,
            details={"field_id": str(f.id), "field_type_id": str(f.field_type_id)},
        )

    await db.flush()
    return approved


async def reject_text_fields(
    db: AsyncSession,
    rejections: list[tuple[UUID, str]],
    reviewer_id: UUID,
) -> list[FilingTextField]:
    """Reject one or more FILLED text fields with a reason."""
    field_ids = [fid for fid, _ in rejections]
    reason_map = dict(rejections)
    result = await db.execute(
        select(FilingTextField).where(FilingTextField.id.in_(field_ids))
    )
    fields = result.scalars().all()
    rejected: list[FilingTextField] = []
    now = datetime.utcnow()
    for f in fields:
        if f.status != TextFieldStatus.FILLED:
            continue
        f.status = TextFieldStatus.REJECTED
        f.reviewed_at = now
        f.reviewed_by = reviewer_id
        f.rejection_reason = reason_map.get(f.id)
        rejected.append(f)

        await record_audit_event(
            db=db,
            event_type=AuditEventType.TEXT_FIELD_REJECTED,
            actor_id=reviewer_id,
            filing_id=f.filing_id,
            details={
                "field_id": str(f.id),
                "field_type_id": str(f.field_type_id),
                "reason": reason_map.get(f.id),
            },
        )

    await db.flush()
    return rejected
