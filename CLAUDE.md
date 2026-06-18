# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Backend (FastAPI) for an **ITR Filing Management Platform** — a CA practice manages the end-to-end Income Tax Return filing lifecycle for its clients. The frontend (Next.js) lives in a separate repo and ships as its own Docker image (`omicron9009/itr-frontend`); only `backend/`, `deploy/`, `scripts/`, and `data_feed/` are in this repo.

App display name is `AIकर` (see `APP_NAME`); the firm is "P G Joshi & Co LLP".

## Commands

All backend commands run from `backend/`.

```bash
# Run locally (expects Postgres + MinIO + Redis reachable per .env / config defaults)
uvicorn app.main:app --reload --port 8000

# Build & push the all-in-one image (Postgres + MinIO + API in one container)
docker build -t itr-platform:latest .
docker tag itr-platform:latest omicron9009/itr-platform:latest && docker push omicron9009/itr-platform:latest
```

Full multi-service stack (Traefik, Cloudflare tunnel, Prometheus/Grafana/Loki) is in `deploy/docker-compose.yml` — see `deploy/README.md`. WhatsApp/OpenWA gateway has its own compose at `deploy/openwa_docker_compose.yml`.

OpenAPI docs: `GET /api/v1/openapi.json`. Health/root: `GET /`. Prometheus metrics: `GET /metrics`.

**Tests:** `pytest` and `pytest-asyncio` are declared in `requirements.txt`, but there is currently **no test suite** in the repo. There is no linter config either.

Operational scripts (run against a live API, not the DB directly):
- `scripts/manage_whatsapp_partner.py` — configure/start the partner OpenWA session
- `scripts/delete_user.py`
- `data_feed/feed_users.py` — bulk-load users from `data_feed/Article Manager Mapping.xlsx`

## Architecture

Standard FastAPI layering, all under `backend/app/`:

- **`api/v1/*.py`** — routers, one file per domain (auth, clients, executives, managers, filings, documents, computations, onboarding, notifications, dashboard, audit, storage, email, tags, reports, action_items, feedback, internal_workings, text_fields, whatsapp). All aggregated in `api/v1/router.py` and mounted under `API_V1_PREFIX` (`/api/v1`).
- **`services/*.py`** — business logic. Endpoints stay thin; real work (state machine, audit, notifications, document/storage handling, reports, PDFs) lives here.
- **`models/*.py`** — SQLAlchemy 2.0 declarative models. **Every model must be imported in `models/__init__.py`** or `Base.metadata.create_all` won't see it.
- **`schemas/*.py`** — Pydantic v2 request/response models.
- **`core/`** — cross-cutting: `security.py` (JWT + role dependencies), `permissions.py` (scoped data access), `cache.py` (Redis/L1 cache), `exceptions.py`, `file_validation.py`.
- **`config.py`** — `pydantic-settings` `Settings` singleton (`settings`), reads env / `.env`. **All config flows through here**, including DB URL properties and per-namespace cache TTLs.

### Database lifecycle — NO Alembic migrations at runtime

This is the single most important convention. Despite `alembic` being installed, **schema changes are applied imperatively on startup**, not via migration files. `app/main.py`'s `lifespan` runs, in order:

1. `_ensure_database_exists` / `_ensure_openwa_database_exists` — create the Postgres DBs if missing
2. `_create_tables` — `Base.metadata.create_all` (under a `pg_advisory_xact_lock` so multiple uvicorn workers don't race)
3. `_sync_pg_enums` — `ALTER TYPE ... ADD VALUE` for any Python enum value missing from the PG enum (uses raw asyncpg because this can't run in a transaction)
4. `_sync_new_columns` — `ALTER TABLE ... ADD COLUMN` / add FKs / create newer tables for already-deployed DBs (raw asyncpg, idempotent)
5. `_migrate_renamed_enum_values` — data migrations for renamed enum values
6. `_cleanup_manager_tags`, `_ensure_perf_indexes`, then seed admin/dashboard users and init cache

**Consequence:** when you add a model field or a new enum value, `create_all` only helps fresh DBs. For existing deployments you **must also** add the column to the `columns_to_sync` list in `_sync_new_columns` (and/or the relevant enum to the `enum_map` in `_sync_pg_enums`). New tables that must appear on old volumes are hand-written as `CREATE TABLE IF NOT EXISTS` blocks in `_sync_new_columns`. Everything here is written to be idempotent and to fail-open (log a warning, never crash startup).

Performance indexes (including `pg_trgm` GIN indexes for name/email search) are created in `_ensure_perf_indexes` via `CREATE INDEX IF NOT EXISTS` — also in code, not Alembic.

### Auth & roles

JWT bearer auth (`core/security.py`), HS256, local password hashing with bcrypt. Roles (`UserRole`): `PARTNER`, `MANAGER`, `EXECUTIVE`, `CLIENT`, `DASHBOARD_USER`. `DASHBOARD_USER` tokens get a 30-day TTL (read-only TV/kiosk dashboards); everyone else gets `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (60).

Two layers of authorization, used together:
- **Role gates** — FastAPI dependencies in `security.py` (`get_current_partner`, `get_current_manager_or_partner`, etc.). `get_current_user` re-fetches the user from the DB on every request (deliberately not cached) so activation/deactivation is instant across workers.
- **Scoped data access** — `core/permissions.py` `enforce_client_access` / `enforce_filing_access`. Partner sees all; Manager sees clients assigned to their team; Executive sees only assigned clients; Client sees only their own data. Assignment tables: `manager_executive_assignments`, `manager_client_assignments`, `executive_client_assignments` (all soft-scoped by `is_active`).

### Filing state machine

`FilingStatus`: `INITIATED → DOCUMENT_UPLOAD → PROCESSING → COMPUTATION → FILING → PAYMENT → COMPLETED`, plus `HALTED`. Allowed transitions are declared in `VALID_FILING_TRANSITIONS` (`enums.py`) and enforced in `services/filing_service.py::transition_filing_status`, which also writes `filing_state_history` and an audit event. `COMPLETED` is terminal. Always go through this function — don't set `filing.status` directly.

Computations and completed docs have their own multi-step approval enums (`ComputationStatus`, `CompletedDocStatus`): uploaded → manager-approved → partner-approved → client-approved, with rejection paths. The approval/rejection columns were retrofitted via `_sync_new_columns`.

### Caching

`core/cache.py` is a two-tier cache (in-process `TTLCache` L1 + Redis L2) with **namespace versioning**: keys are `itr:<ns>:v<n>:<key>` and `bump_version(ns)` invalidates a whole namespace atomically without scanning. Use the `@cached(namespace, ttl)` decorator or `get_or_compute`. Namespaces are constants on the `NS` class; TTLs come from `settings` (kept deliberately short). The whole layer fails open — if Redis is down or `CACHE_ENABLED=false`, everything becomes a no-op and the DB is queried directly. **When a mutation changes cached data, call `bump_version(NS.X)` for the affected namespace.**

### Storage

Documents live in MinIO (`services/storage_service.py`); browsers get presigned URLs built against `MINIO_PUBLIC_ENDPOINT` (must be LAN/public-reachable, distinct from the internal `MINIO_ENDPOINT`). Bucket is auto-created on startup; SSE-S3 encryption is enabled in `start.sh` when `MINIO_KMS_SECRET_KEY` is set.

### Notifications, email, WhatsApp

`services/notification_service.py` writes in-app notifications and dispatches email (SMTP via `aiosmtplib`, config in DB `email_config`) and/or WhatsApp depending on `NotificationChannel`. WhatsApp goes through a self-hosted **OpenWA** gateway over HTTP (`services/whatsapp_service.py`); its admin API key is stored encrypted (Fernet, `WHATSAPP_ENCRYPTION_KEY`) and OpenWA uses its own `openwa` Postgres database on the same cluster. Notification rows carry `whatsapp_*` delivery-tracking columns.

### Observability

Structured JSON logging to stdout (`pythonjsonlogger`) for Loki; every request gets an `x-request-id`, 4xx logged as warning and 5xx as error. Prometheus metrics via `prometheus-fastapi-instrumentator` at `/metrics`. A global exception handler returns a structured 500 with the request id.

## Conventions to follow

- **Adding a model field:** edit the model, ensure the model is imported in `models/__init__.py`, AND add the column to `_sync_new_columns` in `main.py` (with FK handling if it references another table) so existing deployments migrate.
- **Adding an enum value:** add it to the Python enum in `enums.py` and make sure that enum is in the `enum_map` of `_sync_pg_enums`. Renaming an existing value also needs an entry in `_migrate_renamed_enum_values`.
- **DDL that can't run in a transaction** (`CREATE DATABASE`, `ALTER TYPE ... ADD VALUE`) must use raw `asyncpg`, not the SQLAlchemy async engine (which auto-begins a transaction). This is why those startup helpers connect with asyncpg directly.
- Endpoints thin, logic in services. Audit-worthy actions go through `services/audit_service.py` (`record_audit_event`); the `AuditEventType` enum is the catalog of what's tracked.
- The DB session dependency (`database.py::get_db`) commits on success and rolls back on exception per request — services generally don't commit themselves.
