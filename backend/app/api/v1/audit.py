"""API v1 — Audit log endpoints (Partner only)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_partner
from app.database import get_db
from app.enums import AuditEventType
from app.models.audit_log import AuditLog
from app.models.user import User
from app.schemas.audit import AuditLogFilterRequest, AuditLogListResponse, AuditLogResponse

router = APIRouter()


# ─── GET /audit/logs ────────────────────────────────────────
@router.get("/logs", response_model=AuditLogListResponse)
async def list_audit_logs(
    client_id: Optional[UUID] = Query(None),
    event_type: Optional[AuditEventType] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """List audit logs with filters (Partner only)."""
    query = select(AuditLog)

    if client_id:
        query = query.where(AuditLog.client_id == client_id)
    if event_type:
        query = query.where(AuditLog.event_type == event_type)
    if start_date:
        from datetime import datetime, timezone
        query = query.where(AuditLog.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        from datetime import datetime, timezone
        query = query.where(AuditLog.created_at <= datetime.fromisoformat(end_date))

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    logs = result.scalars().all()

    items = []
    for log in logs:
        actor_name = None
        if log.actor_id:
            actor_result = await db.execute(select(User).where(User.id == log.actor_id))
            actor = actor_result.scalar_one_or_none()
            actor_name = actor.full_name if actor else None

        client_name = None
        if log.client_id:
            client_result = await db.execute(select(User).where(User.id == log.client_id))
            client = client_result.scalar_one_or_none()
            client_name = client.full_name if client else None

        items.append(AuditLogResponse(
            id=log.id,
            event_type=log.event_type,
            actor_id=log.actor_id,
            actor_name=actor_name,
            client_id=log.client_id,
            client_name=client_name,
            filing_id=log.filing_id,
            document_id=log.document_id,
            details=log.details,
            ip_address=str(log.ip_address) if log.ip_address else None,
            created_at=log.created_at,
        ))

    return AuditLogListResponse(items=items, total=total, page=page, page_size=page_size)


# ─── POST /audit/generate-report ────────────────────────────
@router.post("/generate-report", response_model=dict)
async def generate_audit_report(
    client_id: Optional[UUID] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate an HTML audit report (Partner only).
    Returns a download URL or the HTML content.
    """
    # Build query
    query = select(AuditLog)
    if client_id:
        query = query.where(AuditLog.client_id == client_id)
    if start_date:
        from datetime import datetime, timezone
        query = query.where(AuditLog.created_at >= datetime.fromisoformat(start_date))
    if end_date:
        from datetime import datetime, timezone
        query = query.where(AuditLog.created_at <= datetime.fromisoformat(end_date))

    query = query.order_by(AuditLog.created_at.desc())
    result = await db.execute(query)
    logs = result.scalars().all()

    # Generate HTML report
    html_rows = []
    for log in logs:
        actor_name = "System"
        if log.actor_id:
            actor_result = await db.execute(select(User).where(User.id == log.actor_id))
            actor = actor_result.scalar_one_or_none()
            actor_name = actor.full_name if actor else "Unknown"

        html_rows.append(f"""
        <tr>
            <td>{log.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
            <td>{log.event_type.value}</td>
            <td>{actor_name}</td>
            <td>{str(log.client_id) if log.client_id else '-'}</td>
            <td>{str(log.details)}</td>
        </tr>
        """)

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Audit Log Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #1a56db; color: white; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        h1 {{ color: #1a56db; }}
    </style>
    </head>
    <body>
        <h1>Audit Log Report</h1>
        <p>Generated: {__import__('datetime').datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
        <p>Total Records: {len(logs)}</p>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Event Type</th>
                    <th>Actor</th>
                    <th>Client ID</th>
                    <th>Details</th>
                </tr>
            </thead>
            <tbody>
                {''.join(html_rows)}
            </tbody>
        </table>
    </body>
    </html>
    """

    return {
        "message": "Audit report generated",
        "total_records": len(logs),
        "html_content": html_content,
    }
