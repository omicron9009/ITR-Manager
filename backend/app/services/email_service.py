"""Service — Email delivery via SMTP (credentials from DB)."""

import html
import logging
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import aiosmtplib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)


async def _get_smtp_config(db: AsyncSession):
    """Retrieve SMTP configuration from DB."""
    from app.models.email_config import EmailConfig

    result = await db.execute(
        select(EmailConfig).order_by(EmailConfig.created_at.desc()).limit(1)
    )
    config = result.scalar_one_or_none()

    if not config:
        logger.warning("Email not configured — no SMTP config in database")
        return None

    return config


async def _send_via_smtp(config, message: MIMEMultipart) -> None:
    """Send a MIME message using async SMTP."""
    await aiosmtplib.send(
        message,
        hostname=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_user,
        password=config.smtp_password,
        start_tls=config.use_tls,
    )


async def send_email(
    to_email: str,
    subject: str,
    body_html: str,
    body_text: Optional[str] = None,
    db: Optional[AsyncSession] = None,
) -> bool:
    """
    Send an email via SMTP using DB-stored credentials.
    """
    if db is None:
        logger.warning(f"Email skipped (no db session): to={to_email}, subject={subject}")
        return False

    config = await _get_smtp_config(db)

    if not config:
        logger.warning(f"Email skipped (not configured): to={to_email}, subject={subject}")
        return False

    try:
        message = MIMEMultipart("alternative")
        message["To"] = to_email
        message["From"] = config.sender_email
        message["Subject"] = subject

        if body_text:
            message.attach(MIMEText(body_text, "plain"))
        message.attach(MIMEText(body_html, "html"))

        await _send_via_smtp(config, message)

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
    # Rich context fields (all optional — backwards compatible)
    client_name: Optional[str] = None,
    financial_year: Optional[str] = None,
    filing_status: Optional[str] = None,
    action_by: Optional[str] = None,
    action_url_path: Optional[str] = None,
    cta_label: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> bool:
    """Send a notification email with professional branded template."""
    html_body = _build_professional_email(
        title=title,
        message=message,
        client_name=client_name,
        financial_year=financial_year,
        filing_status=filing_status,
        action_by=action_by,
        action_url_path=action_url_path,
        cta_label=cta_label,
        extra_details=extra_details,
    )
    return await send_email(to_email=to_email, subject=f"{settings.FIRM_NAME} — {title}", body_html=html_body, db=db)


def _build_professional_email(
    title: str,
    message: str,
    client_name: Optional[str] = None,
    financial_year: Optional[str] = None,
    filing_status: Optional[str] = None,
    action_by: Optional[str] = None,
    action_url_path: Optional[str] = None,
    cta_label: Optional[str] = None,
    extra_details: Optional[dict] = None,
) -> str:
    """Build a professional HTML email with firm branding and context."""
    frontend_url = settings.FRONTEND_URL.rstrip("/")
    firm_name = html.escape(settings.FIRM_NAME)
    firm_website = settings.FIRM_WEBSITE
    firm_phone = settings.FIRM_PHONE

    # HTML-escape user-provided values to prevent XSS
    title = html.escape(title)
    message = html.escape(message)

    # Build details rows
    details_html = ""
    detail_rows = []
    if client_name:
        detail_rows.append(("Client", html.escape(client_name)))
    if financial_year:
        detail_rows.append(("Financial Year", html.escape(financial_year)))
    if filing_status:
        detail_rows.append(("Filing Status", html.escape(filing_status)))
    if action_by:
        detail_rows.append(("Action By", html.escape(action_by)))
    if extra_details:
        for key, value in extra_details.items():
            detail_rows.append((html.escape(key), html.escape(str(value))))

    if detail_rows:
        rows_html = "".join(
            f'<tr><td style="padding:6px 12px;font-weight:600;color:#374151;border-bottom:1px solid #f3f4f6;">{k}</td>'
            f'<td style="padding:6px 12px;color:#1f2937;border-bottom:1px solid #f3f4f6;">{v}</td></tr>'
            for k, v in detail_rows
        )
        details_html = f"""
        <table style="width:100%;border-collapse:collapse;margin:16px 0;background:#f9fafb;border-radius:6px;overflow:hidden;">
            {rows_html}
        </table>
        """

    # CTA button
    cta_html = ""
    if cta_label:
        full_url = frontend_url
        cta_html = f"""
        <div style="text-align:center;margin:24px 0;">
            <a href="{full_url}" style="display:inline-block;background:#1a56db;color:#ffffff;
               padding:12px 28px;border-radius:6px;text-decoration:none;font-weight:600;font-size:14px;">
                {html.escape(cta_label)} &rarr;
            </a>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:'Segoe UI',Arial,sans-serif;">
<div style="max-width:600px;margin:0 auto;background:#ffffff;">
    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1a56db 0%,#1e40af 100%);padding:28px 24px;text-align:center;">
        <h1 style="margin:0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:0.5px;">{firm_name}</h1>
        <p style="margin:4px 0 0;color:#bfdbfe;font-size:12px;">{settings.APP_NAME}</p>
    </div>

    <!-- Body -->
    <div style="padding:28px 24px;">
        <h2 style="margin:0 0 12px;color:#111827;font-size:18px;font-weight:600;">{title}</h2>
        <p style="margin:0 0 16px;color:#374151;font-size:14px;line-height:1.6;">{message}</p>

        {details_html}
        {cta_html}
    </div>

    <!-- Footer -->
    <div style="background:#f9fafb;padding:20px 24px;border-top:1px solid #e5e7eb;">
        <p style="margin:0 0 4px;color:#374151;font-size:13px;font-weight:600;">{firm_name}</p>
        <p style="margin:0 0 4px;color:#6b7280;font-size:12px;">
            {"<a href='https://" + firm_website + "' style='color:#1a56db;text-decoration:none;'>" + firm_website + "</a> &nbsp;|&nbsp; " if firm_website else ""}
            {firm_phone if firm_phone else ""}
        </p>
        <p style="margin:12px 0 0;color:#9ca3af;font-size:11px;">
            This is an automated notification. Please do not reply to this email.<br>
            To manage your notifications, log in at
            <a href="{frontend_url}" style="color:#1a56db;text-decoration:none;">{frontend_url.replace('https://', '')}</a>
        </p>
    </div>
</div>
</body>
</html>"""


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
    """Send an email with a file attachment via SMTP."""
    if db is None:
        logger.warning(f"Email skipped (no db session): to={to_email}, subject={subject}")
        return False

    config = await _get_smtp_config(db)

    if not config:
        logger.warning(f"Email skipped (not configured): to={to_email}, subject={subject}")
        return False

    try:
        message = MIMEMultipart("mixed")
        message["To"] = to_email
        message["From"] = config.sender_email
        message["Subject"] = subject

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

        await _send_via_smtp(config, message)

        logger.info(f"Email with attachment sent: to={to_email}, subject={subject}, file={attachment_filename}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email with attachment to {to_email}: {str(e)}", exc_info=True)
        return False
