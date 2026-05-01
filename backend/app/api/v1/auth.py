"""API v1 — Auth endpoints (user info, token validation)."""

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user import UserResponse

router = APIRouter()


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
