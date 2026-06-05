"""API v1 — Auth endpoints (login, password management, user info)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    get_current_partner,
    get_current_user,
    hash_password,
    invalidate_user_cache,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    AdminGenerateRecoveryCodesRequest,
    ChangeEmailRequest,
    ChangeEmailResponse,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    ProfileUpdateRequest,
    RecoveryCodesResponse,
    TokenResponse,
    UserResponse,
)
from app.services.recovery_code_service import (
    generate_recovery_codes,
    get_unused_code_count,
    verify_recovery_code,
)

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email and password, returns a JWT access token.

    On first login (or if recovery codes have never been issued), the response
    will include one-time recovery codes that the client should present as a
    downloadable .txt file.
    """
    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    token = create_access_token(subject_id=user.id, role=user.role.value)

    # Issue recovery codes on first login (one-time)
    recovery_codes = None
    if not user.recovery_codes_issued:
        recovery_codes = await generate_recovery_codes(db, user.id)
        await db.commit()

    return TokenResponse(
        access_token=token,
        recovery_codes=recovery_codes,
    )


@router.post("/reset-password", response_model=dict)
async def reset_password(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using email and a valid recovery code (public endpoint, no auth)."""
    result = await db.execute(
        select(User).where(User.email == body.email, User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        # Don't reveal whether the email exists
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or recovery code",
        )

    valid = await verify_recovery_code(db, user.id, body.recovery_code)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or recovery code",
        )

    # Update password
    user.password_hash = hash_password(body.new_password)
    await db.commit()

    remaining = await get_unused_code_count(db, user.id)
    return {
        "message": "Password has been reset successfully.",
        "remaining_recovery_codes": remaining,
    }


@router.post("/change-password", response_model=dict)
async def change_password(
    body: PasswordChangeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change password while authenticated. Requires old password confirmation."""
    if not verify_password(body.old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    # Re-fetch from DB to get a session-tracked instance (current_user may be a
    # transient object reconstructed from cache and not attached to this session).
    db_result = await db.execute(select(User).where(User.id == current_user.id))
    db_user = db_result.scalar_one()
    db_user.password_hash = hash_password(body.new_password)
    await db.commit()
    await invalidate_user_cache(current_user.id)

    return {"message": "Password changed successfully."}


@router.post("/change-email", response_model=ChangeEmailResponse)
async def change_email(
    body: ChangeEmailRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change the authenticated user's email. Requires password confirmation."""
    if not verify_password(body.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password is incorrect",
        )

    # Check if new email is already in use
    existing = await db.execute(
        select(User).where(User.email == body.new_email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already in use by another account",
        )

    # Re-fetch from DB to get a session-tracked instance (current_user may be a
    # transient object reconstructed from cache and not attached to this session).
    db_result = await db.execute(select(User).where(User.id == current_user.id))
    db_user = db_result.scalar_one()
    db_user.email = body.new_email
    await db.commit()
    await invalidate_user_cache(current_user.id)

    return ChangeEmailResponse(
        message="Email updated successfully.",
        email=body.new_email,
    )


@router.post("/regenerate-recovery-codes", response_model=RecoveryCodesResponse)
async def regenerate_recovery_codes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a new set of recovery codes (invalidates all previous codes)."""
    codes = await generate_recovery_codes(db, current_user.id)
    await db.commit()

    return RecoveryCodesResponse(
        codes=codes,
        message="New recovery codes generated. Previous codes are now invalid. Save these securely.",
    )


@router.post("/admin/generate-recovery-codes", response_model=RecoveryCodesResponse)
async def admin_generate_recovery_codes(
    body: AdminGenerateRecoveryCodesRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Admin (Partner) only: Generate new recovery codes for a user by email."""
    result = await db.execute(
        select(User).where(User.email == body.email)
    )
    target_user = result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User with this email not found",
        )

    codes = await generate_recovery_codes(db, target_user.id)
    await db.commit()

    return RecoveryCodesResponse(
        codes=codes,
        message=f"New recovery codes generated for {body.email}. Previous codes are now invalid.",
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    """Get the current authenticated user's information."""
    return current_user


@router.patch("/me/profile", response_model=UserResponse)
async def update_my_profile(
    body: ProfileUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the current user's profile (name). Available to all roles."""
    # Re-fetch from DB to get a session-tracked instance (current_user may be a
    # transient object reconstructed from cache and not attached to this session).
    db_result = await db.execute(select(User).where(User.id == current_user.id))
    db_user = db_result.scalar_one()
    db_user.full_name = body.full_name
    await db.commit()
    await db.refresh(db_user)
    await invalidate_user_cache(current_user.id)
    return db_user


@router.get("/health")
async def health_check():
    """Public health check endpoint."""
    return {"status": "healthy", "service": "ITR Filing Platform API"}