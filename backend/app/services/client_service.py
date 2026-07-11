"""Service — Client management (registration, activation, profile)."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AccountStatus, AuditEventType, UserRole
from app.models.client_profile import ClientProfile
from app.models.client_income_heads import ClientIncomeHeads
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.declaration_service import generate_declaration_pdf
from app.services.notification_service import create_notification
from app.services.storage_service import create_client_directory, ensure_bucket_exists, upload_declaration_pdf


async def register_client(
    db: AsyncSession,
    email: str,
    full_name: str,
    password_hash: str,
    phone_number: Optional[str] = None,
    income_heads: Optional[dict] = None,
    referral_source: Optional[str] = None,
    referral_source_other: Optional[str] = None,
    city: Optional[str] = None,
) -> User:
    """Register a new client. Account is auto-activated on registration."""
    # Check if email already exists
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        phone_number=phone_number,
        role=UserRole.CLIENT,
        account_status=AccountStatus.ACTIVE,
        activated_at=now,
    )
    db.add(user)
    await db.flush()

    # Create empty client profile with declaration timestamp
    profile = ClientProfile(
        user_id=user.id,
        declaration_accepted_at=now,
        referral_source=referral_source,
        referral_source_other=referral_source_other,
        city=city,
    )
    db.add(profile)

    # Store income heads
    if income_heads:
        heads = ClientIncomeHeads(user_id=user.id, **income_heads)
    else:
        heads = ClientIncomeHeads(user_id=user.id)
    db.add(heads)

    # Generate and upload declaration PDF to MinIO
    pdf_bytes = generate_declaration_pdf(
        full_name=full_name,
        email=email,
        phone_number=phone_number,
        accepted_at=now,
    )
    upload_declaration_pdf(
        client_id=str(user.id),
        client_name=full_name,
        pdf_bytes=pdf_bytes,
    )

    # Record audit
    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_REGISTERED,
        actor_id=user.id,
        client_id=user.id,
        details={"email": email, "full_name": full_name},
    )

    # Notify Partner
    partner = await _get_partner(db)
    if partner:
        await create_notification(
            db=db,
            user_id=partner.id,
            title=f"{full_name} — New Client Registered",
            message=f"A new client ({full_name}) has registered and their account is now active.",
            related_client_id=user.id,
            client_name=full_name,
            action_url_path=f"/clients/{user.id}",
            cta_label="View Client",
            extra_details={"Email": email},
        )

    await db.flush()
    return user


async def activate_client(
    db: AsyncSession,
    client_id: UUID,
    activated_by: UUID,
    ip_address: Optional[str] = None,
    professional_fee=None,
    no_fees_applicable: bool = False,
) -> User:
    """Activate a client account (Partner action)."""
    from fastapi import HTTPException, status as http_status

    result = await db.execute(select(User).where(User.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        from app.core.exceptions import ClientNotFoundError
        raise ClientNotFoundError()

    # Verify account is in PENDING_VERIFICATION state before activating
    if client.account_status != AccountStatus.PENDING_VERIFICATION:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Client account is in '{client.account_status.value}' state, not PENDING_VERIFICATION. Cannot activate.",
        )

    client.account_status = AccountStatus.ACTIVE
    client.is_active = True
    client.activated_at = datetime.now(timezone.utc)
    client.activated_by = activated_by

    # Store professional fee and no_fees_applicable on profile if provided
    if professional_fee is not None or no_fees_applicable:
        profile_result = await db.execute(
            select(ClientProfile).where(ClientProfile.user_id == client_id)
        )
        profile = profile_result.scalar_one_or_none()
        if profile:
            if no_fees_applicable:
                profile.no_fees_applicable = True
                profile.professional_fee = None  # Clear fee when no fees applicable
            elif professional_fee is not None:
                profile.professional_fee = professional_fee

    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_ACTIVATED,
        actor_id=activated_by,
        client_id=client_id,
        ip_address=ip_address,
    )

    await create_notification(
        db=db,
        user_id=client_id,
        title="Account Verified",
        message="Your account has been successfully verified. You can now log in and initiate your ITR filing. Please complete the onboarding form to get started.",
        action_url_path="/onboarding/form",
        cta_label="Complete Onboarding",
    )

    # Notify Partner if fee not set (and no_fees_applicable is not set)
    if professional_fee is None and not no_fees_applicable:
        partner = await _get_partner(db)
        if partner:
            await create_notification(
                db=db,
                user_id=partner.id,
                title=f"{client.full_name} — Professional Fee Pending",
                message=f"Professional fee has not been set for client {client.full_name}. Please set the fee so the client can initiate their filing.",
                related_client_id=client_id,
                client_name=client.full_name,
                action_url_path=f"/clients/{client_id}",
                cta_label="Set Professional Fee",
            )

    # Create the client's base directory in MinIO
    ensure_bucket_exists()
    create_client_directory(str(client_id), client.full_name)

    await db.flush()
    # Invalidate auth user cache so the client's new ACTIVE status is honored immediately
    from app.core.cache import NS, bump_version
    await bump_version(NS.USER_BY_ID)
    await bump_version(NS.DASHBOARD_SUMMARY)
    await bump_version(NS.CLIENT_LIST)
    return client


async def reject_client(
    db: AsyncSession,
    client_id: UUID,
    rejected_by: UUID,
    reason: str,
    ip_address: Optional[str] = None,
) -> User:
    """Reject a client registration (Partner action)."""
    result = await db.execute(select(User).where(User.id == client_id))
    client = result.scalar_one_or_none()
    if not client:
        from app.core.exceptions import ClientNotFoundError
        raise ClientNotFoundError()

    client.account_status = AccountStatus.REJECTED
    client.rejection_reason = reason

    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_REJECTED,
        actor_id=rejected_by,
        client_id=client_id,
        details={"reason": reason},
        ip_address=ip_address,
    )

    await create_notification(
        db=db,
        user_id=client_id,
        title="Registration Rejected",
        message=f"We regret to inform you that your registration has been rejected. Reason: {reason}. If you believe this is an error, please contact us.",
        extra_details={"Reason": reason},
    )

    await db.flush()
    from app.core.cache import NS, bump_version
    await bump_version(NS.USER_BY_ID)
    await bump_version(NS.CLIENT_LIST)
    return client


async def _get_partner(db: AsyncSession) -> Optional[User]:
    """Get the Partner user."""
    result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    return result.scalar_one_or_none()


async def create_client_by_staff(
    db: AsyncSession,
    email: str,
    full_name: str,
    password_hash: str,
    created_by: User,
    phone_number: Optional[str] = None,
    income_heads: Optional[dict] = None,
    city: Optional[str] = None,
    manager_id: Optional["UUID"] = None,
) -> User:
    """Create a client account on behalf of a manager/partner.

    The client is auto-activated with referral_source=DIRECTED_BY_FIRM.
    If the creator is a Manager, the client is auto-assigned to that manager.
    If the creator is a Partner and manager_id is provided, the client is assigned to that manager.
    """
    from app.enums import ReferralSource
    from app.models.manager_client_assignment import ManagerClientAssignment

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    now = datetime.now(timezone.utc)
    user = User(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        phone_number=phone_number,
        role=UserRole.CLIENT,
        account_status=AccountStatus.ACTIVE,
        activated_at=now,
        activated_by=created_by.id,
    )
    db.add(user)
    await db.flush()

    profile = ClientProfile(
        user_id=user.id,
        referral_source=ReferralSource.DIRECTED_BY_FIRM,
        city=city,
        created_by_staff_id=created_by.id,
    )
    db.add(profile)

    if income_heads:
        heads = ClientIncomeHeads(user_id=user.id, **income_heads)
    else:
        heads = ClientIncomeHeads(user_id=user.id)
    db.add(heads)

    await record_audit_event(
        db=db,
        event_type=AuditEventType.ACCOUNT_REGISTERED,
        actor_id=created_by.id,
        client_id=user.id,
        details={
            "email": email,
            "full_name": full_name,
            "created_by": created_by.full_name,
            "created_by_role": created_by.role.value,
        },
    )

    # Create MinIO directory
    ensure_bucket_exists()
    create_client_directory(str(user.id), full_name)

    # Auto-assign to manager
    if created_by.role == UserRole.MANAGER:
        assignment = ManagerClientAssignment(
            manager_id=created_by.id,
            client_id=user.id,
            assigned_by=created_by.id,
        )
        db.add(assignment)
    elif created_by.role == UserRole.PARTNER and manager_id:
        # Validate the manager exists, is active, and has the MANAGER role
        mgr_result = await db.execute(
            select(User).where(User.id == manager_id, User.role == UserRole.MANAGER)
        )
        manager = mgr_result.scalar_one_or_none()
        if not manager:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Specified manager not found or is not a manager.",
            )
        if manager.account_status != AccountStatus.ACTIVE:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Specified manager account is not active.",
            )
        assignment = ManagerClientAssignment(
            manager_id=manager_id,
            client_id=user.id,
            assigned_by=created_by.id,
        )
        db.add(assignment)

    # Notify partner about new client
    partner = await _get_partner(db)
    if partner and partner.id != created_by.id:
        await create_notification(
            db=db,
            user_id=partner.id,
            title=f"{full_name} — New Client Created",
            message=f"A new client ({full_name}) has been created by {created_by.full_name} ({created_by.role.value}).",
            related_client_id=user.id,
            client_name=full_name,
            action_url_path=f"/clients/{user.id}",
            cta_label="View Client",
            extra_details={"Email": email, "Created By": created_by.full_name},
        )

    await db.flush()
    return user
