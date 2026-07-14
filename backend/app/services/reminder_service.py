"""Service — Reminders subsystem: shared dispatcher, dedup, and per-type evaluators.

The reminder pipeline has three concerns:

1. **Evaluators** (`_eval_*`) inspect the DB for one specific `ReminderType` and
   produce a list of `ReminderCandidate` objects (one per recipient-per-subject).
2. **Dispatcher** (`dispatch_candidate`) applies dedup / rate-limit / channel
   gating and calls `notification_service.create_notification`, which handles
   in-app + email + WhatsApp fan-out.
3. **Orchestrator** (`dispatch_due_reminders`) iterates enabled configs and
   routes each type to its evaluator via `_ROUTES`.

The background worker in `app/main.py::_reminders_worker_loop` invokes
`dispatch_due_reminders` on a fixed cadence. The Partner-only
`POST /api/v1/reminders/run-now` endpoint calls the same entry point.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import (
    AccountStatus,
    AuditEventType,
    NotificationChannel,
    REMINDER_DEFAULT_LABELS,
    REMINDER_DEFAULT_MESSAGES,
    ReminderType,
    UserRole,
)
from app.models.reminder_config import ReminderConfig
from app.models.reminder_dispatch_log import ReminderDispatchLog
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.notification_service import create_notification

logger = logging.getLogger(__name__)


# ─── Data class ─────────────────────────────────────────────
@dataclass
class ReminderCandidate:
    """One dispatchable reminder — recipient + related entities + template context."""

    reminder_type: ReminderType
    subject_user_id: UUID
    related_client_id: Optional[UUID] = None
    related_filing_id: Optional[UUID] = None
    context: dict = field(default_factory=dict)


# ─── Time / FY helpers ──────────────────────────────────────
def compute_current_indian_fy(now: Optional[datetime] = None) -> str:
    """Return India FY as `"YYYY-YYYY"` (Apr 1 → Mar 31) in `REMINDERS_TIMEZONE`."""
    tz = ZoneInfo(settings.REMINDERS_TIMEZONE)
    ref = (now or datetime.now(timezone.utc)).astimezone(tz)
    if ref.month >= 4:
        start = ref.year
    else:
        start = ref.year - 1
    return f"{start}-{start + 1}"


def _days_since(ts: Optional[datetime]) -> int:
    """Floor-days between now (UTC) and `ts`. Treats naive datetimes as UTC."""
    if ts is None:
        return 0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(0, delta.days)


# ─── Config helpers ─────────────────────────────────────────
async def get_config(db: AsyncSession, rt: ReminderType) -> Optional[ReminderConfig]:
    """Single-row config lookup."""
    result = await db.execute(
        select(ReminderConfig).where(ReminderConfig.reminder_type == rt)
    )
    return result.scalar_one_or_none()


async def get_all_configs(db: AsyncSession) -> list[ReminderConfig]:
    result = await db.execute(
        select(ReminderConfig).order_by(ReminderConfig.reminder_type)
    )
    return list(result.scalars().all())


# ─── Channel / template / dedup helpers ─────────────────────
def _channel_from_flags(in_app: bool, email: bool) -> Optional[NotificationChannel]:
    """Map the two boolean flags to `NotificationChannel`. None → skip send."""
    if in_app and email:
        return NotificationChannel.BOTH
    if email:
        return NotificationChannel.EMAIL
    if in_app:
        return NotificationChannel.IN_APP
    return None


def _dedup_key(rt: ReminderType, filing_id: Optional[UUID], user_id: UUID) -> str:
    return f"{rt.value}:{filing_id if filing_id is not None else 'no_filing'}:{user_id}"


def _render(template: str, ctx: dict) -> str:
    """Safe `str.format_map` — missing placeholders resolve to empty string."""
    if not template:
        return ""
    return template.format_map(defaultdict(str, ctx))


async def _should_send(
    db: AsyncSession, candidate: ReminderCandidate, cfg: ReminderConfig
) -> tuple[bool, str]:
    """Enforce `max_sends` + `repeat_interval_days` against `reminder_dispatch_logs`."""
    key = _dedup_key(cfg.reminder_type, candidate.related_filing_id, candidate.subject_user_id)

    if cfg.max_sends and cfg.max_sends > 0:
        count = (await db.execute(
            select(func.count(ReminderDispatchLog.id))
            .where(ReminderDispatchLog.dedup_key == key)
        )).scalar() or 0
        if count >= cfg.max_sends:
            return (False, "max_sends_reached")

    if cfg.repeat_interval_days and cfg.repeat_interval_days > 0:
        last_sent = (await db.execute(
            select(func.max(ReminderDispatchLog.sent_at))
            .where(ReminderDispatchLog.dedup_key == key)
        )).scalar()
        if last_sent is not None:
            if last_sent.tzinfo is None:
                last_sent = last_sent.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last_sent < timedelta(days=cfg.repeat_interval_days):
                return (False, "within_repeat_interval")

    return (True, "ok")


# ─── Recipient resolvers ────────────────────────────────────
async def _elevated_manager_ids(db: AsyncSession) -> list[UUID]:
    result = await db.execute(
        select(User.id).where(
            User.role == UserRole.MANAGER,
            User.is_elevated == True,  # noqa: E712
            User.is_active == True,  # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


async def _partner_ids(db: AsyncSession) -> list[UUID]:
    result = await db.execute(
        select(User.id).where(
            User.role == UserRole.PARTNER,
            User.is_active == True,  # noqa: E712
        )
    )
    return [row[0] for row in result.all()]


async def _exec_and_manager_for_client(
    db: AsyncSession, client_id: UUID
) -> tuple[Optional[UUID], Optional[UUID]]:
    """Resolve (executive_id, manager_id) for a client via active assignments."""
    from app.models.executive_assignment import ExecutiveClientAssignment
    from app.models.manager_executive_assignment import ManagerExecutiveAssignment

    exec_id = (await db.execute(
        select(ExecutiveClientAssignment.executive_id).where(
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,  # noqa: E712
        ).limit(1)
    )).scalar()

    if exec_id is None:
        return (None, None)

    mgr_id = (await db.execute(
        select(ManagerExecutiveAssignment.manager_id).where(
            ManagerExecutiveAssignment.executive_id == exec_id,
            ManagerExecutiveAssignment.is_active == True,  # noqa: E712
        ).limit(1)
    )).scalar()

    return (exec_id, mgr_id)


# ─── Dispatcher ─────────────────────────────────────────────
async def dispatch_candidate(
    db: AsyncSession, cfg: ReminderConfig, candidate: ReminderCandidate
) -> bool:
    """Send one reminder to one recipient. Returns True on success, False on skip."""
    if not cfg.is_enabled:
        return False

    ok, _reason = await _should_send(db, candidate, cfg)
    if not ok:
        return False

    channels = cfg.channels or {}
    channel = _channel_from_flags(
        bool(channels.get("in_app", True)),
        bool(channels.get("email", True)),
    )
    if channel is None:
        # WhatsApp-only is not supported for staff roles; for clients WhatsApp
        # rides on top of an in-app/email notification. If both flags are off
        # there is nothing to send.
        return False

    title = cfg.custom_title or REMINDER_DEFAULT_LABELS.get(
        cfg.reminder_type, cfg.reminder_type.value
    )
    template = cfg.custom_message or REMINDER_DEFAULT_MESSAGES.get(cfg.reminder_type, "")
    message = _render(template, candidate.context)

    ctx = candidate.context or {}
    notification = await create_notification(
        db=db,
        user_id=candidate.subject_user_id,
        title=title,
        message=message,
        channel=channel,
        related_filing_id=candidate.related_filing_id,
        related_client_id=candidate.related_client_id,
        client_name=ctx.get("client_name"),
        financial_year=ctx.get("fy"),
        filing_status=ctx.get("filing_status"),
        cta_label=ctx.get("cta_label"),
        action_url_path=ctx.get("action_url_path"),
    )

    log_row = ReminderDispatchLog(
        reminder_type=cfg.reminder_type,
        subject_user_id=candidate.subject_user_id,
        related_client_id=candidate.related_client_id,
        related_filing_id=candidate.related_filing_id,
        dedup_key=_dedup_key(cfg.reminder_type, candidate.related_filing_id, candidate.subject_user_id),
        notification_id=notification.id,
    )
    db.add(log_row)
    await db.flush()

    await record_audit_event(
        db,
        event_type=AuditEventType.REMINDER_SENT,
        actor_id=None,
        client_id=candidate.related_client_id,
        filing_id=candidate.related_filing_id,
        details={
            "reminder_type": cfg.reminder_type.value,
            "recipient": str(candidate.subject_user_id),
        },
    )
    return True


# ─── Evaluators ─────────────────────────────────────────────
async def _eval_unassigned_client(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 1 — activated clients missing exec / manager / partner-tag.

    Fires when a client is ACTIVE, `activated_at < now - threshold_days`, and
    ANY of these three deficiencies hold:
      a. no active `ExecutiveClientAssignment`
      b. exec has no active `ManagerExecutiveAssignment`
      c. `client_profiles.partner_tag_id IS NULL`

    Recipients: all elevated Managers.
    """
    from app.models.client_profile import ClientProfile
    from app.models.executive_assignment import ExecutiveClientAssignment
    from app.models.manager_executive_assignment import ManagerExecutiveAssignment

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # ── Candidate clients ─────────────────────────────────
    rows = (await db.execute(
        select(User, ClientProfile)
        .outerjoin(ClientProfile, ClientProfile.user_id == User.id)
        .where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.ACTIVE,
            User.is_active == True,  # noqa: E712
            User.activated_at.isnot(None),
            User.activated_at < cutoff,
        )
    )).all()
    if not rows:
        return (0, 0)

    client_ids = [u.id for (u, _p) in rows]

    # ── Active exec assignments: client_id → executive_id ─
    exec_map: dict[UUID, UUID] = {}
    exec_rows = (await db.execute(
        select(ExecutiveClientAssignment.client_id, ExecutiveClientAssignment.executive_id)
        .where(
            ExecutiveClientAssignment.client_id.in_(client_ids),
            ExecutiveClientAssignment.is_active == True,  # noqa: E712
        )
    )).all()
    for cid, eid in exec_rows:
        exec_map[cid] = eid

    # ── Active manager assignments: executive_id → manager_id ─
    mgr_map: dict[UUID, UUID] = {}
    exec_ids = list({eid for eid in exec_map.values()})
    if exec_ids:
        mgr_rows = (await db.execute(
            select(ManagerExecutiveAssignment.executive_id, ManagerExecutiveAssignment.manager_id)
            .where(
                ManagerExecutiveAssignment.executive_id.in_(exec_ids),
                ManagerExecutiveAssignment.is_active == True,  # noqa: E712
            )
        )).all()
        for eid, mid in mgr_rows:
            mgr_map[eid] = mid

    # ── Recipients ────────────────────────────────────────
    recipients = await _elevated_manager_ids(db)
    if not recipients:
        # No one to notify; count each client as a single skip so the metric
        # is visible in logs / audit.
        return (0, len(rows))

    dispatched = 0
    skipped = 0
    for user, profile in rows:
        missing: list[str] = []
        exec_id = exec_map.get(user.id)
        if exec_id is None:
            missing.append("executive")
        elif mgr_map.get(exec_id) is None:
            missing.append("manager")
        if profile is None or profile.partner_tag_id is None:
            missing.append("partner_tag")

        if not missing:
            continue  # fully assigned — no deficiency

        ctx = {
            "client_name": user.full_name,
            "days": _days_since(user.activated_at),
            "missing": ", ".join(missing),
        }
        for mgr_user_id in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=mgr_user_id,
                related_client_id=user.id,
                related_filing_id=None,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_filing_not_initiated(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 2 — ACTIVE clients activated > threshold_days with no filing for current FY.

    Recipient: the client themselves (in-app + email + WhatsApp — WhatsApp
    naturally gated inside `create_notification` to CLIENT + opt-in).
    """
    from app.models.filing import ITRFiling

    fy = compute_current_indian_fy()
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # ── Candidate clients ─────────────────────────────────
    clients = list((await db.execute(
        select(User).where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.ACTIVE,
            User.is_active == True,  # noqa: E712
            User.activated_at.isnot(None),
            User.activated_at < cutoff,
        )
    )).scalars().all())
    if not clients:
        return (0, 0)

    client_ids = [c.id for c in clients]

    # ── Exclude clients who already have a filing for the current FY ─
    fy_rows = (await db.execute(
        select(ITRFiling.client_id).where(
            ITRFiling.client_id.in_(client_ids),
            ITRFiling.financial_year == fy,
        )
    )).all()
    excluded = {row[0] for row in fy_rows}

    dispatched = 0
    skipped = 0
    for user in clients:
        if user.id in excluded:
            continue
        ctx = {
            "client_name": user.full_name,
            "fy": fy,
            "days": _days_since(user.activated_at),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=user.id,
            related_client_id=user.id,
            related_filing_id=None,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


async def _eval_tax_payment_pending(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 3 — computation approved > threshold_days ago but tax not marked paid.

    Anchor: `itr_filings.computation_approved_at`. Trigger requires:
      - `is_tax_paid = false`
      - `computation_approved_at` older than `threshold_days`
      - filing status not in {COMPLETED, HALTED}
      - at least one `FilingComputation` in {PARTNER_APPROVED, CLIENT_APPROVED}
        (safety cross-check — the timestamp alone is set only on Partner approval,
        but we join to guard against stale rows).

    Recipient: the client (in-app + email + WhatsApp).
    """
    from app.enums import ComputationStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(
            ITRFiling.is_tax_paid == False,  # noqa: E712
            ITRFiling.computation_approved_at.isnot(None),
            ITRFiling.computation_approved_at < cutoff,
            ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
        )
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Safety cross-check: filings with at least one PARTNER/CLIENT approved computation
    approved_rows = (await db.execute(
        select(FilingComputation.filing_id).distinct().where(
            FilingComputation.filing_id.in_(filing_ids),
            FilingComputation.status.in_([
                ComputationStatus.PARTNER_APPROVED,
                ComputationStatus.CLIENT_APPROVED,
            ]),
        )
    )).all()
    approved_ids = {row[0] for row in approved_rows}

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id not in approved_ids:
            continue
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(f.computation_approved_at),
            "filing_status": f.status.value,
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=f.client_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


async def _eval_filing_stagnant_pre_filing(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 4 — filing pre-FILING, all docs approved, no transition for threshold_days.

    Trigger: filing status in {DOCUMENT_UPLOAD, PROCESSING, COMPUTATION}, has ≥ 1
    `FilingDocument` AND ALL are `APPROVED`, and the latest
    `FilingStateHistory.changed_at` is older than `threshold_days` (falls back to
    `filing.initiated_at` if no history exists yet).

    Recipients: assigned Executive + Manager (via the client→exec→manager chain).
    Skipped entirely if neither is assigned.
    """
    from app.enums import DocumentStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_document import FilingDocument
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.status.in_([
            FilingStatus.DOCUMENT_UPLOAD,
            FilingStatus.PROCESSING,
            FilingStatus.COMPUTATION,
        ]))
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Latest state-history changed_at per filing (single grouped query)
    last_change_rows = (await db.execute(
        select(
            FilingStateHistory.filing_id,
            func.max(FilingStateHistory.changed_at),
        )
        .where(FilingStateHistory.filing_id.in_(filing_ids))
        .group_by(FilingStateHistory.filing_id)
    )).all()
    last_change: dict[UUID, datetime] = {fid: ts for fid, ts in last_change_rows}

    # Doc counts per filing: total + approved (single grouped query using CASE)
    doc_rows = (await db.execute(
        select(
            FilingDocument.filing_id,
            func.count(FilingDocument.id).label("total"),
            func.sum(
                case((FilingDocument.status == DocumentStatus.APPROVED, 1), else_=0)
            ).label("approved"),
        )
        .where(FilingDocument.filing_id.in_(filing_ids))
        .group_by(FilingDocument.filing_id)
    )).all()
    doc_stats: dict[UUID, tuple[int, int]] = {
        fid: (int(total or 0), int(approved or 0))
        for fid, total, approved in doc_rows
    }

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = last_change.get(f.id) or f.initiated_at
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor >= cutoff:
            continue

        total, approved = doc_stats.get(f.id, (0, 0))
        if total == 0 or approved != total:
            continue

        exec_id, mgr_id = await _exec_and_manager_for_client(db, f.client_id)
        recipients = [uid for uid in (exec_id, mgr_id) if uid is not None]
        if not recipients:
            continue

        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "filing_status": f.status.value,
            "days": _days_since(anchor),
        }
        for uid in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=uid,
                related_client_id=f.client_id,
                related_filing_id=f.id,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_invoice_pending_post_filing(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 5 — filing in FILING for > threshold days, INVOICE not PARTNER_APPROVED.

    Anchor: latest `FilingStateHistory.changed_at` where `to_status=FILING`.
    Skip if such an anchor doesn't exist (filing entered FILING before the
    state-history table was populated — extremely rare on modern data, but the
    guard keeps the evaluator idempotent).

    Recipients: all elevated Managers (in-app + email).
    """
    from app.enums import CompletedDocStatus, CompletedDocType, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_completed_doc import FilingCompletedDoc
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.status == FilingStatus.FILING)
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Timestamp of entry into FILING per filing (single grouped query)
    entry_rows = (await db.execute(
        select(
            FilingStateHistory.filing_id,
            func.max(FilingStateHistory.changed_at),
        )
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.FILING,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at: dict[UUID, datetime] = {fid: ts for fid, ts in entry_rows}

    # Filings that already have a Partner-approved INVOICE
    approved_invoice_rows = (await db.execute(
        select(FilingCompletedDoc.filing_id).distinct().where(
            FilingCompletedDoc.filing_id.in_(filing_ids),
            FilingCompletedDoc.doc_type == CompletedDocType.INVOICE,
            FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
        )
    )).all()
    have_invoice = {row[0] for row in approved_invoice_rows}

    recipients = await _elevated_manager_ids(db)
    if not recipients:
        return (0, 0)

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id in have_invoice:
            continue
        anchor = entered_at.get(f.id)
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor >= cutoff:
            continue

        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        for uid in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=uid,
                related_client_id=f.client_id,
                related_filing_id=f.id,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_client_docs_pending_upload(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 6 — DOCUMENT_UPLOAD state for > threshold days with pending/rejected docs.

    Anchor: latest `FilingStateHistory.changed_at` where `to_status=DOCUMENT_UPLOAD`
    (falls back to `filing.initiated_at` for legacy filings that pre-date the
    state-history table).

    Trigger: at least one `FilingDocument` for the filing has
    `status IN (PENDING_UPLOAD, REJECTED)`.

    Recipient: the client (in-app + email + WhatsApp — WhatsApp naturally
    gated to CLIENT + opt-in inside `create_notification`).
    """
    from app.enums import DocumentStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_document import FilingDocument
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.status == FilingStatus.DOCUMENT_UPLOAD)
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Latest entry-into-DOCUMENT_UPLOAD per filing (single grouped query)
    entry_rows = (await db.execute(
        select(
            FilingStateHistory.filing_id,
            func.max(FilingStateHistory.changed_at),
        )
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.DOCUMENT_UPLOAD,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at: dict[UUID, datetime] = {fid: ts for fid, ts in entry_rows}

    # Filings with ≥ 1 pending-or-rejected doc (single DISTINCT query)
    pending_rows = (await db.execute(
        select(FilingDocument.filing_id).distinct().where(
            FilingDocument.filing_id.in_(filing_ids),
            FilingDocument.status.in_([
                DocumentStatus.PENDING_UPLOAD,
                DocumentStatus.REJECTED,
            ]),
        )
    )).all()
    has_pending = {row[0] for row in pending_rows}

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id not in has_pending:
            continue
        anchor = entered_at.get(f.id) or f.initiated_at
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor >= cutoff:
            continue

        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=f.client_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


async def _eval_text_fields_pending_fill(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 7 — non-terminal filing with an oldest PENDING FilingTextField > threshold days.

    Anchor: `MIN(FilingTextField.created_at)` per filing where `status=PENDING`.
    Trigger: that oldest timestamp is older than the threshold AND the filing
    is not in a terminal state (COMPLETED / HALTED).

    Recipient: the client (in-app + email + WhatsApp).
    """
    from app.enums import FilingStatus, TextFieldStatus
    from app.models.filing import ITRFiling
    from app.models.filing_text_field import FilingTextField

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Oldest PENDING text field per filing (single grouped query)
    pending_rows = (await db.execute(
        select(
            FilingTextField.filing_id,
            func.min(FilingTextField.created_at),
        )
        .where(FilingTextField.status == TextFieldStatus.PENDING)
        .group_by(FilingTextField.filing_id)
    )).all()
    # Normalise + gate by cutoff
    oldest: dict[UUID, datetime] = {}
    for fid, ts in pending_rows:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            oldest[fid] = ts
    if not oldest:
        return (0, 0)

    filings = list((await db.execute(
        select(ITRFiling).where(
            ITRFiling.id.in_(list(oldest.keys())),
            ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
        )
    )).scalars().all())
    if not filings:
        return (0, 0)

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = oldest[f.id]
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=f.client_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


async def _eval_computation_awaiting_manager_approval(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 8 — FilingComputation.status=UPLOADED, uploaded_at older than threshold.

    Anchor: `MIN(FilingComputation.uploaded_at)` per filing where `status=UPLOADED`
    and already older than the cutoff (`WHERE` push-down keeps it cheap).

    Recipient: the Manager assigned via the client's active Exec→Manager chain.
    If the client has no Manager (or no Exec), the filing is silently skipped
    — those cases belong to Reminder 1 (`UNASSIGNED_CLIENT`).

    Channels: in-app + email.
    """
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Filings with at least one UPLOADED computation older than the cutoff
    rows = (await db.execute(
        select(
            FilingComputation.filing_id,
            func.min(FilingComputation.uploaded_at),
        )
        .where(
            FilingComputation.status == ComputationStatus.UPLOADED,
            FilingComputation.uploaded_at < cutoff,
        )
        .group_by(FilingComputation.filing_id)
    )).all()
    if not rows:
        return (0, 0)

    filing_map: dict[UUID, datetime] = {fid: ts for fid, ts in rows}
    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(list(filing_map.keys())))
    )).scalars().all())
    if not filings:
        return (0, 0)

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        _exec_id, mgr_id = await _exec_and_manager_for_client(db, f.client_id)
        if mgr_id is None:
            continue
        anchor = filing_map[f.id]
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=mgr_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


async def _eval_computation_awaiting_partner_approval(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 9 — FilingComputation.status=MANAGER_APPROVED older than threshold.

    Anchor: `MIN(manager_approved_at)` per filing across all MANAGER_APPROVED
    computations (guards against multiple approved versions — the earliest wait
    time is the honest one to report).

    Recipients: all active Partners. Every Partner gets their own dispatch row
    so `max_sends` bookkeeping is scoped per partner (correct behaviour when a
    partner joins mid-cycle).

    Channels: in-app + email.
    """
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    rows = (await db.execute(
        select(
            FilingComputation.filing_id,
            func.min(FilingComputation.manager_approved_at),
        )
        .where(
            FilingComputation.status == ComputationStatus.MANAGER_APPROVED,
            FilingComputation.manager_approved_at.isnot(None),
            FilingComputation.manager_approved_at < cutoff,
        )
        .group_by(FilingComputation.filing_id)
    )).all()
    if not rows:
        return (0, 0)

    filing_map: dict[UUID, datetime] = {fid: ts for fid, ts in rows}
    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(list(filing_map.keys())))
    )).scalars().all())
    if not filings:
        return (0, 0)

    partner_ids = await _partner_ids(db)
    if not partner_ids:
        return (0, 0)

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = filing_map[f.id]
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        for uid in partner_ids:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=uid,
                related_client_id=f.client_id,
                related_filing_id=f.id,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_computation_awaiting_client_approval(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 10 — latest computation is PARTNER_APPROVED, partner_approved_at > threshold days.

    Two-step logic to correctly identify "latest computation per filing":
      1. Fetch all PARTNER_APPROVED computations older than the cutoff (candidates).
      2. For each candidate filing, load the newest computation row (by
         `uploaded_at desc`) and keep only filings whose newest is still
         PARTNER_APPROVED. This filters out filings where the client has since
         approved/rejected, or where a new version was uploaded and superseded
         the partner-approved one.

    Anchor: the `partner_approved_at` of the most recent PARTNER_APPROVED
    computation for that filing (in case a filing has multiple).

    Recipient: the client (in-app + email + WhatsApp).
    """
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Step 1 — candidate PARTNER_APPROVED computations older than cutoff
    pa_rows = list((await db.execute(
        select(FilingComputation).where(
            FilingComputation.status == ComputationStatus.PARTNER_APPROVED,
            FilingComputation.partner_approved_at.isnot(None),
            FilingComputation.partner_approved_at < cutoff,
        )
    )).scalars().all())
    if not pa_rows:
        return (0, 0)

    candidate_filing_ids = list({c.filing_id for c in pa_rows})

    # Step 2 — newest computation per filing (by uploaded_at desc)
    latest_rows = (await db.execute(
        select(
            FilingComputation.filing_id,
            FilingComputation.status,
            FilingComputation.uploaded_at,
        )
        .where(FilingComputation.filing_id.in_(candidate_filing_ids))
        .order_by(
            FilingComputation.filing_id,
            FilingComputation.uploaded_at.desc(),
        )
    )).all()
    latest_status_by_filing: dict[UUID, ComputationStatus] = {}
    for fid, status, _uploaded in latest_rows:
        if fid not in latest_status_by_filing:
            latest_status_by_filing[fid] = status

    # Keep only filings whose latest status is still PARTNER_APPROVED
    target_filing_ids = [
        fid for fid, status in latest_status_by_filing.items()
        if status == ComputationStatus.PARTNER_APPROVED
    ]
    if not target_filing_ids:
        return (0, 0)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(target_filing_ids))
    )).scalars().all())
    if not filings:
        return (0, 0)

    # Anchor: most recent partner_approved_at among the candidate rows per filing
    target_set = set(target_filing_ids)
    anchor_by_filing: dict[UUID, datetime] = {}
    for c in pa_rows:
        if c.filing_id not in target_set:
            continue
        current = anchor_by_filing.get(c.filing_id)
        if current is None or c.partner_approved_at > current:
            anchor_by_filing[c.filing_id] = c.partner_approved_at

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = anchor_by_filing.get(f.id)
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=f.client_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


# Mirrors `_REQUIRED_COMPLETED_DOCS` in action_item_service (kept as strings so
# set comparison with `doc_type.value` is unambiguous).
_REQUIRED_COMPLETED_DOCS_FOR_REMINDER: set[str] = {
    "ITR_ACKNOWLEDGEMENT",
    "INVOICE",
    "ITR_JSON",
    "ITR_FORM",
    "TAX_PAID_COMPUTATION",
}


async def _eval_completed_docs_pending(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 11 — filing in FILING > threshold days with any required completed
    doc missing a PARTNER_APPROVED row.

    Anchor: latest `FilingStateHistory.changed_at` where `to_status=FILING`
    (falls back to skip if unavailable — same guard used by Prompt 5).

    Recipients: assigned Executive + Manager. Skipped if neither is assigned
    (that case belongs to Reminder 1 UNASSIGNED_CLIENT).

    Channels: in-app + email.
    """
    from app.enums import CompletedDocStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_completed_doc import FilingCompletedDoc
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.status == FilingStatus.FILING)
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Timestamp of entry into FILING per filing (single grouped query)
    entry_rows = (await db.execute(
        select(
            FilingStateHistory.filing_id,
            func.max(FilingStateHistory.changed_at),
        )
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.FILING,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at: dict[UUID, datetime] = {fid: ts for fid, ts in entry_rows}

    # Per-filing set of PARTNER_APPROVED completed doc types (single query)
    approved_rows = (await db.execute(
        select(FilingCompletedDoc.filing_id, FilingCompletedDoc.doc_type)
        .where(
            FilingCompletedDoc.filing_id.in_(filing_ids),
            FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
        )
    )).all()
    approved_by_filing: dict[UUID, set[str]] = {}
    for fid, dt in approved_rows:
        approved_by_filing.setdefault(fid, set()).add(
            dt.value if hasattr(dt, "value") else str(dt)
        )

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = entered_at.get(f.id)
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor >= cutoff:
            continue

        approved = approved_by_filing.get(f.id, set())
        missing = _REQUIRED_COMPLETED_DOCS_FOR_REMINDER - approved
        if not missing:
            continue

        exec_id, mgr_id = await _exec_and_manager_for_client(db, f.client_id)
        recipients = [uid for uid in (exec_id, mgr_id) if uid is not None]
        if not recipients:
            continue

        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
            "missing_docs": ", ".join(sorted(missing)),
        }
        for uid in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=uid,
                related_client_id=f.client_id,
                related_filing_id=f.id,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_payment_not_marked_received(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 12 — filing in PAYMENT > threshold days, payment_received_at IS NULL.

    Anchor: latest `FilingStateHistory.changed_at` where `to_status=PAYMENT`.
    Skip if no such row (defensive — shouldn't happen for filings that legitimately
    reached PAYMENT via `transition_filing_status`).

    Recipients: all elevated Managers (in-app + email).
    """
    from app.enums import FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(
            ITRFiling.status == FilingStatus.PAYMENT,
            ITRFiling.payment_received_at.is_(None),
        )
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Latest entry-into-PAYMENT per filing (single grouped query)
    entry_rows = (await db.execute(
        select(
            FilingStateHistory.filing_id,
            func.max(FilingStateHistory.changed_at),
        )
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.PAYMENT,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at: dict[UUID, datetime] = {fid: ts for fid, ts in entry_rows}

    recipients = await _elevated_manager_ids(db)
    if not recipients:
        return (0, 0)

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = entered_at.get(f.id)
        if anchor is None:
            continue
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if anchor >= cutoff:
            continue

        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(anchor),
        }
        for uid in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=uid,
                related_client_id=f.client_id,
                related_filing_id=f.id,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1

    return (dispatched, skipped)


async def _eval_feedback_not_submitted(
    db: AsyncSession, cfg: ReminderConfig
) -> tuple[int, int]:
    """Reminder 13 — filing COMPLETED > threshold days, no FilingFeedback row.

    Anchor: `filings.completed_at`. Skip filings whose `completed_at` is NULL
    (shouldn't happen for COMPLETED status but defensive).

    Recipient: the client (in-app + email + WhatsApp).
    """
    from app.enums import FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_feedback import FilingFeedback

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(
            ITRFiling.status == FilingStatus.COMPLETED,
            ITRFiling.completed_at.isnot(None),
            ITRFiling.completed_at < cutoff,
        )
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Filings that already have feedback (single query)
    fb_rows = (await db.execute(
        select(FilingFeedback.filing_id).where(
            FilingFeedback.filing_id.in_(filing_ids)
        )
    )).all()
    have_fb = {row[0] for row in fb_rows}

    # Batch-fetch client names
    client_ids = list({f.client_id for f in filings})
    names = {
        uid: n
        for uid, n in (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(client_ids))
        )).all()
    }

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id in have_fb:
            continue
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(f.completed_at),
        }
        cand = ReminderCandidate(
            reminder_type=cfg.reminder_type,
            subject_user_id=f.client_id,
            related_client_id=f.client_id,
            related_filing_id=f.id,
            context=ctx,
        )
        if await dispatch_candidate(db, cfg, cand):
            dispatched += 1
        else:
            skipped += 1

    return (dispatched, skipped)


# ─── Routing registry ───────────────────────────────────────
# All 13 reminder evaluators are registered here.
_ROUTES: dict[ReminderType, Callable] = {
    ReminderType.UNASSIGNED_CLIENT: _eval_unassigned_client,
    ReminderType.FILING_NOT_INITIATED: _eval_filing_not_initiated,
    ReminderType.TAX_PAYMENT_PENDING: _eval_tax_payment_pending,
    ReminderType.FILING_STAGNANT_PRE_FILING: _eval_filing_stagnant_pre_filing,
    ReminderType.INVOICE_PENDING_POST_FILING: _eval_invoice_pending_post_filing,
    ReminderType.CLIENT_DOCS_PENDING_UPLOAD: _eval_client_docs_pending_upload,
    ReminderType.TEXT_FIELDS_PENDING_FILL: _eval_text_fields_pending_fill,
    ReminderType.COMPUTATION_AWAITING_MANAGER_APPROVAL: _eval_computation_awaiting_manager_approval,
    ReminderType.COMPUTATION_AWAITING_PARTNER_APPROVAL: _eval_computation_awaiting_partner_approval,
    ReminderType.COMPUTATION_AWAITING_CLIENT_APPROVAL: _eval_computation_awaiting_client_approval,
    ReminderType.COMPLETED_DOCS_PENDING: _eval_completed_docs_pending,
    ReminderType.PAYMENT_NOT_MARKED_RECEIVED: _eval_payment_not_marked_received,
    ReminderType.FEEDBACK_NOT_SUBMITTED: _eval_feedback_not_submitted,
}


async def evaluate_reminder_type(
    db: AsyncSession, rt: ReminderType, cfg: ReminderConfig
) -> tuple[int, int]:
    """Dispatch to the evaluator registered for `rt`. Returns `(dispatched, skipped)`."""
    fn = _ROUTES.get(rt)
    if fn is None:
        logger.debug("No evaluator registered for %s", rt.value)
        return (0, 0)
    return await fn(db, cfg)


# ─── Top-level orchestrator ─────────────────────────────────
async def dispatch_due_reminders(
    db: AsyncSession,
) -> tuple[dict[str, int], dict[str, int]]:
    """Iterate enabled configs, run their evaluators, commit, and return counts."""
    dispatched: dict[str, int] = {}
    skipped: dict[str, int] = {}
    configs = await get_all_configs(db)
    for cfg in configs:
        if not cfg.is_enabled:
            continue
        try:
            sent, sk = await evaluate_reminder_type(db, cfg.reminder_type, cfg)
            dispatched[cfg.reminder_type.value] = sent
            skipped[cfg.reminder_type.value] = sk
        except Exception as e:
            logger.warning("Reminder evaluator %s failed: %s", cfg.reminder_type.value, e)
            skipped[cfg.reminder_type.value] = skipped.get(cfg.reminder_type.value, 0) + 1
    await db.commit()
    return dispatched, skipped
