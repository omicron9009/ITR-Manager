"""API v1 — Email configuration endpoints (Partner only)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_partner
from app.database import get_db
from app.models.email_config import EmailConfig
from app.models.user import User
from app.schemas.email_config import (
    EmailConfigResponse,
    EmailConfigSetupRequest,
    EmailConfigTestRequest,
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
        smtp_host=config.smtp_host,
        smtp_port=config.smtp_port,
        smtp_user=config.smtp_user,
        use_tls=config.use_tls,
        is_configured=True,
        configured_by=config.configured_by,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )


# ─── POST /email/setup — Save SMTP credentials ──────────────
@router.post("/setup", response_model=EmailConfigResponse)
async def setup_email(
    payload: EmailConfigSetupRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Set up SMTP email configuration (Partner only).

    The partner provides:
    - sender_email: The email address to send notifications from
    - smtp_host: SMTP server hostname (default: smtp.gmail.com)
    - smtp_port: SMTP port (default: 587)
    - smtp_user: SMTP username (usually the email address)
    - smtp_password: SMTP password (App Password for Gmail)
    - use_tls: Whether to use STARTTLS (default: true)
    """
    # Deactivate any existing config
    existing_result = await db.execute(select(EmailConfig))
    for existing in existing_result.scalars().all():
        await db.delete(existing)

    # Create new config
    email_config = EmailConfig(
        sender_email=payload.sender_email,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        smtp_user=payload.smtp_user,
        smtp_password=payload.smtp_password,
        use_tls=payload.use_tls,
        configured_by=current_user.id,
    )
    db.add(email_config)
    await db.flush()

    return EmailConfigResponse(
        id=email_config.id,
        sender_email=email_config.sender_email,
        smtp_host=email_config.smtp_host,
        smtp_port=email_config.smtp_port,
        smtp_user=email_config.smtp_user,
        use_tls=email_config.use_tls,
        is_configured=True,
        configured_by=email_config.configured_by,
        created_at=email_config.created_at,
        updated_at=email_config.updated_at,
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
            detail="Failed to send test email. Check SMTP credentials.",
        )
