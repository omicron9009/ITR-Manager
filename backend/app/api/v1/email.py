"""API v1 — Email configuration endpoints (Partner only)."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_partner
from app.database import get_db
from app.models.email_config import EmailConfig
from app.models.user import User
from app.schemas.email_config import (
    EmailConfigAuthUrlResponse,
    EmailConfigResponse,
    EmailConfigSetupRequest,
    EmailConfigTestRequest,
    EmailConfigTokenRequest,
)

router = APIRouter()


# ─── GET /email/config — Get current config status ──────────
@router.get("/config", response_model=EmailConfigResponse)
async def get_email_config(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get current email configuration status (Partner only)."""
    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Email not configured yet. Use POST /email/setup to configure.",
        )

    return EmailConfigResponse(
        id=config.id,
        sender_email=config.sender_email,
        is_configured=bool(config.credentials_json and config.token_json),
        configured_by=config.configured_by,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


# ─── POST /email/setup — Upload Gmail OAuth credentials ─────
@router.post("/setup", response_model=EmailConfigResponse)
async def setup_email_credentials(
    payload: EmailConfigSetupRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Set up email configuration (Partner only).

    The partner provides:
    - sender_email: The Gmail address to send notifications from
    - credentials_json: The OAuth2 client credentials JSON content
      (downloaded from Google Cloud Console → Credentials → OAuth 2.0 Client ID)
    """
    # Validate that credentials_json is valid JSON
    try:
        creds_data = json.loads(payload.credentials_json)
        if "installed" not in creds_data and "web" not in creds_data:
            raise ValueError("Invalid OAuth credentials format")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid credentials JSON: {str(e)}",
        )

    # Deactivate any existing config
    existing_result = await db.execute(select(EmailConfig))
    for existing in existing_result.scalars().all():
        await db.delete(existing)

    # Create new config
    email_config = EmailConfig(
        sender_email=payload.sender_email,
        credentials_json=payload.credentials_json,
        token_json=None,
        configured_by=current_user.id,
    )
    db.add(email_config)
    await db.flush()

    return EmailConfigResponse(
        id=email_config.id,
        sender_email=email_config.sender_email,
        is_configured=False,  # Token not yet set
        configured_by=email_config.configured_by,
        created_at=email_config.created_at,
        updated_at=email_config.updated_at,
    )


# ─── POST /email/auth-url — Get OAuth authorization URL ─────
@router.post("/auth-url", response_model=EmailConfigAuthUrlResponse)
async def get_email_auth_url(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate the Gmail OAuth authorization URL (Partner only).
    
    After calling POST /email/setup, the partner calls this endpoint
    to get a URL. Open the URL in a browser, authorize, then use the
    returned auth code with POST /email/authorize.
    """
    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config or not config.credentials_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email credentials not configured. Call POST /email/setup first.",
        )

    try:
        from google_auth_oauthlib.flow import Flow

        creds_data = json.loads(config.credentials_json)
        flow = Flow.from_client_config(
            creds_data,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        auth_url, _ = flow.authorization_url(prompt="consent")

        return EmailConfigAuthUrlResponse(
            auth_url=auth_url,
            message="Open this URL in a browser, authorize, then paste the code in POST /email/authorize",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate auth URL: {str(e)}",
        )


# ─── POST /email/authorize — Complete OAuth with auth code ───
@router.post("/authorize", response_model=EmailConfigResponse)
async def authorize_email(
    auth_code: str,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Complete Gmail OAuth authorization (Partner only).

    After visiting the auth URL and authorizing, the partner
    provides the authorization code here to generate the token.
    """
    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config or not config.credentials_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email credentials not configured.",
        )

    try:
        from google_auth_oauthlib.flow import Flow

        creds_data = json.loads(config.credentials_json)
        flow = Flow.from_client_config(
            creds_data,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
            redirect_uri="urn:ietf:wg:oauth:2.0:oob",
        )
        flow.fetch_token(code=auth_code)
        creds = flow.credentials

        # Store the token JSON
        config.token_json = creds.to_json()
        await db.flush()

        return EmailConfigResponse(
            id=config.id,
            sender_email=config.sender_email,
            is_configured=True,
            configured_by=config.configured_by,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Authorization failed: {str(e)}",
        )


# ─── POST /email/token — Direct token upload (alternative) ──
@router.post("/token", response_model=EmailConfigResponse)
async def upload_email_token(
    payload: EmailConfigTokenRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Directly upload an OAuth token JSON (Partner only).

    Alternative to the auth-url → authorize flow. If the partner
    already has a token JSON (generated locally), they can upload it directly.
    """
    # Validate JSON
    try:
        json.loads(payload.token_json)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid token JSON format.",
        )

    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email credentials not configured. Call POST /email/setup first.",
        )

    config.token_json = payload.token_json
    await db.flush()

    return EmailConfigResponse(
        id=config.id,
        sender_email=config.sender_email,
        is_configured=True,
        configured_by=config.configured_by,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


# ─── POST /email/test — Send a test email ───────────────────
@router.post("/test")
async def test_email_config(
    payload: EmailConfigTestRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Send a test email to verify the configuration works (Partner only)."""
    from app.services.email_service import send_email

    success = await send_email(
        to_email=payload.test_recipient,
        subject="ITR Platform — Email Configuration Test",
        body_html="<h2>Email configuration is working!</h2><p>This is a test email from the ITR Filing Platform.</p>",
        db=db,
    )

    if success:
        return {"message": "Test email sent successfully", "recipient": payload.test_recipient}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send test email. Check credentials and token.",
        )
