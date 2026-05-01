"""API v1 — Onboarding form builder and submission."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_client, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AuditEventType, FormFieldType
from app.models.client_profile import ClientProfile
from app.models.onboarding_form_field import OnboardingFormField
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

router = APIRouter()


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
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Add a new field to the onboarding form (Partner only)."""
    # Validate dropdown has options
    if body.field_type == FormFieldType.DROPDOWN and not body.field_options:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dropdown fields must have options",
        )

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
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a form field (Partner only)."""
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
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete (deactivate) a form field (Partner only)."""
    result = await db.execute(select(OnboardingFormField).where(OnboardingFormField.id == field_id))
    field = result.scalar_one_or_none()
    if not field:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Form field not found")

    field.is_active = False
    field.updated_by = current_user.id

    await record_audit_event(
        db=db,
        event_type=AuditEventType.FORM_FIELD_REMOVED,
        actor_id=current_user.id,
        details={"field_id": str(field_id), "field_key": field.field_key},
    )

    await db.flush()
    return {"message": f"Form field '{field.field_label}' deactivated"}


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
