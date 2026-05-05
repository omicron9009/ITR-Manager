"""API v1 — Auth endpoints (login, user info)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_current_user, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.user import LoginRequest, TokenResponse, UserResponse

router = APIRouter()

# 1. Define the schema right here
class TokenExchangeRequest(BaseModel):
    code: str

@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate with email and password, returns a JWT access token."""
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
    return TokenResponse(access_token=token)


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

@router.post("/exchange")
async def exchange_code_for_token(request: TokenExchangeRequest):
    # DEBUG PRINTS - Check your terminal!
    print(f"DEBUG: Client ID: {settings.AUTHENTIK_CLIENT_ID}")
    print(f"DEBUG: Secret Length: {len(settings.AUTHENTIK_CLIENT_SECRET)}") 
    print(f"DEBUG: Code received: {request.code}")

    token_url = f"{settings.AUTHENTIK_BASE_URL}/application/o/token/"
    
    data = {
        "grant_type": "authorization_code",
        "code": request.code,
        "client_id": settings.AUTHENTIK_CLIENT_ID,
        "client_secret": settings.AUTHENTIK_CLIENT_SECRET,
        "redirect_uri": "http://localhost:3000/api/auth/callback/authentik",
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        
        if response.status_code != 200:
            print(f"DEBUG: Authentik Error: {response.text}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=response.json()
            )
            
        return response.json()