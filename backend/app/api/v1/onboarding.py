"""API v1 — Onboarding form builder and submission."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_manager_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AuditEventType, FormFieldType, UserRole
from app.models.client_profile import ClientProfile
from app.models.onboarding_form_field import OnboardingFormField
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.onboarding import (
    FormFieldCreateRequest,
    FormFieldListResponse,
    FormFieldResponse,
    FormFieldUpdateRequest,
    OnboardingFormResponse,
    OnboardingFormSubmitRequest,
)
from app.services.audit_service import record_audit_event
from app.services.storage_service import get_presigned_download_url

router = APIRouter()


async def _resolve_file_fields(
    db: AsyncSession,
    fields: list,
    form_data: dict,
) -> dict:
    """For FILE-type fields, replace the stored UUID with {file_id, filename, download_url}."""
    if not form_data:
        return form_data

    resolved = dict(form_data)
    file_field_keys = {f.field_key for f in fields if f.field_type == FormFieldType.FILE}

    for key in file_field_keys:
        value = resolved.get(key)
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
            resolved[key] = {
                "file_id": str(stored_file.id),
                "filename": stored_file.original_filename,
                "content_type": stored_file.content_type,
                "file_size_bytes": stored_file.file_size_bytes,
                "download_url": download_url,
            }

    return resolved


# ═══════════════════════════════════════════════════════════════
# FORM BUILDER (Partner Only)
# ═══════════════════════════════════════════════════════════════


@router.get("/fields", response_model=FormFieldListResponse)
async def list_form_fields(
    include_inactive: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all onboarding form fields."""
    query = select(OnboardingFormField)
    if not include_inactive:
        query = query.where(OnboardingFormField.is_active == True)
    query = query.order_by(OnboardingFormField.display_order)

    result = await db.execute(query)
    fields = result.scalars().all()

    return FormFieldListResponse(
        items=[FormFieldResponse.model_validate(f) for f in fields],
        total=len(fields),
    )


@router.post("/fields", response_model=FormFieldResponse, status_code=201)
async def create_form_field(
    body: FormFieldCreateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Add a new field to the onboarding form (Manager/Partner)."""
    # Validate dropdown has options
    if body.field_type == FormFieldType.DROPDOWN and not body.field_options:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dropdown fields must have options",
        )

    # Check if a field with the same key already exists (active or inactive)
    existing_result = await db.execute(
        select(OnboardingFormField).where(OnboardingFormField.field_key == body.field_key)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A form field with key '{body.field_key}' already exists",
            )
        else:
            # Reactivate and update the existing inactive field
            existing.field_label = body.field_label
            existing.field_key = body.field_key
            existing.field_type = body.field_type
            existing.field_options = body.field_options
            existing.is_required = body.is_required
            existing.display_order = body.display_order
            existing.is_active = True
            existing.updated_by = current_user.id

            await record_audit_event(
                db=db,
                event_type=AuditEventType.FORM_FIELD_ADDED,
                actor_id=current_user.id,
                details={"field_key": body.field_key, "field_label": body.field_label, "reactivated": True},
            )

            await db.flush()
            return FormFieldResponse.model_validate(existing)

    field = OnboardingFormField(
        field_label=body.field_label,
        field_key=body.field_key,
        field_type=body.field_type,
        field_options=body.field_options,
        is_required=body.is_required,
        display_order=body.display_order,
        created_by=current_user.id,
    )
    db.add(field)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.FORM_FIELD_ADDED,
        actor_id=current_user.id,
        details={"field_key": body.field_key, "field_label": body.field_label},
    )

    await db.flush()
    return FormFieldResponse.model_validate(field)


@router.put("/fields/{field_id}", response_model=FormFieldResponse)
async def update_form_field(
    field_id: UUID,
    body: FormFieldUpdateRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a form field (Manager/Partner)."""
    result = await db.execute(select(OnboardingFormField).where(OnboardingFormField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form field not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(field, key, value)
    field.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.FORM_FIELD_UPDATED,
        actor_id=current_user.id,
        details={"field_id": str(field_id), "changes": update_data},
    )

    await db.flush()
    return FormFieldResponse.model_validate(field)


@router.delete("/fields/{field_id}", response_model=dict)
async def deactivate_form_field(
    field_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Delete a form field (Manager/Partner). Hard-deletes if unused, soft-deletes if referenced."""
    import uuid as uuid_mod
    from sqlalchemy import func as sa_func

    result = await db.execute(select(OnboardingFormField).where(OnboardingFormField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form field not found")

    field_key = field.field_key
    field_label = field.field_label

    # Check if any client profile references this field_key in form_data (JSONB has_key)
    ref_result = await db.execute(
        select(sa_func.count()).select_from(ClientProfile).where(
            ClientProfile.form_data.has_key(field_key)
        )
    )
    ref_count = ref_result.scalar() or 0

    if ref_count == 0:
        # No references — hard delete to free up field_key for re-creation
        await db.delete(field)
    else:
        # Soft delete: mark inactive + rename field_key to avoid unique constraint collision
        field.is_active = False
        field.field_key = f"{field_key}__deleted_{str(uuid_mod.uuid4())[:8]}"
        field.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.FORM_FIELD_REMOVED,
        actor_id=current_user.id,
        details={"field_id": str(field_id), "field_key": field_key},
    )

    await db.flush()
    return {"message": f"Form field '{field_label}' deleted"}


# ═══════════════════════════════════════════════════════════════
# FORM SUBMISSION (Client)
# ═══════════════════════════════════════════════════════════════


@router.get("/form", response_model=OnboardingFormResponse)
async def get_onboarding_form(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the onboarding form with current field definitions and any existing data."""
    # Get active fields
    fields_result = await db.execute(
        select(OnboardingFormField)
        .where(OnboardingFormField.is_active == True)
        .order_by(OnboardingFormField.display_order)
    )
    fields = fields_result.scalars().all()

    # Check if client has submitted
    submitted = False
    submitted_data = None
    submitted_at = None

    if current_user.role == "CLIENT":
        profile_result = await db.execute(
            select(ClientProfile).where(ClientProfile.user_id == current_user.id)
        )
        profile = profile_result.scalar_one_or_none()
        if profile and profile.form_submitted_at:
            submitted = True
            submitted_data = profile.form_data
            submitted_at = profile.form_submitted_at

    # Resolve FILE fields to download URLs
    if submitted_data:
        submitted_data = await _resolve_file_fields(db, fields, submitted_data)

    return OnboardingFormResponse(
        fields=[FormFieldResponse.model_validate(f) for f in fields],
        submitted=submitted,
        submitted_data=submitted_data,
        submitted_at=submitted_at,
    )


@router.get("/form/{client_id}", response_model=OnboardingFormResponse)
async def get_client_onboarding_form(
    client_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the onboarding form data submitted by a specific client.

    - Client: can only view their own.
    - Executive: can view assigned clients only.
    - Partner: can view any client.
    """
    # Clients can only look up their own data
    if current_user.role == UserRole.CLIENT and current_user.id != client_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only view your own onboarding data")

    # Executive/Partner access check
    if current_user.role in (UserRole.EXECUTIVE, UserRole.PARTNER):
        await enforce_client_access(db, current_user, client_id)

    # Get active fields
    fields_result = await db.execute(
        select(OnboardingFormField)
        .where(OnboardingFormField.is_active == True)
        .order_by(OnboardingFormField.display_order)
    )
    fields = fields_result.scalars().all()

    # Get the client's profile
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()

    submitted = False
    submitted_data = None
    submitted_at = None

    if profile and profile.form_submitted_at:
        submitted = True
        submitted_data = profile.form_data
        submitted_at = profile.form_submitted_at

    # Resolve FILE fields to download URLs
    if submitted_data:
        submitted_data = await _resolve_file_fields(db, fields, submitted_data)

    return OnboardingFormResponse(
        fields=[FormFieldResponse.model_validate(f) for f in fields],
        submitted=submitted,
        submitted_data=submitted_data,
        submitted_at=submitted_at,
    )


@router.post("/form/submit", response_model=dict)
async def submit_onboarding_form(
    body: OnboardingFormSubmitRequest,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Submit or update the onboarding form data."""
    result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == current_user.id)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found")

    # ── Validate required fields ──
    required_fields_result = await db.execute(
        select(OnboardingFormField).where(
            OnboardingFormField.is_active == True,
            OnboardingFormField.is_required == True,
        )
    )
    required_fields = required_fields_result.scalars().all()

    missing_fields = []
    for field in required_fields:
        value = body.form_data.get(field.field_key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing_fields.append(field.field_label)

    if missing_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"The following required fields are missing or empty: {', '.join(missing_fields)}",
        )

    # ── Validate dropdown values are in allowed options ──
    dropdown_fields_result = await db.execute(
        select(OnboardingFormField).where(
            OnboardingFormField.is_active == True,
            OnboardingFormField.field_type == FormFieldType.DROPDOWN,
        )
    )
    dropdown_fields = dropdown_fields_result.scalars().all()

    for field in dropdown_fields:
        value = body.form_data.get(field.field_key)
        if value is not None and field.field_options and value not in field.field_options:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid value '{value}' for field '{field.field_label}'. Allowed: {', '.join(field.field_options)}",
            )

    # ── Validate FILE fields reference actual uploaded stored_files ──
    file_fields_result = await db.execute(
        select(OnboardingFormField).where(
            OnboardingFormField.is_active == True,
            OnboardingFormField.field_type == FormFieldType.FILE,
        )
    )
    file_fields = file_fields_result.scalars().all()

    for field in file_fields:
        value = body.form_data.get(field.field_key)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            continue  # skip if empty (required check already handled above)
        # Value must be a valid stored_file UUID owned by this client
        try:
            file_uuid = UUID(value)
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Field '{field.field_label}' must be a valid file ID. Upload the file first via /storage/onboarding-upload-url.",
            )
        file_result = await db.execute(
            select(StoredFile).where(
                StoredFile.id == file_uuid,
                StoredFile.uploaded_by == current_user.id,
            )
        )
        if not file_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"File for field '{field.field_label}' not found or not uploaded by you.",
            )

    # Update profile with form data
    profile.form_data = body.form_data
    profile.form_submitted_at = datetime.utcnow()

    # Extract key fields if present
    if "pan_number" in body.form_data:
        profile.pan_number = body.form_data["pan_number"]
    if "aadhaar_number" in body.form_data:
        profile.aadhaar_number = body.form_data["aadhaar_number"]
    if "date_of_birth" in body.form_data:
        from dateutil.parser import parse
        try:
            profile.date_of_birth = parse(body.form_data["date_of_birth"]).date()
        except (ValueError, TypeError):
            pass
    if "contact_number" in body.form_data:
        profile.contact_number = body.form_data["contact_number"]
    if "address" in body.form_data:
        profile.address = body.form_data["address"]
    if "income_type" in body.form_data:
        profile.income_type = body.form_data["income_type"]
    if "bank_account_details" in body.form_data:
        profile.bank_account_details = body.form_data["bank_account_details"]

    await db.flush()
    return {"message": "Onboarding form submitted successfully"}
