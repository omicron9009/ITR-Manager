"""Service — Client management (registration, activation, profile)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AccountStatus, AuditEventType, UserRole
from app.models.client_profile import ClientProfile
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification


async def register_client(
    db: AsyncSession,
    email: str,
    full_name: str,
    password_hash: str,
    pan_document_id: Optional[UUID] = None,
    phone_number: Optional[str] = None,
) -> User:
    """Register a new client. Account starts in PENDING_VERIFICATION."""
    user = User(
        email=email,
        full_name=full_name,
        password_hash=password_hash,
        phone_number=phone_number,
        role=UserRole.CLIENT,
        account_status=AccountStatus.PENDING_VERIFICATION,
        pan_document_id=pan_document_id,
    )
    db.add(user)
    await db.flush()

    # Create empty client profile
    profile = ClientProfile(user_id=user.id)
    db.add(profile)

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
            title="New Client Registration",
            message=f"New client registration pending verification - {full_name}",
            related_client_id=user.id,
        )

    await db.flush()
    return user


async def activate_client(
    db: AsyncSession,
    client_id: UUID,
    activated_by: UUID,
    ip_address: Optional[str] = None,
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
    client.activated_at = datetime.utcnow()
    client.activated_by = activated_by

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
        message="Your account has been verified. You may now initiate your ITR filing.",
    )

    await db.flush()
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
        message=f"Your registration has been rejected. Reason: {reason}",
    )

    await db.flush()
    return client


async def _get_partner(db: AsyncSession) -> Optional[User]:
    """Get the Partner user."""
    result = await db.execute(
        select(User).where(User.role == UserRole.PARTNER, User.is_active == True)
    )
    return result.scalar_one_or_none()
