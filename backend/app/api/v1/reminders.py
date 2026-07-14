"""API v1 — Reminders configuration and dispatch log (Partner-only)."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_partner
from app.database import get_db
from app.enums import AuditEventType, ReminderType
from app.models.reminder_config import ReminderConfig
from app.models.reminder_dispatch_log import ReminderDispatchLog
from app.models.user import User
from app.schemas.reminder import (
    ReminderChannels,
    ReminderConfigResponse,
    ReminderConfigUpdate,
    ReminderDispatchLogPage,
    ReminderDispatchLogResponse,
    ReminderRunNowResponse,
)
from app.services.audit_service import record_audit_event
from app.services.reminder_service import dispatch_due_reminders

router = APIRouter()


def _to_response(cfg: ReminderConfig) -> ReminderConfigResponse:
    raw_channels = cfg.channels or {}
    channels = ReminderChannels(
        in_app=bool(raw_channels.get("in_app", True)),
        email=bool(raw_channels.get("email", True)),
        whatsapp=bool(raw_channels.get("whatsapp", True)),
    )
    return ReminderConfigResponse(
        id=cfg.id,
        reminder_type=cfg.reminder_type,
        is_enabled=cfg.is_enabled,
        threshold_days=cfg.threshold_days,
        repeat_interval_days=cfg.repeat_interval_days,
        max_sends=cfg.max_sends,
        channels=channels,
        custom_title=cfg.custom_title,
        custom_message=cfg.custom_message,
        updated_by=cfg.updated_by,
        updated_at=cfg.updated_at,
        created_at=cfg.created_at,
    )


async def _get_config_or_404(db: AsyncSession, rt: ReminderType) -> ReminderConfig:
    cfg = (await db.execute(
        select(ReminderConfig).where(ReminderConfig.reminder_type == rt)
    )).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No reminder config found for type '{rt.value}'.",
        )
    return cfg


# ─── GET /configs ───────────────────────────────────────────
@router.get("/configs", response_model=list[ReminderConfigResponse])
async def list_reminder_configs(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReminderConfig).order_by(ReminderConfig.reminder_type)
    )
    return [_to_response(cfg) for cfg in result.scalars().all()]


# ─── GET /configs/{reminder_type} ───────────────────────────
@router.get("/configs/{reminder_type}", response_model=ReminderConfigResponse)
async def get_reminder_config(
    reminder_type: ReminderType,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _get_config_or_404(db, reminder_type)
    return _to_response(cfg)


# ─── PUT /configs/{reminder_type} ───────────────────────────
@router.put("/configs/{reminder_type}", response_model=ReminderConfigResponse)
async def update_reminder_config(
    reminder_type: ReminderType,
    payload: ReminderConfigUpdate,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Partial update of a reminder configuration (only non-None fields applied)."""
    cfg = await _get_config_or_404(db, reminder_type)

    changed: dict = {}
    if payload.is_enabled is not None and payload.is_enabled != cfg.is_enabled:
        cfg.is_enabled = payload.is_enabled
        changed["is_enabled"] = payload.is_enabled
    if payload.threshold_days is not None and payload.threshold_days != cfg.threshold_days:
        cfg.threshold_days = payload.threshold_days
        changed["threshold_days"] = payload.threshold_days
    if payload.repeat_interval_days is not None and payload.repeat_interval_days != cfg.repeat_interval_days:
        cfg.repeat_interval_days = payload.repeat_interval_days
        changed["repeat_interval_days"] = payload.repeat_interval_days
    if payload.max_sends is not None and payload.max_sends != cfg.max_sends:
        cfg.max_sends = payload.max_sends
        changed["max_sends"] = payload.max_sends
    if payload.channels is not None:
        cfg.channels = payload.channels.model_dump()
        changed["channels"] = cfg.channels
    if payload.custom_title is not None and payload.custom_title != cfg.custom_title:
        cfg.custom_title = payload.custom_title
        changed["custom_title"] = payload.custom_title
    if payload.custom_message is not None and payload.custom_message != cfg.custom_message:
        cfg.custom_message = payload.custom_message
        changed["custom_message"] = payload.custom_message

    if not changed:
        # No-op update — return the current row without touching updated_by/at.
        return _to_response(cfg)

    cfg.updated_by = current_user.id
    cfg.updated_at = datetime.now(timezone.utc)
    await db.flush()

    await record_audit_event(
        db,
        event_type=AuditEventType.REMINDER_CONFIG_UPDATED,
        actor_id=current_user.id,
        details={
            "reminder_type": reminder_type.value,
            "changed_fields": list(changed.keys()),
        },
    )
    return _to_response(cfg)


# ─── POST /configs/{reminder_type}/pause ────────────────────
@router.post("/configs/{reminder_type}/pause", response_model=ReminderConfigResponse)
async def pause_reminder_config(
    reminder_type: ReminderType,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _get_config_or_404(db, reminder_type)
    if cfg.is_enabled:
        cfg.is_enabled = False
        cfg.updated_by = current_user.id
        cfg.updated_at = datetime.now(timezone.utc)
        await db.flush()
    await record_audit_event(
        db,
        event_type=AuditEventType.REMINDER_CONFIG_PAUSED,
        actor_id=current_user.id,
        details={"reminder_type": reminder_type.value},
    )
    return _to_response(cfg)


# ─── POST /configs/{reminder_type}/resume ───────────────────
@router.post("/configs/{reminder_type}/resume", response_model=ReminderConfigResponse)
async def resume_reminder_config(
    reminder_type: ReminderType,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    cfg = await _get_config_or_404(db, reminder_type)
    if not cfg.is_enabled:
        cfg.is_enabled = True
        cfg.updated_by = current_user.id
        cfg.updated_at = datetime.now(timezone.utc)
        await db.flush()
    await record_audit_event(
        db,
        event_type=AuditEventType.REMINDER_CONFIG_RESUMED,
        actor_id=current_user.id,
        details={"reminder_type": reminder_type.value},
    )
    return _to_response(cfg)


# ─── GET /dispatch-log ──────────────────────────────────────
@router.get("/dispatch-log", response_model=ReminderDispatchLogPage)
async def list_dispatch_log(
    reminder_type: Optional[ReminderType] = Query(default=None),
    client_id: Optional[UUID] = Query(default=None),
    filing_id: Optional[UUID] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if reminder_type is not None:
        filters.append(ReminderDispatchLog.reminder_type == reminder_type)
    if client_id is not None:
        filters.append(ReminderDispatchLog.related_client_id == client_id)
    if filing_id is not None:
        filters.append(ReminderDispatchLog.related_filing_id == filing_id)

    count_stmt = select(func.count(ReminderDispatchLog.id))
    if filters:
        count_stmt = count_stmt.where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = select(ReminderDispatchLog)
    if filters:
        stmt = stmt.where(*filters)
    stmt = (
        stmt.order_by(ReminderDispatchLog.sent_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(stmt)).scalars().all())

    return ReminderDispatchLogPage(
        items=[ReminderDispatchLogResponse.model_validate(row) for row in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── POST /run-now ──────────────────────────────────────────
@router.post("/run-now", response_model=ReminderRunNowResponse)
async def run_reminders_now(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Force a single worker tick synchronously. Same rules as the scheduled worker."""
    started_at = datetime.now(timezone.utc)
    dispatched, skipped = await dispatch_due_reminders(db)
    finished_at = datetime.now(timezone.utc)
    return ReminderRunNowResponse(
        started_at=started_at,
        finished_at=finished_at,
        dispatched=dispatched,
        skipped=skipped,
    )
