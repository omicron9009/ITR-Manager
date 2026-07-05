"""API v1 — Text-field placeholders (free-form per-filing inputs)."""

from collections import defaultdict
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_filing_access
from app.core.security import (
    get_current_manager_executive_or_partner,
    get_current_manager_or_partner,
    get_current_user,
)
from app.database import get_db
from app.enums import AuditEventType, DocSubCategory, FilingStatus, IncomeHeadCategory, TextFieldStatus, UserRole
from app.models.filing import ITRFiling
from app.models.filing_text_field import FilingTextField
from app.models.master_text_field_type import MasterTextFieldType
from app.models.master_text_field_type_income_head import MasterTextFieldTypeIncomeHead
from app.models.user import User
from app.schemas.document import IncomeHeadMappingItem
from app.schemas.text_field import (
    FilingTextFieldGroupResponse,
    FilingTextFieldListResponse,
    FilingTextFieldResponse,
    MasterTextFieldTypeCreateRequest,
    MasterTextFieldTypeListResponse,
    MasterTextFieldTypeResponse,
    MasterTextFieldTypeUpdateRequest,
    TextFieldApproveRequest,
    TextFieldAssignRequest,
    TextFieldRejectRequest,
    TextFieldValueRequest,
)
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification
from app.services.text_field_service import (
    approve_text_fields,
    assign_text_field_placeholders,
    reject_text_fields,
    set_text_field_value,
)

router = APIRouter()


_OPEN_FILING_STATES = (
    FilingStatus.DOCUMENT_UPLOAD,
    FilingStatus.PROCESSING,
    FilingStatus.HALTED,
)


def _ensure_filing_state_open(filing: ITRFiling) -> None:
    if filing.status not in _OPEN_FILING_STATES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Text fields can only be modified while the filing is in "
                "DOCUMENT_UPLOAD, PROCESSING, or HALTED state."
            ),
        )


# ═══════════════════════════════════════════════════════════════
# MASTER TEXT FIELD TYPES (Manager / Partner)
# ═══════════════════════════════════════════════════════════════


def _serialize_type(field_type: MasterTextFieldType) -> MasterTextFieldTypeResponse:
    """Build response with eagerly-loaded income_head_mappings (matches doc-type pattern)."""
    mappings = [
        IncomeHeadMappingItem(
            income_head=m.income_head,
            sub_category=m.sub_category,
        )
        for m in (field_type.income_head_mappings or [])
    ]
    return MasterTextFieldTypeResponse(
        id=field_type.id,
        name=field_type.name,
        description=field_type.description,
        max_length=field_type.max_length,
        is_active=field_type.is_active,
        display_order=field_type.display_order,
        created_at=field_type.created_at,
        income_head_mappings=mappings,
    )


async def _replace_text_field_mappings(
    db: AsyncSession,
    text_field_type_id: UUID,
    mappings: list[IncomeHeadMappingItem],
) -> None:
    """Delete existing mappings and recreate from list (full-replace).

    Mirror of `_replace_mappings` in documents.py.
    """
    from sqlalchemy import delete as sql_delete

    await db.execute(
        sql_delete(MasterTextFieldTypeIncomeHead).where(
            MasterTextFieldTypeIncomeHead.text_field_type_id == text_field_type_id
        )
    )

    seen: set[IncomeHeadCategory] = set()
    for m in mappings:
        if m.income_head in seen:
            continue  # silently dedupe
        seen.add(m.income_head)
        db.add(
            MasterTextFieldTypeIncomeHead(
                text_field_type_id=text_field_type_id,
                income_head=m.income_head,
                sub_category=m.sub_category,
            )
        )
    await db.flush()


@router.get("/types", response_model=MasterTextFieldTypeListResponse)
async def list_text_field_types(
    include_inactive: bool = Query(False),
    income_head: Optional[IncomeHeadCategory] = Query(None, description="Filter by income head"),
    sub_category: Optional[DocSubCategory] = Query(None, description="Filter by sub-category"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all master text-field types, with optional income-head/sub-category filters."""
    query = select(MasterTextFieldType)
    if not include_inactive:
        query = query.where(MasterTextFieldType.is_active == True)  # noqa: E712

    if income_head is not None or sub_category is not None:
        join_q = select(MasterTextFieldTypeIncomeHead.text_field_type_id).distinct()
        if income_head is not None:
            join_q = join_q.where(MasterTextFieldTypeIncomeHead.income_head == income_head)
        if sub_category is not None:
            join_q = join_q.where(MasterTextFieldTypeIncomeHead.sub_category == sub_category)
        query = query.where(MasterTextFieldType.id.in_(join_q))

    query = query.order_by(MasterTextFieldType.display_order, MasterTextFieldType.name)
    result = await db.execute(query)
    items = result.scalars().unique().all()
    return MasterTextFieldTypeListResponse(
        items=[_serialize_type(i) for i in items],
        total=len(items),
    )


@router.post("/types", response_model=MasterTextFieldTypeResponse, status_code=201)
async def create_text_field_type(
    body: MasterTextFieldTypeCreateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Create a master text-field type (Manager/Partner). Accepts optional income-head mappings."""
    # Uniqueness pre-check (DB also enforces it)
    existing = await db.execute(
        select(MasterTextFieldType).where(MasterTextFieldType.name == body.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A text field type with this name already exists.",
        )

    field_type = MasterTextFieldType(
        name=body.name,
        description=body.description,
        max_length=body.max_length,
        display_order=body.display_order,
        created_by=current_user.id,
    )
    db.add(field_type)
    await db.flush()  # need field_type.id for mappings

    if body.income_head_mappings:
        await _replace_text_field_mappings(db, field_type.id, body.income_head_mappings)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.TEXT_FIELD_TYPE_ADDED,
        actor_id=current_user.id,
        details={
            "name": body.name,
            "max_length": body.max_length,
            "income_head_mappings": [
                {"income_head": m.income_head.value, "sub_category": m.sub_category.value}
                for m in body.income_head_mappings
            ],
        },
    )

    await db.flush()
    await db.refresh(field_type, attribute_names=["income_head_mappings"])
    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_TEXT_FIELD_TYPES)
    await db.commit()
    return _serialize_type(field_type)


@router.put("/types/{type_id}", response_model=MasterTextFieldTypeResponse)
async def update_text_field_type(
    type_id: UUID,
    body: MasterTextFieldTypeUpdateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a master text-field type (Manager/Partner). Pass `income_head_mappings: []` to clear."""
    result = await db.execute(
        select(MasterTextFieldType).where(MasterTextFieldType.id == type_id)
    )
    field_type = result.scalar_one_or_none()
    if not field_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Text field type not found")

    update_data = body.model_dump(exclude_unset=True)
    mappings_payload = update_data.pop("income_head_mappings", None)

    for key, value in update_data.items():
        setattr(field_type, key, value)
    field_type.updated_by = current_user.id

    if mappings_payload is not None:
        # body.income_head_mappings preserves enum types; rebuild from validated body
        await _replace_text_field_mappings(db, field_type.id, body.income_head_mappings or [])

    await record_audit_event(
        db=db,
        event_type=AuditEventType.TEXT_FIELD_TYPE_UPDATED,
        actor_id=current_user.id,
        details={
            "type_id": str(type_id),
            "changes": update_data,
            "mappings_replaced": mappings_payload is not None,
        },
    )

    await db.flush()
    await db.refresh(field_type, attribute_names=["income_head_mappings"])
    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_TEXT_FIELD_TYPES)
    await db.commit()
    return _serialize_type(field_type)


@router.delete("/types/{type_id}", status_code=204)
async def delete_text_field_type(
    type_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a master text-field type (Manager/Partner)."""
    result = await db.execute(
        select(MasterTextFieldType).where(MasterTextFieldType.id == type_id)
    )
    field_type = result.scalar_one_or_none()
    if not field_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Text field type not found")

    if not field_type.is_active:
        return None

    field_type.is_active = False
    field_type.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.TEXT_FIELD_TYPE_REMOVED,
        actor_id=current_user.id,
        details={"type_id": str(type_id), "name": field_type.name},
    )

    from app.core.cache import NS, bump_version
    await bump_version(NS.MASTER_TEXT_FIELD_TYPES)
    await db.commit()
    return None


# ═══════════════════════════════════════════════════════════════
# FILING TEXT-FIELD PLACEHOLDERS
# ═══════════════════════════════════════════════════════════════


def _serialize_field(
    field: FilingTextField,
    type_map: dict[UUID, MasterTextFieldType],
) -> FilingTextFieldResponse:
    ftype = type_map.get(field.field_type_id)
    return FilingTextFieldResponse(
        id=field.id,
        filing_id=field.filing_id,
        field_type_id=field.field_type_id,
        field_type_name=ftype.name if ftype else None,
        field_type_max_length=ftype.max_length if ftype else None,
        field_type_description=ftype.description if ftype else None,
        status=field.status,
        value=field.value,
        rejection_reason=field.rejection_reason,
        filled_at=field.filled_at,
        filled_by=field.filled_by,
        reviewed_at=field.reviewed_at,
        reviewed_by=field.reviewed_by,
        assigned_at=field.assigned_at,
    )


async def _load_filing_or_404(db: AsyncSession, filing_id: UUID) -> ITRFiling:
    result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = result.scalar_one_or_none()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")
    return filing


@router.post("/filings/{filing_id}/assign", response_model=dict)
async def assign_text_fields_to_filing(
    filing_id: UUID,
    body: TextFieldAssignRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign text-field placeholders to a filing (Manager/Executive/Partner)."""
    filing = await _load_filing_or_404(db, filing_id)
    await enforce_filing_access(db, current_user, filing.client_id)
    _ensure_filing_state_open(filing)

    # Validate all field type ids exist and are active
    type_q = await db.execute(
        select(MasterTextFieldType).where(MasterTextFieldType.id.in_(body.field_type_ids))
    )
    types = type_q.scalars().all()
    type_ids = {t.id for t in types if t.is_active}
    invalid = [str(tid) for tid in body.field_type_ids if tid not in type_ids]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"One or more text field types not found or inactive: {invalid}",
        )

    placeholders = await assign_text_field_placeholders(
        db=db,
        filing_id=filing_id,
        field_type_ids=list(type_ids),
        assigned_by=current_user.id,
    )

    # Notify client
    await create_notification(
        db=db,
        user_id=filing.client_id,
        title="Additional Information Requested",
        message="Your team has requested some additional details. Please fill them in to proceed.",
        related_filing_id=filing_id,
    )

    await db.commit()
    return {"message": f"{len(placeholders)} text-field placeholder(s) present", "count": len(placeholders)}


@router.get("/filings/{filing_id}", response_model=FilingTextFieldListResponse)
async def list_filing_text_fields(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all text-field placeholders for a filing."""
    filing = await _load_filing_or_404(db, filing_id)
    await enforce_filing_access(db, current_user, filing.client_id)

    fields_q = await db.execute(
        select(FilingTextField)
        .where(FilingTextField.filing_id == filing_id)
        .order_by(FilingTextField.assigned_at)
    )
    fields = fields_q.scalars().all()

    type_ids = {f.field_type_id for f in fields}
    type_map: dict[UUID, MasterTextFieldType] = {}
    if type_ids:
        types_q = await db.execute(
            select(MasterTextFieldType).where(MasterTextFieldType.id.in_(type_ids))
        )
        type_map = {t.id: t for t in types_q.scalars().all()}

    items = [_serialize_field(f, type_map) for f in fields]

    summary = {"PENDING": 0, "FILLED": 0, "APPROVED": 0, "REJECTED": 0}
    for f in fields:
        summary[f.status.value] += 1

    groups_map: dict[UUID, list[FilingTextFieldResponse]] = defaultdict(list)
    for it in items:
        groups_map[it.field_type_id].append(it)

    groups = [
        FilingTextFieldGroupResponse(
            field_type_id=tid,
            field_type_name=(type_map[tid].name if tid in type_map else "Unknown"),
            fields=field_list,
        )
        for tid, field_list in groups_map.items()
    ]

    all_approved = summary["APPROVED"] == len(fields) and len(fields) > 0

    return FilingTextFieldListResponse(
        items=items,
        groups=groups,
        total=len(fields),
        pending_count=summary["PENDING"],
        filled_count=summary["FILLED"],
        rejected_count=summary["REJECTED"],
        approved_count=summary["APPROVED"],
        all_approved=all_approved,
    )


@router.put("/{field_id}", response_model=FilingTextFieldResponse)
async def update_text_field_value(
    field_id: UUID,
    body: TextFieldValueRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit or update the value on a text-field placeholder.

    - Allowed for Client (own filing) and Manager/Executive/Partner.
    - Sets status -> FILLED.
    - If currently APPROVED, the edit reverts it to FILLED for re-approval.
    - Value length must be <= the field's max_length.
    """
    result = await db.execute(select(FilingTextField).where(FilingTextField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Text field not found")

    filing = await _load_filing_or_404(db, field.filing_id)
    await enforce_filing_access(db, current_user, filing.client_id)
    _ensure_filing_state_open(filing)

    if current_user.role not in (
        UserRole.CLIENT,
        UserRole.PARTNER,
        UserRole.MANAGER,
        UserRole.EXECUTIVE,
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    type_q = await db.execute(
        select(MasterTextFieldType).where(MasterTextFieldType.id == field.field_type_id)
    )
    field_type = type_q.scalar_one_or_none()

    max_length = field_type.max_length if field_type else 200
    if len(body.value) > max_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Value exceeds maximum length of {max_length} characters.",
        )

    is_staff = current_user.role in (UserRole.PARTNER, UserRole.MANAGER, UserRole.EXECUTIVE)
    await set_text_field_value(db=db, field=field, value=body.value, actor_id=current_user.id, on_behalf=is_staff)

    # Notify client when staff fills a text field on their behalf
    if is_staff:
        _field_type_name = field_type.name if field_type else "a text field"
        await create_notification(
            db=db,
            user_id=filing.client_id,
            title="Information Filled on Your Behalf",
            message=f"{current_user.full_name} has filled '{_field_type_name}' on your behalf for FY {filing.financial_year}.",
            related_filing_id=filing.id,
        )

    await db.commit()

    type_map = {field.field_type_id: field_type} if field_type else {}
    return _serialize_field(field, type_map)


@router.post("/approve", response_model=dict)
async def approve_filing_text_fields(
    body: TextFieldApproveRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Approve one or more FILLED text fields (Manager/Executive/Partner)."""
    # Verify all fields belong to filings the user has access to + state is open
    fields_q = await db.execute(
        select(FilingTextField).where(FilingTextField.id.in_(body.field_ids))
    )
    fields = fields_q.scalars().all()
    if not fields:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No text fields found")

    filing_ids = {f.filing_id for f in fields}
    for fid in filing_ids:
        filing = await _load_filing_or_404(db, fid)
        await enforce_filing_access(db, current_user, filing.client_id)
        _ensure_filing_state_open(filing)

    approved = await approve_text_fields(
        db=db,
        field_ids=body.field_ids,
        reviewer_id=current_user.id,
    )

    await db.commit()
    return {"message": f"{len(approved)} text field(s) approved", "count": len(approved)}


@router.post("/reject", response_model=dict)
async def reject_filing_text_fields(
    body: TextFieldRejectRequest,
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Reject one or more FILLED text fields with a reason (Manager/Executive/Partner)."""
    field_ids = [r.field_id for r in body.rejections]
    fields_q = await db.execute(
        select(FilingTextField).where(FilingTextField.id.in_(field_ids))
    )
    fields = fields_q.scalars().all()
    if not fields:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No text fields found")

    filing_ids = {f.filing_id for f in fields}
    notify_client_ids: set[UUID] = set()
    for fid in filing_ids:
        filing = await _load_filing_or_404(db, fid)
        await enforce_filing_access(db, current_user, filing.client_id)
        _ensure_filing_state_open(filing)
        notify_client_ids.add(filing.client_id)

    rejected = await reject_text_fields(
        db=db,
        rejections=[(r.field_id, r.reason) for r in body.rejections],
        reviewer_id=current_user.id,
    )

    # Notify each affected client once
    for client_id in notify_client_ids:
        await create_notification(
            db=db,
            user_id=client_id,
            title="Text Field Rejected",
            message="One or more details you submitted were rejected. Please review and re-submit.",
        )

    await db.commit()
    return {"message": f"{len(rejected)} text field(s) rejected", "count": len(rejected)}


@router.delete("/{field_id}", status_code=200)
async def delete_filing_text_field(
    field_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a text-field placeholder.

    - Allowed only when filing is in DOCUMENT_UPLOAD/PROCESSING/HALTED.
    - Manager/Executive/Partner: only PENDING placeholders.
    - Client: only their own FILLED or REJECTED placeholders, never APPROVED, never PENDING.
      At least one *other* placeholder of the same field type must remain with a value
      (FILLED / REJECTED / APPROVED).
    """
    result = await db.execute(select(FilingTextField).where(FilingTextField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Text field not found")

    filing = await _load_filing_or_404(db, field.filing_id)
    await enforce_filing_access(db, current_user, filing.client_id)
    _ensure_filing_state_open(filing)

    is_client = current_user.role == UserRole.CLIENT
    is_staff = current_user.role in (UserRole.PARTNER, UserRole.MANAGER, UserRole.EXECUTIVE)

    if is_staff:
        if field.status != TextFieldStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove a text field that has already been filled.",
            )
    elif is_client:
        if field.status not in (TextFieldStatus.FILLED, TextFieldStatus.REJECTED):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You can only remove your own filled or rejected entry that has not been approved.",
            )

        siblings_q = await db.execute(
            select(FilingTextField).where(
                FilingTextField.filing_id == field.filing_id,
                FilingTextField.field_type_id == field.field_type_id,
                FilingTextField.id != field.id,
            )
        )
        siblings = siblings_q.scalars().all()
        sibling_with_value_exists = any(
            s.status in (TextFieldStatus.FILLED, TextFieldStatus.REJECTED, TextFieldStatus.APPROVED)
            for s in siblings
        )
        if not sibling_with_value_exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "At least one filled entry must remain for this field type. "
                    "Submit another value before removing this one."
                ),
            )
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    field_type_id = field.field_type_id
    filing_id = field.filing_id
    prev_status = field.status.value

    await db.delete(field)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.TEXT_FIELD_PLACEHOLDER_REMOVED,
        actor_id=current_user.id,
        filing_id=filing_id,
        details={
            "field_id": str(field_id),
            "field_type_id": str(field_type_id),
            "previous_status": prev_status,
            "removed_by_role": current_user.role.value,
        },
    )

    await db.commit()
    return {"message": "Text field removed"}
