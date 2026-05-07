"""Service — Audit log recording."""

import json
import re
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditEventType
from app.models.audit_log import AuditLog


def _sanitize_ascii(value: Any) -> Any:
    """Recursively strip non-ASCII characters from strings/dicts/lists.

    PostgreSQL SQL_ASCII encoding cannot store Unicode escape sequences,
    so we replace any non-ASCII character with its closest ASCII equivalent
    or strip it entirely.
    """
    if isinstance(value, str):
        # Replace common Unicode punctuation with ASCII equivalents
        value = value.replace("\u2014", "-")   # em-dash
        value = value.replace("\u2013", "-")   # en-dash
        value = value.replace("\u2018", "'")   # left single quote
        value = value.replace("\u2019", "'")   # right single quote
        value = value.replace("\u201c", '"')   # left double quote
        value = value.replace("\u201d", '"')   # right double quote
        value = value.replace("\u2026", "...")  # ellipsis
        # Strip any remaining non-ASCII characters
        return value.encode("ascii", "ignore").decode("ascii")
    if isinstance(value, dict):
        return {k: _sanitize_ascii(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_ascii(v) for v in value]
    return value


async def record_audit_event(
    db: AsyncSession,
    event_type: AuditEventType,
    actor_id: Optional[UUID] = None,
    client_id: Optional[UUID] = None,
    filing_id: Optional[UUID] = None,
    document_id: Optional[UUID] = None,
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    """Record an audit event. This is called throughout the application."""
    safe_details = _sanitize_ascii(details) if details else {}
    audit_entry = AuditLog(
        event_type=event_type,
        actor_id=actor_id,
        client_id=client_id,
        filing_id=filing_id,
        document_id=document_id,
        details=safe_details,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(audit_entry)
    await db.flush()
    return audit_entry
