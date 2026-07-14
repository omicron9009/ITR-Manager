# Reminders Subsystem — One-Prompt-Per-Reminder Implementation Plan

> Backend: `backend/app/` — FastAPI, SQLAlchemy 2.0 async, PostgreSQL, MinIO, Redis.
> Repo convention: **NO Alembic at runtime**. Schema changes are applied imperatively on startup via `_sync_new_columns` / `_sync_pg_enums` in `backend/app/main.py`. Every new model must be imported in `backend/app/models/__init__.py`.

## How to use this file

Each numbered section is a **standalone, copy-paste prompt** that implements ONE reminder end-to-end (enum value + PG enum sync + seed row + evaluator + route registration + smoke test).

- **Prompt 1** is special: it lays down the shared scaffolding (models, tables, config, dispatcher, worker, router) alongside Reminder 1. Do this one first.
- **Prompts 2–13** each add exactly one reminder on top of the scaffolding.
- Run them in order. Each prompt tells the sub-agent which files to read first and what to change.

---

## 0. Locked-in design (do NOT re-negotiate — every prompt assumes these)

### 13 reminders

| # | ReminderType key | Anchor timestamp | Trigger | Recipients | Channels |
|---|---|---|---|---|---|
| 1 | `UNASSIGNED_CLIENT` | `users.activated_at` | Client ACTIVE, activated > N days AND (no active `ExecutiveClientAssignment` **OR** no active `ManagerExecutiveAssignment` for that exec **OR** `client_profiles.partner_tag_id IS NULL`) | Elevated Managers (`role=MANAGER, is_elevated=true, is_active=true`) | in-app + email |
| 2 | `FILING_NOT_INITIATED` | `users.activated_at` | Client ACTIVE, activated > N days AND no `ITRFiling` for current India FY | Client | in-app + email + WhatsApp |
| 3 | `TAX_PAYMENT_PENDING` | `filings.computation_approved_at` | Filing has a `FilingComputation` in status `PARTNER_APPROVED`/`CLIENT_APPROVED`, `is_tax_paid=false`, N days elapsed since `computation_approved_at`, status NOT IN (COMPLETED, HALTED) | Client | in-app + email + WhatsApp |
| 4 | `FILING_STAGNANT_PRE_FILING` | latest `FilingStateHistory.changed_at` | Filing status in {`DOCUMENT_UPLOAD`,`PROCESSING`,`COMPUTATION`}, ≥ 1 `FilingDocument` exists AND all are `APPROVED`, no state transition for N days | Assigned Executive + Manager | in-app + email |
| 5 | `INVOICE_PENDING_POST_FILING` | latest `FilingStateHistory.changed_at` where `to_status=FILING` | Filing status = `FILING`, no `FilingCompletedDoc` with `doc_type=INVOICE` AND `status=PARTNER_APPROVED`, N days elapsed | Elevated Managers | in-app + email |
| 6 | `CLIENT_DOCS_PENDING_UPLOAD` | latest `FilingStateHistory.changed_at` where `to_status=DOCUMENT_UPLOAD` | Filing status = `DOCUMENT_UPLOAD`, ≥ 1 `FilingDocument` with `status IN (PENDING_UPLOAD, REJECTED)`, N days elapsed | Client | in-app + email + WhatsApp |
| 7 | `TEXT_FIELDS_PENDING_FILL` | oldest `FilingTextField.created_at` with `status=PENDING` | Filing not in `COMPLETED`/`HALTED`, ≥ 1 `FilingTextField.status=PENDING` older than N days | Client | in-app + email + WhatsApp |
| 8 | `COMPUTATION_AWAITING_MANAGER_APPROVAL` | `filing_computations.uploaded_at` where `status=UPLOADED` | Any `FilingComputation.status=UPLOADED` with `uploaded_at < now - N days` | Assigned Manager (skip if none) | in-app + email |
| 9 | `COMPUTATION_AWAITING_PARTNER_APPROVAL` | `filing_computations.manager_approved_at` where `status=MANAGER_APPROVED` | Any `FilingComputation.status=MANAGER_APPROVED` with `manager_approved_at < now - N days` | All active Partners | in-app + email |
| 10 | `COMPUTATION_AWAITING_CLIENT_APPROVAL` | `filing_computations.partner_approved_at` where `status=PARTNER_APPROVED` | Latest computation per filing is `PARTNER_APPROVED` (not superseded/rejected/client-approved), `partner_approved_at < now - N days` | Client | in-app + email + WhatsApp |
| 11 | `COMPLETED_DOCS_PENDING` | latest `FilingStateHistory.changed_at` where `to_status=FILING` | Filing status = `FILING`, missing at least one PARTNER-APPROVED doc among {ITR_ACKNOWLEDGEMENT, INVOICE, ITR_JSON, ITR_FORM, TAX_PAID_COMPUTATION}, N days elapsed | Assigned Executive + Manager | in-app + email |
| 12 | `PAYMENT_NOT_MARKED_RECEIVED` | latest `FilingStateHistory.changed_at` where `to_status=PAYMENT` | Filing status = `PAYMENT`, `payment_received_at IS NULL`, N days elapsed | Elevated Managers | in-app + email |
| 13 | `FEEDBACK_NOT_SUBMITTED` | `filings.completed_at` | Filing status = `COMPLETED`, `completed_at < now - N days`, no `FilingFeedback` row for the filing | Client | in-app + email + WhatsApp |

### Global settings
- All configs **seeded disabled** with defaults: `threshold_days=7`, `repeat_interval_days=3`, `max_sends=5`, all channels enabled.
- Worker cadence: **6 hours** (`REMINDERS_WORKER_INTERVAL_SECONDS=21600`).
- Timezone: `Asia/Kolkata` for FY computation.
- FY format: `"YYYY-YYYY"` (India Apr-Mar).

### Delivery
Use `services/notification_service.py::create_notification` — already fans out in-app + email + WhatsApp (WhatsApp only for CLIENT with opt-in). The `channels` JSONB on `reminder_configs` gates **in-app / email** via the `NotificationChannel` passed to `create_notification`. The `whatsapp` sub-flag on `channels` is stored for future use but is **not enforced in v1** — WhatsApp is already gated inside `create_notification` to CLIENT-with-opt-in only, and no changes to `notification_service.py` are made by this plan. Document this behaviour in the router response comments; a future PR can plumb a `suppress_whatsapp` parameter through if the operator wants finer control.

### Dedup / rate-limit
- `dedup_key = "{ReminderType.value}:{filing_id or 'no_filing'}:{recipient_user_id}"`
- If `max_sends > 0` and existing rows >= max_sends → skip.
- If newest `sent_at > now - repeat_interval_days` (and `repeat_interval_days > 0`) → skip.
- Otherwise dispatch, log a `reminder_dispatch_logs` row, emit `REMINDER_SENT` audit.

### API surface — Partner-only
```
GET    /api/v1/reminders/configs
GET    /api/v1/reminders/configs/{reminder_type}
PUT    /api/v1/reminders/configs/{reminder_type}
POST   /api/v1/reminders/configs/{reminder_type}/pause
POST   /api/v1/reminders/configs/{reminder_type}/resume
GET    /api/v1/reminders/dispatch-log?reminder_type=&client_id=&filing_id=&page=&page_size=
POST   /api/v1/reminders/run-now
```

### Audit events (added incrementally — Prompt 1 adds all four)
`REMINDER_CONFIG_UPDATED`, `REMINDER_CONFIG_PAUSED`, `REMINDER_CONFIG_RESUMED`, `REMINDER_SENT`.

---

# Prompt 1 — Reminder 1: `UNASSIGNED_CLIENT` (+ full scaffolding)

> This is the biggest prompt. It builds the entire reminders subsystem infrastructure alongside Reminder 1 so that Prompts 2–13 can each add just their evaluator + enum value.

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`.

CONTEXT: We are building a reminders subsystem. This prompt implements the FIRST reminder (`UNASSIGNED_CLIENT`) end-to-end AND lays down all shared scaffolding (enum shell, models, tables, config, dispatcher, worker, router). Prompts 2–13 will each add only their evaluator + enum value on top of this scaffolding.

READ FIRST (in order — do not skip):
1. `CLAUDE.md` at repo root — especially the "Database lifecycle — NO Alembic migrations at runtime" section
2. `backend/app/enums.py` — full file (patterns, `AuditEventType`, `NotificationChannel`, `UserRole`, `AccountStatus`)
3. `backend/app/config.py` — settings pattern (esp. WhatsApp block)
4. `backend/app/main.py` — full `lifespan`, `_sync_pg_enums`, `_sync_new_columns` (esp. the `internal_working_doc_type` enum + `CREATE TABLE IF NOT EXISTS` blocks), `_seed_admin_user`, `_whatsapp_watchdog_loop`
5. `backend/app/models/__init__.py`, `backend/app/models/notification.py`, `backend/app/models/whatsapp_config.py`
6. `backend/app/services/notification_service.py` — full `create_notification` signature
7. `backend/app/services/audit_service.py` — `record_audit_event` signature
8. `backend/app/models/user.py`, `backend/app/models/client_profile.py`, `backend/app/models/executive_assignment.py`, `backend/app/models/manager_executive_assignment.py`
9. `backend/app/api/v1/router.py`, `backend/app/api/v1/whatsapp.py` (template for a Partner-only router)
10. `backend/app/core/security.py` — find `get_current_partner`

DESIGN RULES (locked — do NOT change):
- Enum name: `ReminderType(str, enum.Enum)`. This prompt adds ONLY `UNASSIGNED_CLIENT`. Subsequent prompts will append more values.
- `reminder_configs` seeded disabled by default. Defaults: `threshold_days=7`, `repeat_interval_days=3`, `max_sends=5`, `channels={"in_app": true, "email": true, "whatsapp": true}`.
- Worker: 6-hour interval, 120s initial delay, fails open.
- Dedup key format: `"{ReminderType.value}:{filing_id or 'no_filing'}:{recipient_user_id}"`.
- Reminder 1 recipients: elevated Managers only. Channels: in-app + email (WhatsApp is naturally gated to CLIENT only inside `create_notification`).

────────────────────────────────────────────────────────
STEP 1 — Enum additions in `backend/app/enums.py`
────────────────────────────────────────────────────────

1a. Add new class ABOVE `# Valid state transitions ...`:
```python
class ReminderType(str, enum.Enum):
    UNASSIGNED_CLIENT = "UNASSIGNED_CLIENT"
```
(Subsequent prompts will append more values — keep insertion order.)

1b. Extend `AuditEventType` with (append near end):
```python
    REMINDER_CONFIG_UPDATED = "REMINDER_CONFIG_UPDATED"
    REMINDER_CONFIG_PAUSED = "REMINDER_CONFIG_PAUSED"
    REMINDER_CONFIG_RESUMED = "REMINDER_CONFIG_RESUMED"
    REMINDER_SENT = "REMINDER_SENT"
```

1c. Add two module-level dicts at the bottom of the file:
```python
REMINDER_DEFAULT_LABELS: dict[ReminderType, str] = {
    ReminderType.UNASSIGNED_CLIENT: "Client not fully assigned",
}

REMINDER_DEFAULT_MESSAGES: dict[ReminderType, str] = {
    ReminderType.UNASSIGNED_CLIENT: (
        "Client {client_name} was activated {days} day(s) ago but is missing: {missing}. "
        "Please complete the assignment so filing work can begin."
    ),
}
```

────────────────────────────────────────────────────────
STEP 2 — Pydantic schemas in `backend/app/schemas/reminder.py` (new file)
────────────────────────────────────────────────────────

Create `backend/app/schemas/reminder.py` with Pydantic v2:

- `ReminderChannels`: `in_app: bool = True`, `email: bool = True`, `whatsapp: bool = True`, `model_config = ConfigDict(from_attributes=True)`.
- `ReminderConfigResponse`: `id: UUID`, `reminder_type: ReminderType`, `is_enabled: bool`, `threshold_days: int`, `repeat_interval_days: int`, `max_sends: int`, `channels: ReminderChannels`, `custom_title: Optional[str]`, `custom_message: Optional[str]`, `updated_by: Optional[UUID]`, `updated_at: datetime`, `created_at: datetime`. `from_attributes=True`.
- `ReminderConfigUpdate` — all fields optional with validation ranges: `threshold_days ge=0 le=365`, `repeat_interval_days ge=0 le=365`, `max_sends ge=0 le=100`, `custom_title max_length=255`, `custom_message max_length=2000`.
- `ReminderDispatchLogResponse`: `id`, `reminder_type`, `subject_user_id`, `related_client_id: Optional[UUID]`, `related_filing_id: Optional[UUID]`, `dedup_key: str`, `notification_id: Optional[UUID]`, `sent_at: datetime`. `from_attributes=True`.
- `ReminderDispatchLogPage`: `items: list[ReminderDispatchLogResponse]`, `total: int`, `page: int`, `page_size: int`.
- `ReminderRunNowResponse`: `started_at: datetime`, `finished_at: datetime`, `dispatched: dict[str, int]`, `skipped: dict[str, int]`.

────────────────────────────────────────────────────────
STEP 3 — SQLAlchemy models
────────────────────────────────────────────────────────

Create `backend/app/models/reminder_config.py`:
- `ReminderConfig(Base)`, `__tablename__ = "reminder_configs"`.
- Columns: `id` UUID PK default uuid4; `reminder_type` `Enum(ReminderType, name="reminder_type")` NOT NULL UNIQUE; `is_enabled` BOOL NOT NULL default False, `server_default="false"`; `threshold_days` Integer NOT NULL default 7, `server_default="7"`; `repeat_interval_days` Integer NOT NULL default 3, `server_default="3"`; `max_sends` Integer NOT NULL default 5, `server_default="5"`; `channels` JSONB NOT NULL default `lambda: {"in_app": True, "email": True, "whatsapp": True}`, `server_default=text("'{\"in_app\": true, \"email\": true, \"whatsapp\": true}'::jsonb")`; `custom_title` String(255) nullable; `custom_message` Text nullable; `updated_by` UUID FK `users.id` ondelete SET NULL nullable; `updated_at` DateTime(tz) NOT NULL default now onupdate now; `created_at` DateTime(tz) NOT NULL default now.

Create `backend/app/models/reminder_dispatch_log.py`:
- `ReminderDispatchLog(Base)`, `__tablename__ = "reminder_dispatch_logs"`.
- Columns: `id` UUID PK default uuid4; `reminder_type` `Enum(ReminderType, name="reminder_type")` NOT NULL; `subject_user_id` UUID FK `users.id` ondelete CASCADE NOT NULL; `related_client_id` UUID FK `users.id` ondelete SET NULL nullable; `related_filing_id` UUID FK `itr_filings.id` ondelete SET NULL nullable; `dedup_key` String(255) NOT NULL; `notification_id` UUID FK `notifications.id` ondelete SET NULL nullable; `sent_at` DateTime(tz) NOT NULL default now.
- `__table_args__ = (Index("ix_reminder_dispatch_dedup_sent", "dedup_key", "sent_at"),)`.

Register both in `backend/app/models/__init__.py` (append with `# noqa: F401`):
```python
from app.models.reminder_config import ReminderConfig  # noqa: F401
from app.models.reminder_dispatch_log import ReminderDispatchLog  # noqa: F401
```

────────────────────────────────────────────────────────
STEP 4 — Settings in `backend/app/config.py`
────────────────────────────────────────────────────────

Append inside `Settings` after the WhatsApp block:
```python
    # ─── Reminders subsystem ────────────────────────────────
    REMINDERS_WORKER_ENABLED: bool = True
    REMINDERS_WORKER_INTERVAL_SECONDS: int = 21600  # 6 hours
    REMINDERS_TIMEZONE: str = "Asia/Kolkata"
    REMINDERS_WORKER_INITIAL_DELAY_SECONDS: int = 120
```

────────────────────────────────────────────────────────
STEP 5 — Startup DDL & seed in `backend/app/main.py`
────────────────────────────────────────────────────────

5a. In `_sync_pg_enums`: add `"reminder_type": ReminderType` to the `enum_map`. **Also add `ReminderType` to the local `from app.enums import ...` block at the top of that function** (it currently imports many other enums the same way). This lets future values be auto-`ALTER TYPE ... ADD VALUE`-ed on every startup.

5b. In `_sync_new_columns` (inside the `try:` block, after existing `CREATE TABLE IF NOT EXISTS` sections):

- Ensure PG type exists (idempotent, guard with `pg_type` existence check like the `internal_working_doc_type` block does):
  ```sql
  CREATE TYPE reminder_type AS ENUM ('UNASSIGNED_CLIENT')
  ```
  (Only one value now — later prompts will `ALTER TYPE ... ADD VALUE`. But since `_sync_pg_enums` runs earlier and now knows about `reminder_type`, missing values will be auto-added on future startups.)

- Ensure `reminder_configs` table (`CREATE TABLE IF NOT EXISTS`):
  ```sql
  CREATE TABLE reminder_configs (
      id UUID PRIMARY KEY,
      reminder_type reminder_type NOT NULL UNIQUE,
      is_enabled BOOLEAN NOT NULL DEFAULT false,
      threshold_days INTEGER NOT NULL DEFAULT 7,
      repeat_interval_days INTEGER NOT NULL DEFAULT 3,
      max_sends INTEGER NOT NULL DEFAULT 5,
      channels JSONB NOT NULL DEFAULT '{"in_app": true, "email": true, "whatsapp": true}'::jsonb,
      custom_title VARCHAR(255),
      custom_message TEXT,
      updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  ```

- Ensure `reminder_dispatch_logs` table:
  ```sql
  CREATE TABLE reminder_dispatch_logs (
      id UUID PRIMARY KEY,
      reminder_type reminder_type NOT NULL,
      subject_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      related_client_id UUID REFERENCES users(id) ON DELETE SET NULL,
      related_filing_id UUID REFERENCES itr_filings(id) ON DELETE SET NULL,
      dedup_key VARCHAR(255) NOT NULL,
      notification_id UUID REFERENCES notifications(id) ON DELETE SET NULL,
      sent_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  ```
  Then:
  ```sql
  CREATE INDEX IF NOT EXISTS ix_reminder_dispatch_dedup_sent
      ON reminder_dispatch_logs (dedup_key, sent_at)
  ```

All checks must use `information_schema.tables` / `pg_type` / `pg_indexes` guards and log an INFO line on creation, matching the repo style.

5c. Add helper `_seed_reminder_configs()` near `_seed_admin_user`:
- Iterate `ReminderType` values. For each, if no `ReminderConfig` row exists for that type, insert one with the locked defaults: `is_enabled=False`, `threshold_days=7`, `repeat_interval_days=3`, `max_sends=5`, `channels={"in_app": True, "email": True, "whatsapp": True}`, `custom_title=REMINDER_DEFAULT_LABELS.get(t)`, `custom_message=REMINDER_DEFAULT_MESSAGES.get(t)`. Commit. Wrap in try/except; log a warning on failure.

5d. Call `await _seed_reminder_configs()` in `lifespan` after `_seed_dashboard_user()` and before `init_cache()`.

────────────────────────────────────────────────────────
STEP 6 — Shared service in `backend/app/services/reminder_service.py` (new file)
────────────────────────────────────────────────────────

Create this new file with the following contents. Use `from __future__ import annotations`.

Public exports:
- `@dataclass class ReminderCandidate` — fields: `reminder_type: ReminderType`, `subject_user_id: UUID`, `related_client_id: Optional[UUID]`, `related_filing_id: Optional[UUID]`, `context: dict`.
- `def compute_current_indian_fy(now: Optional[datetime] = None) -> str` — return `"YYYY-YYYY"` for India FY (Apr-Mar) in `settings.REMINDERS_TIMEZONE` via `ZoneInfo`.
- `def _dedup_key(rt, filing_id, user_id) -> str`.
- `async def get_config(db, rt) -> Optional[ReminderConfig]`.
- `async def get_all_configs(db) -> list[ReminderConfig]`.
- `def _channel_from_flags(in_app: bool, email: bool) -> Optional[NotificationChannel]` — BOTH / EMAIL / IN_APP / None.
- `def _days_since(ts: datetime) -> int` — floor days between now(UTC) and ts (treat naive as UTC).
- `def _render(template: str, ctx: dict) -> str` — safe `str.format_map(defaultdict(str, ctx))`.
- `async def _should_send(db, candidate, cfg) -> tuple[bool, str]` — check `max_sends` and `repeat_interval_days` using `reminder_dispatch_logs`.
- `async def _elevated_manager_ids(db) -> list[UUID]` — `role=MANAGER, is_elevated=True, is_active=True`.
- `async def _partner_ids(db) -> list[UUID]` — `role=PARTNER, is_active=True`.
- `async def _exec_and_manager_for_client(db, client_id) -> tuple[Optional[UUID], Optional[UUID]]` — resolve via `ExecutiveClientAssignment` (is_active) and `ManagerExecutiveAssignment` (is_active).
- `async def dispatch_candidate(db, cfg, candidate) -> bool` — see below.
- `async def evaluate_reminder_type(db, rt, cfg) -> tuple[int, int]` — dispatch to per-type evaluator using a `routes: dict[ReminderType, Callable]` registry.
- `async def dispatch_due_reminders(db) -> tuple[dict[str, int], dict[str, int]]` — iterate enabled configs, run evaluators, aggregate counts, commit, return.

`dispatch_candidate(db, cfg, candidate)`:
1. If `cfg.is_enabled is False` → return False.
2. `(ok, _reason) = await _should_send(db, candidate, cfg)`. If not ok → return False.
3. Compute channel via `_channel_from_flags(cfg.channels.get("in_app", True), cfg.channels.get("email", True))`. If None → return False.
4. `title = cfg.custom_title or REMINDER_DEFAULT_LABELS.get(cfg.reminder_type, cfg.reminder_type.value)`.
5. `template = cfg.custom_message or REMINDER_DEFAULT_MESSAGES.get(cfg.reminder_type, "")`. `message = _render(template, candidate.context)`.
6. Call `create_notification(db=db, user_id=candidate.subject_user_id, title=title, message=message, channel=<computed>, related_filing_id=candidate.related_filing_id, related_client_id=candidate.related_client_id, client_name=candidate.context.get("client_name"), financial_year=candidate.context.get("fy"), filing_status=candidate.context.get("filing_status"), cta_label=candidate.context.get("cta_label"), action_url_path=candidate.context.get("action_url_path"))`.
7. Insert a `ReminderDispatchLog(reminder_type=cfg.reminder_type, subject_user_id=..., related_client_id=..., related_filing_id=..., dedup_key=_dedup_key(...), notification_id=notification.id)` via `db.add(...)`; `await db.flush()`.
8. `await record_audit_event(db, AuditEventType.REMINDER_SENT, actor_id=None, client_id=candidate.related_client_id, filing_id=candidate.related_filing_id, details={"reminder_type": cfg.reminder_type.value, "recipient": str(candidate.subject_user_id)})`.
9. Return True.

`dispatch_due_reminders(db)`:
- Iterate `await get_all_configs(db)`, skip disabled.
- For each enabled config wrap the evaluator call in `try/except` — on error log warning and count 1 skip.
- At the end `await db.commit()` and return `(dispatched_by_type, skipped_by_type)`.

────────────────────────────────────────────────────────
STEP 7 — Reminder 1 evaluator: `_eval_unassigned_client`
────────────────────────────────────────────────────────

Add to `reminder_service.py`:

```python
async def _eval_unassigned_client(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 1 — clients activated > threshold_days with missing exec/manager/partner-tag."""
    from datetime import datetime, timezone, timedelta
    from app.models.client_profile import ClientProfile
    from app.models.executive_assignment import ExecutiveClientAssignment
    from app.models.manager_executive_assignment import ManagerExecutiveAssignment
    from app.enums import AccountStatus

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Candidate clients (activated long enough ago, ACTIVE, is_active=true)
    q = (
        select(User, ClientProfile)
        .join(ClientProfile, ClientProfile.user_id == User.id, isouter=True)
        .where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.ACTIVE,
            User.is_active == True,
            User.activated_at.isnot(None),
            User.activated_at < cutoff,
        )
    )
    rows = (await db.execute(q)).all()
    if not rows:
        return (0, 0)

    client_ids = [u.id for (u, _p) in rows]

    # Active exec assignments — client_id -> executive_id
    exec_map: dict[UUID, UUID] = {}
    if client_ids:
        exec_rows = (await db.execute(
            select(ExecutiveClientAssignment.client_id, ExecutiveClientAssignment.executive_id)
            .where(
                ExecutiveClientAssignment.client_id.in_(client_ids),
                ExecutiveClientAssignment.is_active == True,
            )
        )).all()
        for cid, eid in exec_rows:
            exec_map[cid] = eid

    # Active manager assignments for those executives — executive_id -> manager_id
    exec_ids = list(set(exec_map.values()))
    mgr_map: dict[UUID, UUID] = {}
    if exec_ids:
        mgr_rows = (await db.execute(
            select(ManagerExecutiveAssignment.executive_id, ManagerExecutiveAssignment.manager_id)
            .where(
                ManagerExecutiveAssignment.executive_id.in_(exec_ids),
                ManagerExecutiveAssignment.is_active == True,
            )
        )).all()
        for eid, mid in mgr_rows:
            mgr_map[eid] = mid

    recipients = await _elevated_manager_ids(db)
    if not recipients:
        return (0, len(rows))

    dispatched = 0
    skipped = 0
    for user, profile in rows:
        missing: list[str] = []
        exec_id = exec_map.get(user.id)
        if exec_id is None:
            missing.append("executive")
        else:
            if mgr_map.get(exec_id) is None:
                missing.append("manager")
        if profile is None or profile.partner_tag_id is None:
            missing.append("partner_tag")

        if not missing:
            continue  # fully assigned

        ctx = {
            "client_name": user.full_name,
            "days": _days_since(user.activated_at),
            "missing": ", ".join(missing),
        }
        for mgr_id in recipients:
            cand = ReminderCandidate(
                reminder_type=cfg.reminder_type,
                subject_user_id=mgr_id,
                related_client_id=user.id,
                related_filing_id=None,
                context=ctx,
            )
            if await dispatch_candidate(db, cfg, cand):
                dispatched += 1
            else:
                skipped += 1
    return (dispatched, skipped)
```

And register the routes registry:

```python
_ROUTES: dict[ReminderType, Callable] = {
    ReminderType.UNASSIGNED_CLIENT: _eval_unassigned_client,
}

async def evaluate_reminder_type(db, rt, cfg):
    fn = _ROUTES.get(rt)
    if fn is None:
        logger.debug("No evaluator registered for %s", rt.value)
        return (0, 0)
    return await fn(db, cfg)
```

────────────────────────────────────────────────────────
STEP 8 — Router in `backend/app/api/v1/reminders.py` (new file)
────────────────────────────────────────────────────────

Create the file. All endpoints Partner-only (`Depends(get_current_partner)`).

- `GET /configs` → `list[ReminderConfigResponse]` — order by `reminder_type` name.
- `GET /configs/{reminder_type}` → `ReminderConfigResponse` — 404 if missing.
- `PUT /configs/{reminder_type}` → `ReminderConfigResponse` — body `ReminderConfigUpdate`, apply non-None fields, set `updated_by=current_user.id`, `updated_at=now`. If `channels` provided replace the whole JSONB. Emit `REMINDER_CONFIG_UPDATED` audit with the changed field names in `details`.
- `POST /configs/{reminder_type}/pause` → set `is_enabled=False`. Audit `REMINDER_CONFIG_PAUSED`.
- `POST /configs/{reminder_type}/resume` → set `is_enabled=True`. Audit `REMINDER_CONFIG_RESUMED`.
- `GET /dispatch-log` — filters: `reminder_type: Optional[ReminderType]`, `client_id: Optional[UUID]`, `filing_id: Optional[UUID]`, `page: int = 1 (ge=1)`, `page_size: int = 20 (ge=1, le=100)`. Order by `sent_at DESC`. Return `ReminderDispatchLogPage`.
- `POST /run-now` → capture `started_at = datetime.now(timezone.utc)`, call `dispatch, skipped = await dispatch_due_reminders(db)`, capture `finished_at`. Return `ReminderRunNowResponse`.

Mount in `backend/app/api/v1/router.py`:
- Add `reminders` to the import tuple.
- Append `api_router.include_router(reminders.router, prefix="/reminders", tags=["Reminders"])`.

────────────────────────────────────────────────────────
STEP 9 — Background worker in `backend/app/main.py`
────────────────────────────────────────────────────────

9a. Add `_reminders_worker_loop()` near `_whatsapp_watchdog_loop`, modelled on it:
```python
async def _reminders_worker_loop():
    from app.database import AsyncSessionLocal
    from app.services.reminder_service import dispatch_due_reminders

    initial = max(0, int(settings.REMINDERS_WORKER_INITIAL_DELAY_SECONDS))
    if initial:
        await asyncio.sleep(initial)

    while True:
        try:
            async with AsyncSessionLocal() as db:
                dispatched, skipped = await dispatch_due_reminders(db)
                if dispatched or skipped:
                    logger.info(
                        "Reminders worker tick: dispatched=%s skipped=%s",
                        dispatched, skipped,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Reminders worker tick failed: %s", e)
        try:
            await asyncio.sleep(settings.REMINDERS_WORKER_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
```

9b. In `lifespan()`, alongside the existing WhatsApp watchdog start/stop blocks:
- Startup:
  ```python
  reminders_task = None
  if settings.REMINDERS_WORKER_ENABLED:
      reminders_task = asyncio.create_task(_reminders_worker_loop())
      logger.info(
          "Reminders worker started (interval=%ds).",
          settings.REMINDERS_WORKER_INTERVAL_SECONDS,
      )
  ```
- Shutdown (after WhatsApp watchdog teardown):
  ```python
  if reminders_task is not None:
      reminders_task.cancel()
      try:
          await reminders_task
      except asyncio.CancelledError:
          pass
      logger.info("Reminders worker stopped.")
  ```

────────────────────────────────────────────────────────
STEP 10 — Verify
────────────────────────────────────────────────────────

1. `python -c "from app.enums import ReminderType, REMINDER_DEFAULT_LABELS, AuditEventType; assert AuditEventType.REMINDER_SENT; assert ReminderType.UNASSIGNED_CLIENT"` from `backend/`.
2. Boot the app: `uvicorn app.main:app --reload --port 8000`. Startup log must contain "Reminders worker started (interval=21600s).".
3. `SELECT reminder_type, is_enabled FROM reminder_configs;` returns exactly 1 row: `UNASSIGNED_CLIENT | false`.
4. `GET /api/v1/reminders/configs` with a Partner JWT → 200, 1 item; with a Client JWT → 403.
5. `POST /api/v1/reminders/run-now` with a Partner JWT → 200, `dispatched={}` (config disabled).
6. Enable and test:
   ```sql
   UPDATE reminder_configs SET is_enabled=true, threshold_days=0, repeat_interval_days=0, max_sends=1
   WHERE reminder_type='UNASSIGNED_CLIENT';
   ```
   Ensure at least one activated CLIENT exists without a full assignment chain (missing exec / manager / partner_tag) and at least one Manager with `is_elevated=true, is_active=true`.
   `POST /api/v1/reminders/run-now` → response shows `dispatched.UNASSIGNED_CLIENT > 0`. Verify:
   - Row in `reminder_dispatch_logs`.
   - Row in `notifications` for each elevated manager.
   - Audit row with `event_type=REMINDER_SENT`.
7. Re-run `POST /run-now` → same clients skipped (max_sends reached).
8. Reset the config back to defaults (`is_enabled=false, threshold_days=7, repeat_interval_days=3, max_sends=5`) via `PUT /api/v1/reminders/configs/UNASSIGNED_CLIENT`.

STOP here. Do NOT preempt Prompts 2–13.
````

---

# Prompt 2 — Reminder 2: `FILING_NOT_INITIATED`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompt 1's scaffolding is already in place.

CONTEXT: Add Reminder 2 — nudge a client whose account was activated > N days ago but who has not initiated an `ITRFiling` for the current India FY.

READ FIRST:
1. `backend/app/enums.py` — confirm `ReminderType` exists and note current members
2. `backend/app/services/reminder_service.py` — helpers: `compute_current_indian_fy`, `_days_since`, `dispatch_candidate`, `ReminderCandidate`, `_ROUTES`
3. `backend/app/models/user.py`, `backend/app/models/client_profile.py`, `backend/app/models/filing.py`
4. `backend/app/main.py::_sync_new_columns` — for the pattern used to `ALTER TYPE reminder_type ADD VALUE IF NOT EXISTS`

RECIPIENT: the client themselves. Channels: in-app + email + WhatsApp (WhatsApp naturally gated inside `create_notification`).

────────────────────────────────────────────────────────
STEP 1 — Add enum value
────────────────────────────────────────────────────────

In `backend/app/enums.py`:
- Append to `ReminderType`:
  ```python
      FILING_NOT_INITIATED = "FILING_NOT_INITIATED"
  ```
- Add to `REMINDER_DEFAULT_LABELS`:
  ```python
      ReminderType.FILING_NOT_INITIATED: "Filing not yet initiated",
  ```
- Add to `REMINDER_DEFAULT_MESSAGES`:
  ```python
      ReminderType.FILING_NOT_INITIATED: (
          "Hi {client_name}, your account has been active for {days} day(s) but you haven't "
          "initiated your ITR filing for FY {fy} yet. Please start the filing to receive your "
          "document checklist."
      ),
  ```

────────────────────────────────────────────────────────
STEP 2 — PG enum sync
────────────────────────────────────────────────────────

`_sync_pg_enums` will auto-add the new value on next startup because `reminder_type` is already in its `enum_map`. Nothing more to do here — but VERIFY on your dev DB after startup:
```sql
SELECT unnest(enum_range(NULL::reminder_type));
```
must include `FILING_NOT_INITIATED`.

────────────────────────────────────────────────────────
STEP 3 — Seed row
────────────────────────────────────────────────────────

`_seed_reminder_configs` iterates all `ReminderType` values so the new row will be seeded automatically on next startup with defaults (disabled, threshold=7, interval=3, max_sends=5, all channels). No code change needed.

────────────────────────────────────────────────────────
STEP 4 — Evaluator
────────────────────────────────────────────────────────

Add to `backend/app/services/reminder_service.py` (near `_eval_unassigned_client`):

```python
async def _eval_filing_not_initiated(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 2 — ACTIVE clients activated > threshold_days with no filing for current FY."""
    from datetime import datetime, timezone, timedelta
    from app.enums import AccountStatus
    from app.models.filing import ITRFiling

    fy = compute_current_indian_fy()
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Clients matching activation criteria
    q = select(User).where(
        User.role == UserRole.CLIENT,
        User.account_status == AccountStatus.ACTIVE,
        User.is_active == True,
        User.activated_at.isnot(None),
        User.activated_at < cutoff,
    )
    clients = list((await db.execute(q)).scalars().all())
    if not clients:
        return (0, 0)

    client_ids = [c.id for c in clients]
    # Filings already existing for the current FY — exclude those clients
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
```

Register in `_ROUTES`:
```python
_ROUTES[ReminderType.FILING_NOT_INITIATED] = _eval_filing_not_initiated
```

────────────────────────────────────────────────────────
STEP 5 — Verify
────────────────────────────────────────────────────────

1. Restart the app; startup log should note the enum sync if the value was added.
2. `SELECT reminder_type, is_enabled FROM reminder_configs;` → now 2 rows.
3. `GET /api/v1/reminders/configs` → 2 items.
4. Enable via `PUT /api/v1/reminders/configs/FILING_NOT_INITIATED` with `is_enabled=true, threshold_days=0, repeat_interval_days=0, max_sends=1`.
5. Ensure at least one activated client without an FY filing exists.
6. `POST /api/v1/reminders/run-now` → `dispatched.FILING_NOT_INITIATED > 0`; a `notifications` row is created for that client; `reminder_dispatch_logs` row exists.
7. Re-run → same client skipped (max_sends).
8. Reset config back to defaults.
````

---

# Prompt 3 — Reminder 3: `TAX_PAYMENT_PENDING`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–2 are complete.

CONTEXT: Add Reminder 3 — nudge the client when a computation is approved by Partner (or Client) but tax has not been marked paid within N days of `filings.computation_approved_at`.

READ FIRST:
1. `backend/app/enums.py` — confirm current `ReminderType` values
2. `backend/app/services/reminder_service.py` — reuse helpers
3. `backend/app/models/filing.py` — `computation_approved_at`, `is_tax_paid`, `status`
4. `backend/app/models/filing_computation.py` and `backend/app/enums.py::ComputationStatus`
5. `backend/app/api/v1/computations.py` — for how `computation_approved_at` and `is_tax_paid` are set (context)

RECIPIENT: the client. Channels: in-app + email + WhatsApp.

────────────────────────────────────────────────────────
STEP 1 — Add enum value + labels/messages
────────────────────────────────────────────────────────

Append to `ReminderType`:
```python
    TAX_PAYMENT_PENDING = "TAX_PAYMENT_PENDING"
```

Add label:
```python
    ReminderType.TAX_PAYMENT_PENDING: "Tax payment confirmation pending",
```

Add message:
```python
    ReminderType.TAX_PAYMENT_PENDING: (
        "Hi {client_name}, your ITR computation for FY {fy} was approved {days} day(s) ago but "
        "we haven't received your tax payment confirmation yet. Please pay the tax and confirm, "
        "or let us know if a refund is expected."
    ),
```

────────────────────────────────────────────────────────
STEP 2 — Evaluator
────────────────────────────────────────────────────────

Add to `reminder_service.py`:

```python
async def _eval_tax_payment_pending(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 3 — computation approved > threshold_days ago but tax not marked paid."""
    from datetime import datetime, timezone, timedelta
    from app.enums import ComputationStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Filings where computation is approved (timestamp set) but tax unpaid, and not terminal
    q = (
        select(ITRFiling)
        .where(
            ITRFiling.is_tax_paid == False,
            ITRFiling.computation_approved_at.isnot(None),
            ITRFiling.computation_approved_at < cutoff,
            ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
        )
    )
    filings = list((await db.execute(q)).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]
    # Safety cross-check: at least one computation in PARTNER_APPROVED or CLIENT_APPROVED
    approved_rows = (await db.execute(
        select(FilingComputation.filing_id)
        .where(
            FilingComputation.filing_id.in_(filing_ids),
            FilingComputation.status.in_([
                ComputationStatus.PARTNER_APPROVED,
                ComputationStatus.CLIENT_APPROVED,
            ]),
        )
    )).all()
    approved_ids = {row[0] for row in approved_rows}

    # Client names in one shot
    client_ids = list({f.client_id for f in filings})
    name_rows = (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(client_ids))
    )).all()
    names = {uid: n for uid, n in name_rows}

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
```

Register:
```python
_ROUTES[ReminderType.TAX_PAYMENT_PENDING] = _eval_tax_payment_pending
```

────────────────────────────────────────────────────────
STEP 3 — Verify
────────────────────────────────────────────────────────

1. Restart, confirm 3 rows in `reminder_configs`.
2. Enable via `PUT` with low thresholds.
3. Prep DB: at least one filing with `computation_approved_at IS NOT NULL`, `is_tax_paid=false`, status not in COMPLETED/HALTED, and a matching `FilingComputation` in PARTNER_APPROVED or CLIENT_APPROVED.
4. `POST /api/v1/reminders/run-now` → `dispatched.TAX_PAYMENT_PENDING > 0`.
5. Verify a client-owned notification row and a dispatch log row.
6. Reset config to defaults.
````

---

# Prompt 4 — Reminder 4: `FILING_STAGNANT_PRE_FILING`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–3 are complete.

CONTEXT: Add Reminder 4 — nudge Exec + Manager when a filing sits in DOCUMENT_UPLOAD/PROCESSING/COMPUTATION with all client documents APPROVED but no state transition for N days.

READ FIRST:
1. `backend/app/services/reminder_service.py` — helpers `_exec_and_manager_for_client`, `_days_since`, etc.
2. `backend/app/models/filing.py`, `backend/app/models/filing_document.py`, `backend/app/models/filing_state_history.py`
3. `backend/app/enums.py` — `FilingStatus`, `DocumentStatus`
4. `backend/app/services/filing_service.py::transition_filing_status` (for context on how `FilingStateHistory` is written)

RECIPIENT: assigned Executive + Manager. Channels: in-app + email.

────────────────────────────────────────────────────────
STEP 1 — Add enum value + labels/messages
────────────────────────────────────────────────────────

Append to `ReminderType`:
```python
    FILING_STAGNANT_PRE_FILING = "FILING_STAGNANT_PRE_FILING"
```

Label:
```python
    ReminderType.FILING_STAGNANT_PRE_FILING: "Filing not progressing",
```

Message:
```python
    ReminderType.FILING_STAGNANT_PRE_FILING: (
        "Filing for {client_name} (FY {fy}) is in {filing_status} with all documents approved but "
        "hasn't progressed for {days} day(s). Please advance it toward FILING."
    ),
```

────────────────────────────────────────────────────────
STEP 2 — Evaluator
────────────────────────────────────────────────────────

Add to `reminder_service.py`:

```python
async def _eval_filing_stagnant_pre_filing(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 4 — filing pre-FILING, all docs approved, no transition for threshold_days."""
    from datetime import datetime, timezone, timedelta
    from app.enums import DocumentStatus, FilingStatus
    from app.models.filing import ITRFiling
    from app.models.filing_document import FilingDocument
    from app.models.filing_state_history import FilingStateHistory

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.status.in_([
            FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING, FilingStatus.COMPUTATION,
        ]))
    )).scalars().all())
    if not filings:
        return (0, 0)

    filing_ids = [f.id for f in filings]

    # Latest state-history changed_at per filing
    last_change_rows = (await db.execute(
        select(FilingStateHistory.filing_id, func.max(FilingStateHistory.changed_at))
        .where(FilingStateHistory.filing_id.in_(filing_ids))
        .group_by(FilingStateHistory.filing_id)
    )).all()
    last_change = {fid: ts for fid, ts in last_change_rows}

    # Doc counts per filing (total, approved)
    doc_rows = (await db.execute(
        select(
            FilingDocument.filing_id,
            func.count(FilingDocument.id),
            func.sum(func.cast(FilingDocument.status == DocumentStatus.APPROVED, func.INTEGER)),
        )
        .where(FilingDocument.filing_id.in_(filing_ids))
        .group_by(FilingDocument.filing_id)
    )).all()
    # SQLAlchemy note: SUM over boolean cast — if the above syntax fails in your PG dialect, use a portable version:
    #   select(FilingDocument.filing_id, FilingDocument.status, func.count()).group_by(...)
    # and post-aggregate in Python. Both approaches acceptable — pick whichever compiles.
    doc_stats: dict[UUID, tuple[int, int]] = {fid: (int(total or 0), int(approved or 0)) for fid, total, approved in doc_rows}

    # Client names
    client_ids = list({f.client_id for f in filings})
    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(client_ids))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = last_change.get(f.id) or f.initiated_at
        if anchor is None or anchor >= cutoff:
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
```

Register:
```python
_ROUTES[ReminderType.FILING_STAGNANT_PRE_FILING] = _eval_filing_stagnant_pre_filing
```

────────────────────────────────────────────────────────
STEP 3 — Verify
────────────────────────────────────────────────────────

1. Confirm 4 rows in `reminder_configs`.
2. Enable and pick a filing in DOCUMENT_UPLOAD/PROCESSING/COMPUTATION with all docs APPROVED and a `filing_state_history` entry older than threshold_days.
3. `POST /api/v1/reminders/run-now` → exec + manager notifications created.
4. Reset config.
````

---

# Prompt 5 — Reminder 5: `INVOICE_PENDING_POST_FILING`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–4 complete.

CONTEXT: Add Reminder 5 — nudge Elevated Managers when a filing in `FILING` state has no `INVOICE` completed doc PARTNER_APPROVED for N days.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_completed_doc.py`, `backend/app/models/filing_state_history.py`
3. `backend/app/enums.py` — `FilingStatus`, `CompletedDocType`, `CompletedDocStatus`

RECIPIENT: Elevated Managers. Channels: in-app + email.

────────────────────────────────────────────────────────
STEP 1 — Enum + labels/messages
────────────────────────────────────────────────────────

Append to `ReminderType`:
```python
    INVOICE_PENDING_POST_FILING = "INVOICE_PENDING_POST_FILING"
```

Label:
```python
    ReminderType.INVOICE_PENDING_POST_FILING: "Invoice upload pending",
```

Message:
```python
    ReminderType.INVOICE_PENDING_POST_FILING: (
        "Filing for {client_name} (FY {fy}) entered FILING {days} day(s) ago but the invoice "
        "hasn't been uploaded and Partner-approved yet."
    ),
```

────────────────────────────────────────────────────────
STEP 2 — Evaluator
────────────────────────────────────────────────────────

```python
async def _eval_invoice_pending_post_filing(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 5 — filing in FILING for > threshold days, INVOICE not PARTNER_APPROVED."""
    from datetime import datetime, timezone, timedelta
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

    # Timestamp of entry into FILING per filing
    entry_rows = (await db.execute(
        select(FilingStateHistory.filing_id, func.max(FilingStateHistory.changed_at))
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.FILING,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at = {fid: ts for fid, ts in entry_rows}

    # Existing PARTNER-APPROVED invoices
    approved_invoice_rows = (await db.execute(
        select(FilingCompletedDoc.filing_id)
        .where(
            FilingCompletedDoc.filing_id.in_(filing_ids),
            FilingCompletedDoc.doc_type == CompletedDocType.INVOICE,
            FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
        )
    )).all()
    have_invoice = {row[0] for row in approved_invoice_rows}

    recipients = await _elevated_manager_ids(db)
    if not recipients:
        return (0, 0)

    # Client names
    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id in have_invoice:
            continue
        anchor = entered_at.get(f.id)
        if anchor is None or anchor >= cutoff:
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
```

Register in `_ROUTES`.

────────────────────────────────────────────────────────
STEP 3 — Verify
────────────────────────────────────────────────────────

- 5 rows in `reminder_configs`.
- Enable, prep a filing in FILING state that entered FILING > threshold_days ago and has no PARTNER_APPROVED invoice.
- `POST /run-now` → elevated managers notified. Reset after test.
````

---

# Prompt 6 — Reminder 6: `CLIENT_DOCS_PENDING_UPLOAD`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–5 complete.

CONTEXT: Nudge the client when a filing is in DOCUMENT_UPLOAD for > N days AND ≥ 1 `FilingDocument` is still PENDING_UPLOAD or REJECTED.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_document.py`, `backend/app/models/filing_state_history.py`
3. `backend/app/enums.py` — `FilingStatus`, `DocumentStatus`

RECIPIENT: client. Channels: in-app + email + WhatsApp.

────────────────────────────────────────────────────────
STEP 1 — Enum + labels
────────────────────────────────────────────────────────

```python
    CLIENT_DOCS_PENDING_UPLOAD = "CLIENT_DOCS_PENDING_UPLOAD"
```

```python
    ReminderType.CLIENT_DOCS_PENDING_UPLOAD: "Documents pending upload",
```

```python
    ReminderType.CLIENT_DOCS_PENDING_UPLOAD: (
        "Hi {client_name}, your filing for FY {fy} is waiting on document uploads for "
        "{days} day(s). Please complete the checklist to move forward."
    ),
```

────────────────────────────────────────────────────────
STEP 2 — Evaluator
────────────────────────────────────────────────────────

```python
async def _eval_client_docs_pending_upload(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 6 — DOCUMENT_UPLOAD state for > threshold days with pending/rejected docs."""
    from datetime import datetime, timezone, timedelta
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

    # Latest entry-into-DOCUMENT_UPLOAD per filing
    entry_rows = (await db.execute(
        select(FilingStateHistory.filing_id, func.max(FilingStateHistory.changed_at))
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.DOCUMENT_UPLOAD,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at = {fid: ts for fid, ts in entry_rows}

    # Filings that have at least one pending/rejected doc
    pending_rows = (await db.execute(
        select(FilingDocument.filing_id).distinct()
        .where(
            FilingDocument.filing_id.in_(filing_ids),
            FilingDocument.status.in_([DocumentStatus.PENDING_UPLOAD, DocumentStatus.REJECTED]),
        )
    )).all()
    has_pending = {row[0] for row in pending_rows}

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        if f.id not in has_pending:
            continue
        anchor = entered_at.get(f.id) or f.initiated_at
        if anchor is None or anchor >= cutoff:
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
```

Register in `_ROUTES`.

VERIFY: 6 rows; enable; prep a filing in DOCUMENT_UPLOAD with a pending doc; run-now → client notification created; reset.
````

---

# Prompt 7 — Reminder 7: `TEXT_FIELDS_PENDING_FILL`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–6 complete.

CONTEXT: Nudge the client when their filing has PENDING `FilingTextField` placeholders older than N days and the filing is not terminal.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_text_field.py`
3. `backend/app/enums.py` — `FilingStatus`, `TextFieldStatus`

RECIPIENT: client. Channels: in-app + email + WhatsApp.

STEP 1 — Enum + labels:

```python
    TEXT_FIELDS_PENDING_FILL = "TEXT_FIELDS_PENDING_FILL"
```

```python
    ReminderType.TEXT_FIELDS_PENDING_FILL: "Information fields pending",
```

```python
    ReminderType.TEXT_FIELDS_PENDING_FILL: (
        "Hi {client_name}, we need some information from you for your FY {fy} filing. "
        "There are pending fields waiting for {days} day(s)."
    ),
```

STEP 2 — Evaluator:

```python
async def _eval_text_fields_pending_fill(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 7 — filing not terminal, oldest PENDING FilingTextField.created_at > threshold days."""
    from datetime import datetime, timezone, timedelta
    from app.enums import FilingStatus, TextFieldStatus
    from app.models.filing import ITRFiling
    from app.models.filing_text_field import FilingTextField

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Oldest PENDING text field per filing (must be older than cutoff)
    pending_rows = (await db.execute(
        select(FilingTextField.filing_id, func.min(FilingTextField.created_at))
        .where(FilingTextField.status == TextFieldStatus.PENDING)
        .group_by(FilingTextField.filing_id)
    )).all()
    oldest = {fid: ts for fid, ts in pending_rows if ts and ts < cutoff}
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

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

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
```

Register in `_ROUTES`.

VERIFY: 7 rows; enable; prep a filing with a PENDING `filing_text_fields` row older than threshold_days; run-now; reset.
````

---

# Prompt 8 — Reminder 8: `COMPUTATION_AWAITING_MANAGER_APPROVAL`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–7 complete.

CONTEXT: Nudge the assigned Manager when a computation is UPLOADED and older than N days.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_computation.py`, `backend/app/models/executive_assignment.py`, `backend/app/models/manager_executive_assignment.py`
3. `backend/app/enums.py::ComputationStatus`

RECIPIENT: assigned Manager (skip if none). Channels: in-app + email.

STEP 1 — Enum + labels:

```python
    COMPUTATION_AWAITING_MANAGER_APPROVAL = "COMPUTATION_AWAITING_MANAGER_APPROVAL"
```

```python
    ReminderType.COMPUTATION_AWAITING_MANAGER_APPROVAL: "Computation awaiting Manager approval",
```

```python
    ReminderType.COMPUTATION_AWAITING_MANAGER_APPROVAL: (
        "Computation for {client_name} (FY {fy}) was uploaded {days} day(s) ago and is waiting "
        "for your review."
    ),
```

STEP 2 — Evaluator:

```python
async def _eval_computation_awaiting_manager_approval(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 8 — FilingComputation.status=UPLOADED, uploaded_at older than threshold."""
    from datetime import datetime, timezone, timedelta
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    rows = (await db.execute(
        select(FilingComputation.filing_id, func.min(FilingComputation.uploaded_at))
        .where(
            FilingComputation.status == ComputationStatus.UPLOADED,
            FilingComputation.uploaded_at < cutoff,
        )
        .group_by(FilingComputation.filing_id)
    )).all()
    if not rows:
        return (0, 0)

    filing_map = {fid: ts for fid, ts in rows}
    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(list(filing_map.keys())))
    )).scalars().all())

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        _exec_id, mgr_id = await _exec_and_manager_for_client(db, f.client_id)
        if mgr_id is None:
            continue
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(filing_map[f.id]),
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
```

Register.

VERIFY: 8 rows; enable; prep an UPLOADED computation older than threshold on a client with a manager; run-now; reset.
````

---

# Prompt 9 — Reminder 9: `COMPUTATION_AWAITING_PARTNER_APPROVAL`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–8 complete.

CONTEXT: Nudge Partners when a MANAGER_APPROVED computation is awaiting partner approval for > N days.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing_computation.py`

RECIPIENT: all active Partners. Channels: in-app + email.

STEP 1 — Enum + labels:

```python
    COMPUTATION_AWAITING_PARTNER_APPROVAL = "COMPUTATION_AWAITING_PARTNER_APPROVAL"
```

```python
    ReminderType.COMPUTATION_AWAITING_PARTNER_APPROVAL: "Computation awaiting Partner approval",
```

```python
    ReminderType.COMPUTATION_AWAITING_PARTNER_APPROVAL: (
        "Computation for {client_name} (FY {fy}) was Manager-approved {days} day(s) ago and is "
        "awaiting Partner sign-off."
    ),
```

STEP 2 — Evaluator:

```python
async def _eval_computation_awaiting_partner_approval(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 9 — status=MANAGER_APPROVED, manager_approved_at older than threshold."""
    from datetime import datetime, timezone, timedelta
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    rows = (await db.execute(
        select(FilingComputation.filing_id, func.min(FilingComputation.manager_approved_at))
        .where(
            FilingComputation.status == ComputationStatus.MANAGER_APPROVED,
            FilingComputation.manager_approved_at.isnot(None),
            FilingComputation.manager_approved_at < cutoff,
        )
        .group_by(FilingComputation.filing_id)
    )).all()
    if not rows:
        return (0, 0)

    filing_map = {fid: ts for fid, ts in rows}
    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(list(filing_map.keys())))
    )).scalars().all())

    partner_ids = await _partner_ids(db)
    if not partner_ids:
        return (0, 0)

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        ctx = {
            "client_name": names.get(f.client_id, ""),
            "fy": f.financial_year,
            "days": _days_since(filing_map[f.id]),
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
```

Register.

VERIFY: 9 rows; enable; prep a MANAGER_APPROVED computation older than threshold; run-now; reset.
````

---

# Prompt 10 — Reminder 10: `COMPUTATION_AWAITING_CLIENT_APPROVAL`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–9 complete.

CONTEXT: Nudge the client when the latest computation on a filing is PARTNER_APPROVED (not yet CLIENT_APPROVED / REJECTED / SUPERSEDED) for > N days.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing_computation.py`
3. `backend/app/enums.py::ComputationStatus`

RECIPIENT: client. Channels: in-app + email + WhatsApp.

STEP 1 — Enum + labels:

```python
    COMPUTATION_AWAITING_CLIENT_APPROVAL = "COMPUTATION_AWAITING_CLIENT_APPROVAL"
```

```python
    ReminderType.COMPUTATION_AWAITING_CLIENT_APPROVAL: "Computation awaiting your approval",
```

```python
    ReminderType.COMPUTATION_AWAITING_CLIENT_APPROVAL: (
        "Hi {client_name}, your ITR computation for FY {fy} was Partner-approved {days} day(s) "
        "ago and is waiting for your review."
    ),
```

STEP 2 — Evaluator:

Use the "latest computation per filing" pattern via a windowed / correlated subquery, or a simpler approach: pick all PARTNER_APPROVED computations whose `partner_approved_at < cutoff`, then filter out any filing that has a NEWER computation (by `uploaded_at`) whose status is in {CLIENT_APPROVED, REJECTED, SUPERSEDED, MANAGER_REJECTED}.

```python
async def _eval_computation_awaiting_client_approval(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 10 — latest computation is PARTNER_APPROVED, partner_approved_at > threshold days."""
    from datetime import datetime, timezone, timedelta
    from app.enums import ComputationStatus
    from app.models.filing import ITRFiling
    from app.models.filing_computation import FilingComputation

    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.threshold_days)

    # Candidate rows: PARTNER_APPROVED, older than cutoff
    pa_rows = list((await db.execute(
        select(FilingComputation)
        .where(
            FilingComputation.status == ComputationStatus.PARTNER_APPROVED,
            FilingComputation.partner_approved_at.isnot(None),
            FilingComputation.partner_approved_at < cutoff,
        )
    )).scalars().all())
    if not pa_rows:
        return (0, 0)

    filing_ids = list({c.filing_id for c in pa_rows})

    # For each filing, fetch newest computation.uploaded_at + its status
    latest_rows = (await db.execute(
        select(FilingComputation.filing_id, FilingComputation.status, FilingComputation.uploaded_at)
        .where(FilingComputation.filing_id.in_(filing_ids))
        .order_by(FilingComputation.filing_id, FilingComputation.uploaded_at.desc())
    )).all()
    latest_by_filing: dict[UUID, tuple] = {}
    for fid, status, uploaded in latest_rows:
        if fid not in latest_by_filing:
            latest_by_filing[fid] = (status, uploaded)

    # Keep only filings whose latest status is PARTNER_APPROVED
    target_filing_ids = [fid for fid, (status, _u) in latest_by_filing.items() if status == ComputationStatus.PARTNER_APPROVED]
    if not target_filing_ids:
        return (0, 0)

    filings = list((await db.execute(
        select(ITRFiling).where(ITRFiling.id.in_(target_filing_ids))
    )).scalars().all())

    # Anchor: use the PARTNER_APPROVED comp's partner_approved_at from pa_rows
    anchor_by_filing: dict[UUID, datetime] = {}
    for c in pa_rows:
        if c.filing_id in target_filing_ids:
            if c.filing_id not in anchor_by_filing or c.partner_approved_at > anchor_by_filing[c.filing_id]:
                anchor_by_filing[c.filing_id] = c.partner_approved_at

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = anchor_by_filing.get(f.id)
        if anchor is None:
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
```

Register.

VERIFY: 10 rows; enable; prep a filing whose latest computation is PARTNER_APPROVED older than threshold; run-now; reset.
````

---

# Prompt 11 — Reminder 11: `COMPLETED_DOCS_PENDING`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–10 complete.

CONTEXT: Nudge assigned Exec + Manager when a filing is in FILING for > N days and at least one required completed doc (out of {ITR_ACKNOWLEDGEMENT, INVOICE, ITR_JSON, ITR_FORM, TAX_PAID_COMPUTATION}) is missing a PARTNER_APPROVED row.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing_completed_doc.py`, `backend/app/models/filing_state_history.py`, `backend/app/models/filing.py`
3. `backend/app/enums.py` — `CompletedDocType`, `CompletedDocStatus`, `FilingStatus`
4. `backend/app/services/action_item_service.py` — `_REQUIRED_COMPLETED_DOCS` set (mirror the same 5)

RECIPIENT: Exec + Manager. Channels: in-app + email.

STEP 1 — Enum + labels:

```python
    COMPLETED_DOCS_PENDING = "COMPLETED_DOCS_PENDING"
```

```python
    ReminderType.COMPLETED_DOCS_PENDING: "Completed documents pending",
```

```python
    ReminderType.COMPLETED_DOCS_PENDING: (
        "Filing for {client_name} (FY {fy}) has been in FILING for {days} day(s). "
        "Pending completed docs: {missing_docs}."
    ),
```

STEP 2 — Evaluator:

```python
_REQUIRED_COMPLETED_DOCS_FOR_REMINDER = {"ITR_ACKNOWLEDGEMENT", "INVOICE", "ITR_JSON", "ITR_FORM", "TAX_PAID_COMPUTATION"}

async def _eval_completed_docs_pending(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 11 — filing in FILING > threshold days with any required completed doc missing Partner approval."""
    from datetime import datetime, timezone, timedelta
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

    entry_rows = (await db.execute(
        select(FilingStateHistory.filing_id, func.max(FilingStateHistory.changed_at))
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.FILING,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at = {fid: ts for fid, ts in entry_rows}

    # Per-filing set of PARTNER_APPROVED completed doc types
    approved_rows = (await db.execute(
        select(FilingCompletedDoc.filing_id, FilingCompletedDoc.doc_type)
        .where(
            FilingCompletedDoc.filing_id.in_(filing_ids),
            FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
        )
    )).all()
    approved_by_filing: dict[UUID, set[str]] = {}
    for fid, dt in approved_rows:
        approved_by_filing.setdefault(fid, set()).add(dt.value if hasattr(dt, "value") else str(dt))

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = entered_at.get(f.id)
        if anchor is None or anchor >= cutoff:
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
```

Register.

VERIFY: 11 rows; enable; prep a filing in FILING > threshold days with missing completed docs; run-now; reset.
````

---

# Prompt 12 — Reminder 12: `PAYMENT_NOT_MARKED_RECEIVED`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–11 complete.

CONTEXT: Nudge Elevated Managers when a filing is in PAYMENT for > N days without `payment_received_at` set.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_state_history.py`
3. `backend/app/enums.py::FilingStatus`

RECIPIENT: Elevated Managers. Channels: in-app + email.

STEP 1 — Enum + labels:

```python
    PAYMENT_NOT_MARKED_RECEIVED = "PAYMENT_NOT_MARKED_RECEIVED"
```

```python
    ReminderType.PAYMENT_NOT_MARKED_RECEIVED: "Payment not marked received",
```

```python
    ReminderType.PAYMENT_NOT_MARKED_RECEIVED: (
        "Filing for {client_name} (FY {fy}) has been in PAYMENT for {days} day(s) but the "
        "payment received flag is still not set."
    ),
```

STEP 2 — Evaluator:

```python
async def _eval_payment_not_marked_received(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 12 — filing in PAYMENT > threshold days, payment_received_at is NULL."""
    from datetime import datetime, timezone, timedelta
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
    entry_rows = (await db.execute(
        select(FilingStateHistory.filing_id, func.max(FilingStateHistory.changed_at))
        .where(
            FilingStateHistory.filing_id.in_(filing_ids),
            FilingStateHistory.to_status == FilingStatus.PAYMENT,
        )
        .group_by(FilingStateHistory.filing_id)
    )).all()
    entered_at = {fid: ts for fid, ts in entry_rows}

    recipients = await _elevated_manager_ids(db)
    if not recipients:
        return (0, 0)

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

    dispatched = 0
    skipped = 0
    for f in filings:
        anchor = entered_at.get(f.id)
        if anchor is None or anchor >= cutoff:
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
```

Register.

VERIFY: 12 rows; enable; prep a filing in PAYMENT > threshold days with `payment_received_at IS NULL`; run-now; reset.
````

---

# Prompt 13 — Reminder 13: `FEEDBACK_NOT_SUBMITTED`

````text
You are working on the ITR-Manager FastAPI backend at `backend/app/`. Prompts 1–12 complete. This completes the reminders subsystem.

CONTEXT: Nudge the client when a filing is COMPLETED > N days ago and no `FilingFeedback` exists.

READ FIRST:
1. `backend/app/services/reminder_service.py`
2. `backend/app/models/filing.py`, `backend/app/models/filing_feedback.py`
3. `backend/app/enums.py::FilingStatus`

RECIPIENT: client. Channels: in-app + email + WhatsApp.

STEP 1 — Enum + labels:

```python
    FEEDBACK_NOT_SUBMITTED = "FEEDBACK_NOT_SUBMITTED"
```

```python
    ReminderType.FEEDBACK_NOT_SUBMITTED: "We'd love your feedback",
```

```python
    ReminderType.FEEDBACK_NOT_SUBMITTED: (
        "Hi {client_name}, your FY {fy} filing was completed {days} day(s) ago. "
        "We'd appreciate a quick rating so we can serve you better next year."
    ),
```

STEP 2 — Evaluator:

```python
async def _eval_feedback_not_submitted(db: AsyncSession, cfg: ReminderConfig) -> tuple[int, int]:
    """Reminder 13 — filing COMPLETED > threshold days, no FilingFeedback row."""
    from datetime import datetime, timezone, timedelta
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
    fb_rows = (await db.execute(
        select(FilingFeedback.filing_id).where(FilingFeedback.filing_id.in_(filing_ids))
    )).all()
    have_fb = {row[0] for row in fb_rows}

    names = {uid: n for uid, n in (await db.execute(
        select(User.id, User.full_name).where(User.id.in_(list({f.client_id for f in filings})))
    )).all()}

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
```

Register.

────────────────────────────────────────────────────────
FINAL SMOKE TEST (after this prompt)
────────────────────────────────────────────────────────

1. `SELECT reminder_type, is_enabled FROM reminder_configs ORDER BY reminder_type;` → 13 rows, all `is_enabled = false`.
2. `SELECT unnest(enum_range(NULL::reminder_type));` → 13 values matching `ReminderType`.
3. `GET /api/v1/reminders/configs` → 13 items with a Partner JWT; 403 with any other role.
4. `POST /api/v1/reminders/run-now` → `dispatched={}, skipped={}` when all disabled.
5. Enable each type one by one via `PUT` with `threshold_days=0, repeat_interval_days=0, max_sends=1`, run `run-now`, verify the correct recipient(s) get a notification, then reset the config to defaults.
6. Confirm the background worker logs `Reminders worker started (interval=21600s).` on boot.

Reminders subsystem is complete.
````

---

## Post-implementation (out of scope)

- Frontend UI for `/reminders/configs` list, edit modal, pause/resume toggles, and dispatch-log viewer.
- Optional Prometheus counter `reminders_dispatched_total{reminder_type=...}` inside `dispatch_candidate` for Grafana.
- Consider partial indexes on `itr_filings(status)` and `users(account_status, activated_at)` if the worker query cost grows.
