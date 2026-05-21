"""API v1 — Auth endpoints (login, password management, user info)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
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

    current_user.password_hash = hash_password(body.new_password)
    await db.commit()

    return {"message": "Password changed successfully."}


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


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
):
    """Get the current authenticated user's information."""
    return current_user


@router.get("/health")
async def health_check():
    """Public health check endpoint."""
    return {"status": "healthy", "service": "ITR Filing Platform API"}