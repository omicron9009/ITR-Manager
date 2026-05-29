"""Service — Email delivery via Google Gmail API (credentials from DB)."""

import asyncio
import base64
import json
import logging
from email.mime.application import MIMEApplication
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
        creds_data = json.loads(config.credentials_json)

        # Extract client_id and client_secret from the OAuth credentials
        # (may be under "installed" or "web" key)
        client_info = creds_data.get("installed") or creds_data.get("web") or {}

        # Ensure token_data has all required fields for from_authorized_user_info
        if "client_id" not in token_data:
            token_data["client_id"] = client_info.get("client_id", "")
        if "client_secret" not in token_data:
            token_data["client_secret"] = client_info.get("client_secret", "")
        if "token_uri" not in token_data:
            token_data["token_uri"] = client_info.get("token_uri", "https://oauth2.googleapis.com/token")

        creds = Credentials.from_authorized_user_info(token_data)

        # Refresh if expired (blocking I/O → run in thread pool)
        if creds.expired and creds.refresh_token:
            await asyncio.to_thread(creds.refresh, Request())
            # Update token in DB
            config.token_json = creds.to_json()
            await db.flush()

        # Build service (minor I/O → thread pool)
        service = await asyncio.to_thread(build, "gmail", "v1", credentials=creds)
        return service, config.sender_email
    except Exception as e:
        logger.error(f"Failed to build Gmail service: {e}")
        return None, None


def _send_gmail_message(service, raw_message: str):
    """Synchronous Gmail API send — to be called via asyncio.to_thread."""
    return service.users().messages().send(
        userId="me", body={"raw": raw_message}
    ).execute()


async def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> bool:
    """
    Send an email via Google Gmail API using DB-stored credentials.
    
    All blocking network I/O is offloaded to a thread pool so the
    async event loop is never blocked.
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

        # Send via Gmail API (blocking HTTP → thread pool)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        await asyncio.to_thread(_send_gmail_message, service, raw)

        logger.info(f"Email sent: to={to_email}, subject={subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}", exc_info=True)
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


async def send_email_with_attachment(
    to_email: str,
    subject: str,
    body_html: str,
    attachment_bytes: bytes,
    attachment_filename: str,
    attachment_content_type: str = "application/pdf",
    body_text: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> bool:
    """Send an email with a file attachment via Gmail API."""
    if db is None:
        logger.warning(f"Email skipped (no db session): to={to_email}, subject={subject}")
        return False

    service, sender_email = await _get_gmail_service(db)

    if not service:
        logger.warning(f"Email skipped (not configured): to={to_email}, subject={subject}")
        return False

    try:
        message = MIMEMultipart("mixed")
        message["to"] = to_email
        message["from"] = sender_email
        message["subject"] = subject

        # Body part
        body_part = MIMEMultipart("alternative")
        if body_text:
            body_part.attach(MIMEText(body_text, "plain"))
        body_part.attach(MIMEText(body_html, "html"))
        message.attach(body_part)

        # Attachment
        maintype, subtype = attachment_content_type.split("/", 1)
        attachment = MIMEApplication(attachment_bytes, _subtype=subtype)
        attachment.add_header("Content-Disposition", "attachment", filename=attachment_filename)
        message.attach(attachment)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        await asyncio.to_thread(_send_gmail_message, service, raw)

        logger.info(f"Email with attachment sent: to={to_email}, subject={subject}, file={attachment_filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email with attachment to {to_email}: {str(e)}", exc_info=True)
        return False
