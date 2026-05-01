"""Core — Security: JWT validation via Authentik JWKS."""

from typing import Optional
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.enums import UserRole
from app.models.user import User

security_scheme = HTTPBearer()

# Cache JWKS keys in memory
_jwks_cache: Optional[dict] = None


async def get_jwks() -> dict:
    """Fetch and cache JWKS from Authentik."""
    global _jwks_cache
    if _jwks_cache is None:
        async with httpx.AsyncClient() as client:
            response = await client.get(settings.AUTHENTIK_JWKS_URL)
            response.raise_for_status()
            _jwks_cache = response.json()
    return _jwks_cache


def invalidate_jwks_cache():
    """Invalidate cached JWKS keys (call on key rotation)."""
    global _jwks_cache
    _jwks_cache = None


async def decode_token(token: str) -> dict:
    """Decode and validate a JWT token against Authentik's JWKS."""
    try:
        jwks = await get_jwks()
        # Extract unverified header to find matching key
        unverified_header = jwt.get_unverified_header(token)
        rsa_key = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == unverified_header.get("kid"):
                rsa_key = key
                break

        if not rsa_key:
            # Try refreshing JWKS cache
            invalidate_jwks_cache()
            jwks = await get_jwks()
            for key in jwks.get("keys", []):
                if key.get("kid") == unverified_header.get("kid"):
                    rsa_key = key
                    break

        if not rsa_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unable to find matching key for token validation",
            )

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=settings.AUTHENTIK_AUDIENCE,
            issuer=settings.AUTHENTIK_ISSUER,
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
    payload = await decode_token(credentials.credentials)

    subject_id = payload.get("sub")
    if not subject_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject claim",
        )

    result = await db.execute(
        select(User).where(User.authentik_subject_id == subject_id, User.is_active == True)
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
