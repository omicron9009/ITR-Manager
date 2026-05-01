"""Service — Email delivery via Google Gmail API (credentials from DB)."""

import base64
import json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)


async def _get_gmail_service(db: AsyncSession):
    """Build Gmail API service from DB-stored credentials."""
    from app.models.email_config import EmailConfig

    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config or not config.credentials_json or not config.token_json:
        logger.warning("Email not configured — no credentials/token in database")
        return None, None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_data = json.loads(config.token_json)
        creds = Credentials.from_authorized_user_info(token_data)

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Update token in DB
            config.token_json = creds.to_json()
            await db.flush()

        service = build("gmail", "v1", credentials=creds)
        return service, config.sender_email
    except Exception as e:
        logger.error(f"Failed to build Gmail service: {e}")
        return None, None


async def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> bool:
    """
    Send an email via Google Gmail API using DB-stored credentials.
    
    The partner configures credentials via POST /api/v1/email/setup.
    """
    if db is None:
        logger.warning(f"Email skipped (no db session): to={to_email}, subject={subject}")
        return False

    service, sender_email = await _get_gmail_service(db)

    if not service:
        logger.warning(f"Email skipped (not configured): to={to_email}, subject={subject}")
        return False

    try:
        # Build email message
        message = MIMEMultipart("alternative")
        message["to"] = to_email
        message["from"] = sender_email
        message["subject"] = subject

        if body_text:
            message.attach(MIMEText(body_text, "plain"))
        message.attach(MIMEText(body_html, "html"))

        # Send via Gmail API
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        logger.info(f"Email sent: to={to_email}, subject={subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False


async def send_notification_email(
    to_email: str,
    title: str,
    message: str,
    db: Optional[AsyncSession] = None,
) -> bool:
    """Send a notification email with standard template."""
    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
        <div style="background: #1a56db; padding: 20px; color: white; text-align: center;">
            <h2>{settings.APP_NAME}</h2>
        </div>
        <div style="padding: 20px; border: 1px solid #e5e7eb;">
            <h3>{title}</h3>
            <p>{message}</p>
            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 20px 0;">
            <p style="color: #6b7280; font-size: 12px;">
                This is an automated notification from {settings.APP_NAME}.
            </p>
        </div>
    </body>
    </html>
    """
    return await send_email(to_email=to_email, subject=title, body_html=html_body, db=db)
