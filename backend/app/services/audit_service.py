"""Service — Audit log recording."""

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditEventType
from app.models.audit_log import AuditLog


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
    audit_entry = AuditLog(
        event_type=event_type,
        actor_id=actor_id,
        client_id=client_id,
        filing_id=filing_id,
        document_id=document_id,
        details=details or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(audit_entry)
    await db.flush()
    return audit_entry
