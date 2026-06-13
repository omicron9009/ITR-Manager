"""Core — Security: Local JWT authentication with password hashing."""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import NS, bump_version
from app.database import get_db
from app.enums import UserRole
from app.models.user import User

security_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(subject_id: UUID, role: str) -> str:
    """Create a signed JWT access token.

    DASHBOARD_USER tokens use a long TTL (``JWT_DASHBOARD_TOKEN_EXPIRE_MINUTES``,
    default 30 days) so TV/kiosk dashboards don't get logged out. All other
    roles use the standard ``JWT_ACCESS_TOKEN_EXPIRE_MINUTES`` (default 60 min).
    """
    if role == UserRole.DASHBOARD_USER.value:
        ttl_minutes = settings.JWT_DASHBOARD_TOKEN_EXPIRE_MINUTES
    else:
        ttl_minutes = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    payload = {
        "sub": str(subject_id),
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a locally-issued JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {str(e)}",
        )


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate the current user from the JWT token."""
    payload = decode_token(credentials.credentials)

    subject_id = payload.get("sub")
    if not subject_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    # Auth must always reflect the latest DB state — a single PK lookup is
    # cheap and avoids the staleness window inherent to multi-worker caches
    # (e.g. a partner activating a client must be visible to the next request
    # served by ANY worker, with zero delay).
    result = await db.execute(
        select(User).where(User.id == UUID(subject_id), User.is_active == True)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Store user in request state for downstream use
    request.state.current_user = user
    return user


async def invalidate_user_cache(user_id: UUID | str) -> None:
    """Best-effort invalidation hook.

    Auth itself no longer caches users (see ``get_current_user``). This
    function remains so callers that bump other user-derived caches keep
    working without changes.
    """
    await bump_version(NS.USER_BY_ID)


async def get_current_active_client(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is an active client."""
    if current_user.role != UserRole.CLIENT:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client access required")
    if current_user.account_status.value != "ACTIVE":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account not yet activated")
    return current_user


async def get_current_executive(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is an executive."""
    if current_user.role != UserRole.EXECUTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Executive access required")
    return current_user


async def get_current_partner(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is the Partner."""
    if current_user.role != UserRole.PARTNER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Partner access required")
    return current_user


async def get_current_executive_or_partner(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is either Executive or Partner."""
    if current_user.role not in (UserRole.EXECUTIVE, UserRole.PARTNER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Executive or Partner access required",
        )
    return current_user


async def get_current_manager_or_partner(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is either Manager or Partner."""
    if current_user.role not in (UserRole.MANAGER, UserRole.PARTNER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or Partner access required",
        )
    return current_user


async def get_current_manager_executive_or_partner(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is Manager, Executive, or Partner."""
    if current_user.role not in (UserRole.MANAGER, UserRole.EXECUTIVE, UserRole.PARTNER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager, Executive, or Partner access required",
        )
    return current_user


async def get_current_dashboard_user_or_partner(
    current_user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user is either DASHBOARD_USER or Partner."""
    if current_user.role not in (UserRole.DASHBOARD_USER, UserRole.PARTNER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dashboard or Partner access required",
        )
    return current_user
