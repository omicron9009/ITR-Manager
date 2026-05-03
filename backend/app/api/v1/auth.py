"""API v1 — Auth endpoints (user info, token validation)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel  # <--- Need this import
import httpx

from app.core.security import get_current_user
from app.models.user import User
from app.schemas.user import UserResponse
from app.config import settings

router = APIRouter()

# 1. Define the schema right here
class TokenExchangeRequest(BaseModel):
    code: str

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