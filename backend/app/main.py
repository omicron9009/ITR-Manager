"""ITR Filing Platform — FastAPI Application."""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pythonjsonlogger import jsonlogger

from app.api.v1.router import api_router
from app.config import settings
from app.core.cache import close_cache, init_cache
from app.core.security import get_current_user
import sys

from prometheus_fastapi_instrumentator import Instrumentator # type: ignore

# ── Structured JSON logging (Loki-friendly) ─────────────────────
# Emits one JSON line per record with stable field names so Loki can
# parse with `| json` and filter on `level`, `status_code`, `path`, etc.
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(
    jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
    )
)
# Wipe any handlers basicConfig may have installed, then attach JSON one.
_root_logger = logging.getLogger()
_root_logger.handlers.clear()
_root_logger.addHandler(_log_handler)
_root_logger.setLevel(logging.INFO)

# Application logger
logger = logging.getLogger("app")
logger.setLevel(logging.DEBUG)

# Quiet down noisy libraries (keep WARNING+)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # ── Startup ──
    await _ensure_database_exists()
    await _create_tables()
    await _ensure_perf_indexes()
    await _seed_admin_user()
    await _seed_dashboard_user()
    await init_cache()
    

    try:
        from app.services.storage_service import ensure_bucket_exists
        ensure_bucket_exists()
    except Exception:
        logger.warning("MinIO bucket initialization skipped — service may not be available")

    yield
    # ── Shutdown ──
    await close_cache()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="ITR Filing Management Platform API — manages end-to-end ITR filing lifecycle for CA practices.",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    lifespan=lifespan,
)


instrumentator = Instrumentator(
    should_group_status_codes=False,  # Exposes exact codes (200, 404, 500) instead of grouping by 2xx/5xx
    # should_ignore_untargeted_http_methods=True,
    should_instrument_requests_inprogress=True,
    inprogress_name="http_requests_inprogress",
    inprogress_labels=True,
)
instrumentator.instrument(app)
instrumentator.expose(app, endpoint="/metrics")

# Gzip large responses (dashboards, reports, lists)
if settings.GZIP_MIN_SIZE > 0:
    app.add_middleware(GZipMiddleware, minimum_size=settings.GZIP_MIN_SIZE)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request-scoped logging middleware ──────────────────────────
# Tags every request with a UUID, logs duration, and ERROR-logs any 5xx
# response so they're easily filterable in Loki via `level="ERROR"`.
@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # Re-raised; the global exception_handler below will log + format the response
        raise
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    log_extra = {
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": duration_ms,
        "client_ip": request.client.host if request.client else None,
    }
    if response.status_code >= 500:
        logger.error("http_5xx_response", extra=log_extra)
    elif response.status_code >= 400:
        logger.warning("http_4xx_response", extra=log_extra)
    response.headers["x-request-id"] = request_id
    return response


# ── Global exception handler ───────────────────────────────────
# Catches anything not handled by FastAPI's built-in HTTPException flow.
# Emits a structured ERROR log line (with traceback) for Loki.
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    logger.exception(
        "unhandled_exception",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": 500,
            "client_ip": request.client.host if request.client else None,
            "exception_type": type(exc).__name__,
        },
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
        headers={"x-request-id": request_id},
    )


# Include API router
app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": f"{settings.API_V1_PREFIX}/openapi.json",
    }

@app.get("/api/v1/my-tax-filings")
async def read_filings(current_user: str = Depends(get_current_user)):
    return {"message": f"Hello {current_user}, here are your tax documents."}

async def _ensure_database_exists():
    """Connect to the default 'postgres' DB and create the target database if it doesn't exist."""
    import asyncpg

    try:
        # Connect to the default maintenance database
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database="postgres",
        )


        # Check if our target database exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.POSTGRES_DB,
        )

        if not exists:
            # CREATE DATABASE cannot run inside a transaction
            await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
            logger.info(f"Database '{settings.POSTGRES_DB}' created successfully.")
        else:
            logger.info(f"Database '{settings.POSTGRES_DB}' already exists.")

        await conn.close()
    except Exception as e:
        logger.warning(f"Database auto-creation skipped: {e}")
        logger.warning("Ensure the database exists manually if this is first run.")


async def _create_tables():
    """Create all tables from SQLAlchemy models if they don't exist."""
    from sqlalchemy import text

    from app.database import Base, engine

    # Import all models so metadata is populated
    import app.models  # noqa: F401

    try:
        async with engine.begin() as conn:
            # Advisory lock prevents race between multiple uvicorn workers
            await conn.execute(text("SELECT pg_advisory_xact_lock(1)"))
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured.")

        # Ensure PostgreSQL enums have all values defined in Python enums
        await _sync_pg_enums()

        # Ensure new columns exist on existing tables
        await _sync_new_columns()

        # Migrate renamed enum values in existing data
        await _migrate_renamed_enum_values()

        # Remove deprecated MANAGER tags from the database
        await _cleanup_manager_tags()
    except Exception as e:
        logger.warning(f"Table creation skipped: {e}")


async def _ensure_perf_indexes():
    """Create performance indexes if they don't already exist.

    All statements use ``CREATE INDEX IF NOT EXISTS`` so this is idempotent
    and safe to run on every startup. We do this in code (not Alembic) per
    project convention.
    """
    import asyncpg

    statements = [
        # itr_filings hot filters
        "CREATE INDEX IF NOT EXISTS ix_filings_client_fy ON itr_filings (client_id, financial_year)",
        "CREATE INDEX IF NOT EXISTS ix_filings_executive_status ON itr_filings (assigned_executive_id, status)",
        "CREATE INDEX IF NOT EXISTS ix_filings_status ON itr_filings (status)",
        # latest computation per filing  ─ the dashboard summary hot path
        "CREATE INDEX IF NOT EXISTS ix_filing_comp_filing_version ON filing_computations (filing_id, version DESC)",
        # completed docs lookup
        "CREATE INDEX IF NOT EXISTS ix_completed_doc_filing_type ON filing_completed_docs (filing_id, doc_type)",
        # assignment scopes
        "CREATE INDEX IF NOT EXISTS ix_mgr_exec_active ON manager_executive_assignments (manager_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_mgr_client_active ON manager_client_assignments (manager_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_exec_client_exec_active ON executive_client_assignments (executive_id, is_active)",
        "CREATE INDEX IF NOT EXISTS ix_exec_client_client_active ON executive_client_assignments (client_id, is_active)",
        # users
        "CREATE INDEX IF NOT EXISTS ix_users_role_active ON users (role, is_active)",
        # users — client list hot path (role + status filter + created_at sort)
        "CREATE INDEX IF NOT EXISTS ix_users_role_status_created ON users (role, account_status, created_at DESC)",
        # notifications
        "CREATE INDEX IF NOT EXISTS ix_notif_user_read_created ON notifications (user_id, is_read, created_at DESC)",
        # itr_filings — client list active filings batch fetch
        "CREATE INDEX IF NOT EXISTS ix_filings_client_status ON itr_filings (client_id, status)",
    ]

    # Trigram indexes for ILIKE '%...%' search on client name/email
    # Requires pg_trgm extension — attempted separately so failure doesn't block other indexes
    trgm_statements = [
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "CREATE INDEX IF NOT EXISTS ix_users_full_name_trgm ON users USING gin (full_name gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS ix_users_email_trgm ON users USING gin (email gin_trgm_ops)",
    ]

    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
        )
        try:
            for stmt in statements:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    # Table may not exist on a brand-new DB; ignore.
                    logger.debug(f"Index ensure skipped ({stmt[:60]}...): {e}")
            # Trigram indexes — non-fatal if pg_trgm is unavailable
            for stmt in trgm_statements:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    logger.debug(f"Trigram index skipped ({stmt[:60]}...): {e}")
        finally:
            await conn.close()
        logger.info("Performance indexes ensured.")
    except Exception as e:
        logger.warning(f"Index ensure failed: {e}")


async def _sync_pg_enums():
    """Ensure PostgreSQL enum types have all values defined in Python enums.

    On old volumes, newly added Python enum values (e.g. ITR_JSON) may not
    exist in the PostgreSQL enum type yet. This adds any missing values
    automatically on startup so no manual migration is needed.

    Uses raw asyncpg (not SQLAlchemy) because ALTER TYPE ... ADD VALUE
    cannot run inside a transaction block, and SQLAlchemy's async engine
    auto-begins transactions that cannot be fully escaped.
    """
    import asyncpg

    from app.enums import (
        AccountStatus, FilingStatus, DocumentStatus, ComputationStatus,
        CompletedDocType, CompletedDocStatus, FormFieldType, AuditEventType, NotificationChannel, UserRole,
        TagType, ReferralSource, IncomeHeadCategory, DocSubCategory, TextFieldStatus,
    )
    enum_map = {
        "user_role": UserRole,
        "account_status": AccountStatus,
        "filing_status": FilingStatus,
        "document_status": DocumentStatus,
        "computation_status": ComputationStatus,
        "completed_doc_type": CompletedDocType,
        "completed_doc_status": CompletedDocStatus,
        "form_field_type": FormFieldType,
        "audit_event_type": AuditEventType,
        "notification_channel": NotificationChannel,
        "tag_type": TagType,
        "referral_source": ReferralSource,
        "income_head_category": IncomeHeadCategory,
        "doc_sub_category": DocSubCategory,
        "text_field_status": TextFieldStatus,
    }

    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
        )

        try:
            for pg_type_name, py_enum in enum_map.items():
                # Get existing values from PostgreSQL
                rows = await conn.fetch(
                    "SELECT enumlabel FROM pg_enum WHERE enumtypid = "
                    "(SELECT oid FROM pg_type WHERE typname = $1)",
                    pg_type_name,
                )
                existing_values = {row["enumlabel"] for row in rows}

                if not existing_values:
                    # Type doesn't exist yet (fresh DB) — create_all handles it
                    continue

                # Add any missing values (runs outside a transaction by default)
                for member in py_enum:
                    if member.value not in existing_values:
                        # DDL cannot use $1 params; value is from our own enum (safe)
                        await conn.execute(
                            f"ALTER TYPE {pg_type_name} ADD VALUE IF NOT EXISTS '{member.value}'"
                        )
                        logger.info(f"Added '{member.value}' to PostgreSQL enum '{pg_type_name}'")
        finally:
            await conn.close()

        logger.info("PostgreSQL enum sync complete.")
    except Exception as e:
        logger.warning(f"Enum sync failed: {e}")


async def _sync_new_columns():
    """Add new columns to existing tables if they don't exist.

    Uses raw asyncpg because SQLAlchemy's create_all does not add columns
    to already-existing tables. This runs on every startup and is idempotent.
    """
    import asyncpg

    # Define columns to ensure exist: (table, column, SQL type, default)
    columns_to_sync = [
        # phone_number on users table
        ("users", "phone_number", "VARCHAR(20)", None),
        # Computation rejection fields on filing_computations table
        ("filing_computations", "rejected_by", "UUID", None),
        ("filing_computations", "rejected_at", "TIMESTAMPTZ", None),
        ("filing_computations", "rejection_reason", "TEXT", None),
        # Manager approval fields on filing_computations table
        ("filing_computations", "manager_approved_by", "UUID", None),
        ("filing_computations", "manager_approved_at", "TIMESTAMPTZ", None),
        ("filing_computations", "manager_rejected_by", "UUID", None),
        ("filing_computations", "manager_rejected_at", "TIMESTAMPTZ", None),
        ("filing_computations", "manager_rejection_reason", "TEXT", None),
        # Partner approval fields on filing_computations table
        ("filing_computations", "partner_approved_by", "UUID", None),
        ("filing_computations", "partner_approved_at", "TIMESTAMPTZ", None),
        # Recovery codes flag on users table
        ("users", "recovery_codes_issued", "BOOLEAN NOT NULL", "'false'"),
        # Tax payment confirmation on itr_filings table
        ("itr_filings", "is_tax_paid", "BOOLEAN NOT NULL", "'false'"),
        ("itr_filings", "tax_paid_at", "TIMESTAMPTZ", None),
        # Declaration consent timestamp on client_profiles table
        ("client_profiles", "declaration_accepted_at", "TIMESTAMPTZ", None),
        # Professional fee on client_profiles
        ("client_profiles", "professional_fee", "NUMERIC(10,2)", None),
        # Engagement letter fields on itr_filings
        ("itr_filings", "professional_fee", "NUMERIC(10,2)", None),
        ("itr_filings", "engagement_accepted_at", "TIMESTAMPTZ", None),
        ("itr_filings", "engagement_letter_key", "TEXT", None),
        # Fee change proposal fields on itr_filings
        ("itr_filings", "proposed_fee", "NUMERIC(10,2)", None),
        ("itr_filings", "fee_proposed_at", "TIMESTAMPTZ", None),
        ("itr_filings", "fee_proposed_by", "UUID", None),
        # Executive + Manager snapshot on viewer_completed_queue
        ("viewer_completed_queue", "executive_id", "UUID", None),
        ("viewer_completed_queue", "executive_name", "VARCHAR(255)", None),
        ("viewer_completed_queue", "manager_id", "UUID", None),
        ("viewer_completed_queue", "manager_name", "VARCHAR(255)", None),
        # No fees applicable flag
        ("client_profiles", "no_fees_applicable", "BOOLEAN NOT NULL", "'false'"),
        ("itr_filings", "no_fees_applicable", "BOOLEAN NOT NULL", "'false'"),
        # Referral source
        ("client_profiles", "referral_source", "VARCHAR(50)", None),
        ("client_profiles", "referral_source_other", "TEXT", None),
        # Partner tag
        ("client_profiles", "partner_tag_id", "UUID", None),
        # Any Other income head description
        ("client_income_heads", "any_other_text", "VARCHAR(255)", None),
        # Internal working doc versioning (replace-without-delete)
        ("internal_working_docs", "replaces_id", "UUID", None),
        ("internal_working_docs", "superseded_at", "TIMESTAMPTZ", None),
    ]

    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
        )

        try:
            for table, column, col_type, default in columns_to_sync:
                # Check if column exists
                exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = $1 AND column_name = $2",
                    table, column,
                )
                if not exists:
                    default_clause = f" DEFAULT {default}" if default else ""
                    await conn.execute(
                        f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_type}{default_clause}'
                    )
                    logger.info(f"Added column '{column}' ({col_type}) to table '{table}'")

            # Add FK constraints for user reference columns if not present
            fk_constraints = [
                ("fk_filing_computations_rejected_by", "rejected_by"),
                ("fk_filing_computations_manager_approved_by", "manager_approved_by"),
                ("fk_filing_computations_manager_rejected_by", "manager_rejected_by"),
                ("fk_filing_computations_partner_approved_by", "partner_approved_by"),
            ]
            for constraint_name, col_name in fk_constraints:
                fk_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.table_constraints "
                    "WHERE constraint_name = $1 AND table_name = 'filing_computations'",
                    constraint_name,
                )
                if not fk_exists:
                    col_exists = await conn.fetchval(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'filing_computations' AND column_name = $1",
                        col_name,
                    )
                    if col_exists:
                        await conn.execute(
                            f'ALTER TABLE "filing_computations" '
                            f'ADD CONSTRAINT "{constraint_name}" '
                            f'FOREIGN KEY ("{col_name}") REFERENCES "users"("id") ON DELETE SET NULL'
                        )
                        logger.info(f"Added FK constraint '{constraint_name}'")

            # Add FK constraint for partner_tag_id on client_profiles
            fk_partner_tag = "fk_client_profiles_partner_tag_id"
            fk_pt_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name = $1 AND table_name = 'client_profiles'",
                fk_partner_tag,
            )
            if not fk_pt_exists:
                col_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'client_profiles' AND column_name = 'partner_tag_id'",
                )
                if col_exists:
                    await conn.execute(
                        f'ALTER TABLE "client_profiles" '
                        f'ADD CONSTRAINT "{fk_partner_tag}" '
                        f'FOREIGN KEY ("partner_tag_id") REFERENCES "tags"("id") ON DELETE SET NULL'
                    )
                    logger.info(f"Added FK constraint '{fk_partner_tag}'")

            # Drop unique constraint on filing_documents to allow multiple files per type
            uq_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name = 'uq_filing_doc_type' AND table_name = 'filing_documents'"
            )
            if uq_exists:
                await conn.execute(
                    'ALTER TABLE "filing_documents" DROP CONSTRAINT "uq_filing_doc_type"'
                )
                logger.info("Dropped unique constraint 'uq_filing_doc_type' (multi-doc support)")

            # Create non-unique index if not exists
            ix_exists = await conn.fetchval(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'ix_filing_doc_type'"
            )
            if not ix_exists:
                await conn.execute(
                    'CREATE INDEX "ix_filing_doc_type" ON "filing_documents" ("filing_id", "document_type_id")'
                )
                logger.info("Created index 'ix_filing_doc_type'")

            # Ensure filing_feedback table exists (for existing deployments)
            feedback_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'filing_feedback'"
            )
            if not feedback_table_exists:
                await conn.execute("""
                    CREATE TABLE filing_feedback (
                        id UUID PRIMARY KEY,
                        filing_id UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
                        client_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        rating INTEGER NOT NULL CHECK (rating >= 1 AND rating <= 5),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT uq_filing_feedback_filing_id UNIQUE (filing_id)
                    )
                """)
                logger.info("Created table 'filing_feedback'")

            # Ensure filing_other_docs table exists (for existing deployments)
            other_docs_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'filing_other_docs'"
            )
            if not other_docs_table_exists:
                await conn.execute("""
                    CREATE TABLE filing_other_docs (
                        id UUID PRIMARY KEY,
                        filing_id UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
                        file_id UUID NOT NULL REFERENCES stored_files(id) ON DELETE RESTRICT,
                        label VARCHAR(255),
                        uploaded_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                await conn.execute(
                    'CREATE INDEX "ix_filing_other_docs_filing_id" ON "filing_other_docs" ("filing_id")'
                )
                logger.info("Created table 'filing_other_docs'")

            # Ensure internal_working_docs table exists (for existing deployments)
            iw_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'internal_working_docs'"
            )
            if not iw_table_exists:
                await conn.execute("""
                    CREATE TABLE internal_working_docs (
                        id UUID PRIMARY KEY,
                        filing_id UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
                        file_id UUID NOT NULL REFERENCES stored_files(id) ON DELETE RESTRICT,
                        label VARCHAR(255),
                        uploaded_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        replaces_id UUID REFERENCES internal_working_docs(id) ON DELETE SET NULL,
                        superseded_at TIMESTAMPTZ
                    )
                """)
                await conn.execute(
                    'CREATE INDEX "ix_internal_working_docs_filing_id" ON "internal_working_docs" ("filing_id")'
                )
                logger.info("Created table 'internal_working_docs'")

            # Ensure self-referencing FK on internal_working_docs.replaces_id (existing deployments)
            iw_fk_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name = 'fk_internal_working_docs_replaces_id' "
                "AND table_name = 'internal_working_docs'"
            )
            if not iw_fk_exists:
                replaces_col_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'internal_working_docs' AND column_name = 'replaces_id'"
                )
                if replaces_col_exists:
                    try:
                        await conn.execute(
                            'ALTER TABLE "internal_working_docs" '
                            'ADD CONSTRAINT "fk_internal_working_docs_replaces_id" '
                            'FOREIGN KEY ("replaces_id") REFERENCES "internal_working_docs"("id") ON DELETE SET NULL'
                        )
                        logger.info("Added FK constraint 'fk_internal_working_docs_replaces_id'")
                    except Exception as e:
                        logger.debug(f"FK constraint add skipped: {e}")

            # Ensure viewer_completed_queue table exists
            vcq_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'viewer_completed_queue'"
            )
            if not vcq_table_exists:
                await conn.execute("""
                    CREATE TABLE viewer_completed_queue (
                        id UUID PRIMARY KEY,
                        viewer_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        filing_id UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
                        client_name VARCHAR(255) NOT NULL,
                        financial_year VARCHAR(20) NOT NULL,
                        completed_at TIMESTAMPTZ NOT NULL,
                        completed_by UUID REFERENCES users(id) ON DELETE SET NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        dismissed_at TIMESTAMPTZ
                    )
                """)
                await conn.execute(
                    'CREATE INDEX "ix_viewer_completed_queue_viewer_id" ON "viewer_completed_queue" ("viewer_id")'
                )
                await conn.execute(
                    'CREATE INDEX "ix_viewer_completed_queue_undismissed" ON "viewer_completed_queue" ("viewer_id") WHERE dismissed_at IS NULL'
                )
                logger.info("Created table 'viewer_completed_queue'")

            # ─── Add completed_doc_status enum and approval columns ──────
            # Create the enum type if it doesn't exist
            enum_exists = await conn.fetchval(
                "SELECT 1 FROM pg_type WHERE typname = 'completed_doc_status'"
            )
            if not enum_exists:
                await conn.execute(
                    "CREATE TYPE completed_doc_status AS ENUM ('UPLOADED', 'MANAGER_APPROVED', 'PARTNER_APPROVED', 'MANAGER_REJECTED')"
                )
                logger.info("Created enum type 'completed_doc_status'")

            # Add status column to filing_completed_docs if missing
            status_col_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'filing_completed_docs' AND column_name = 'status'"
            )
            if not status_col_exists:
                await conn.execute(
                    "ALTER TABLE filing_completed_docs ADD COLUMN status completed_doc_status NOT NULL DEFAULT 'UPLOADED'"
                )
                logger.info("Added 'status' column to filing_completed_docs")

            # Add approval/rejection columns
            for col, col_type in [
                ("manager_approved_by", "UUID REFERENCES users(id) ON DELETE SET NULL"),
                ("manager_approved_at", "TIMESTAMPTZ"),
                ("manager_rejected_by", "UUID REFERENCES users(id) ON DELETE SET NULL"),
                ("manager_rejected_at", "TIMESTAMPTZ"),
                ("rejection_reason", "TEXT"),
                ("partner_approved_by", "UUID REFERENCES users(id) ON DELETE SET NULL"),
                ("partner_approved_at", "TIMESTAMPTZ"),
            ]:
                col_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'filing_completed_docs' AND column_name = $1",
                    col,
                )
                if not col_exists:
                    await conn.execute(
                        f"ALTER TABLE filing_completed_docs ADD COLUMN {col} {col_type}"
                    )
                    logger.info(f"Added '{col}' column to filing_completed_docs")

            # ─── Migrate email_config from OAuth to SMTP ─────────────────
            email_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'email_config'"
            )
            if email_table_exists:
                # Add new SMTP columns if missing
                for col, col_type, default in [
                    ("smtp_host", "VARCHAR(255)", "'smtp.gmail.com'"),
                    ("smtp_port", "INTEGER", "587"),
                    ("smtp_user", "VARCHAR(255)", "''"),
                    ("smtp_password", "VARCHAR(255)", "''"),
                    ("use_tls", "BOOLEAN", "'true'"),
                ]:
                    col_exists = await conn.fetchval(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'email_config' AND column_name = $1",
                        col,
                    )
                    if not col_exists:
                        await conn.execute(
                            f'ALTER TABLE "email_config" ADD COLUMN "{col}" {col_type} NOT NULL DEFAULT {default}'
                        )
                        logger.info(f"Added column '{col}' to email_config")

                # Drop old OAuth columns if they still exist
                for old_col in ("credentials_json", "token_json"):
                    old_exists = await conn.fetchval(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'email_config' AND column_name = $1",
                        old_col,
                    )
                    if old_exists:
                        await conn.execute(
                            f'ALTER TABLE "email_config" DROP COLUMN "{old_col}"'
                        )
                        logger.info(f"Dropped column '{old_col}' from email_config")

            # ─── Income head doc-type categorization ─────────────────────
            # Add snapshot columns to itr_filings (idempotent)
            for col, col_type in [
                ("income_heads_snapshot", "JSONB"),
                ("income_heads_confirmed_at", "TIMESTAMPTZ"),
            ]:
                col_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'itr_filings' AND column_name = $1",
                    col,
                )
                if not col_exists:
                    await conn.execute(
                        f'ALTER TABLE "itr_filings" ADD COLUMN "{col}" {col_type}'
                    )
                    logger.info(f"Added column '{col}' to itr_filings")

            # Create enum types if missing
            ihc_exists = await conn.fetchval(
                "SELECT 1 FROM pg_type WHERE typname = 'income_head_category'"
            )
            if not ihc_exists:
                await conn.execute(
                    "CREATE TYPE income_head_category AS ENUM ("
                    "'SALARY','ESOP','RENTAL_INCOME','MORE_THAN_2_PROPERTIES',"
                    "'CAPITAL_GAIN_SHARES','CAPITAL_GAIN_LAND','BUSINESS_PROFESSION',"
                    "'INTEREST_DIVIDEND','FOREIGN_ASSETS','ANY_OTHER','OTHERS')"
                )
                logger.info("Created enum type 'income_head_category'")

            dsc_exists = await conn.fetchval(
                "SELECT 1 FROM pg_type WHERE typname = 'doc_sub_category'"
            )
            if not dsc_exists:
                await conn.execute(
                    "CREATE TYPE doc_sub_category AS ENUM ('BASE','INCREMENTAL')"
                )
                logger.info("Created enum type 'doc_sub_category'")

            # Create master_doc_type_income_heads junction table
            mapping_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'master_doc_type_income_heads'"
            )
            if not mapping_table_exists:
                await conn.execute("""
                    CREATE TABLE master_doc_type_income_heads (
                        id UUID PRIMARY KEY,
                        doc_type_id UUID NOT NULL REFERENCES master_document_types(id) ON DELETE CASCADE,
                        income_head income_head_category NOT NULL,
                        sub_category doc_sub_category NOT NULL DEFAULT 'INCREMENTAL',
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        CONSTRAINT uq_doc_type_income_head UNIQUE (doc_type_id, income_head)
                    )
                """)
                await conn.execute(
                    'CREATE INDEX "ix_mdtih_doc_type" ON "master_doc_type_income_heads" ("doc_type_id")'
                )
                await conn.execute(
                    'CREATE INDEX "ix_mdtih_income_head" ON "master_doc_type_income_heads" ("income_head")'
                )
                logger.info("Created table 'master_doc_type_income_heads'")

            # ─── Text-field placeholders ─────────────────────────────────
            tfs_enum_exists = await conn.fetchval(
                "SELECT 1 FROM pg_type WHERE typname = 'text_field_status'"
            )
            if not tfs_enum_exists:
                await conn.execute(
                    "CREATE TYPE text_field_status AS ENUM ('PENDING','FILLED','APPROVED','REJECTED')"
                )
                logger.info("Created enum type 'text_field_status'")

            mtft_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'master_text_field_types'"
            )
            if not mtft_table_exists:
                await conn.execute("""
                    CREATE TABLE master_text_field_types (
                        id UUID PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        description TEXT,
                        max_length INTEGER NOT NULL DEFAULT 200,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        display_order INTEGER NOT NULL DEFAULT 0,
                        created_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                        updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                logger.info("Created table 'master_text_field_types'")

            ftf_table_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'filing_text_fields'"
            )
            if not ftf_table_exists:
                await conn.execute("""
                    CREATE TABLE filing_text_fields (
                        id UUID PRIMARY KEY,
                        filing_id UUID NOT NULL REFERENCES itr_filings(id) ON DELETE CASCADE,
                        field_type_id UUID NOT NULL REFERENCES master_text_field_types(id) ON DELETE RESTRICT,
                        status text_field_status NOT NULL DEFAULT 'PENDING',
                        value TEXT,
                        rejection_reason TEXT,
                        filled_at TIMESTAMPTZ,
                        filled_by UUID REFERENCES users(id) ON DELETE SET NULL,
                        reviewed_at TIMESTAMPTZ,
                        reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
                        assigned_by UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                        assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                """)
                await conn.execute(
                    'CREATE INDEX "ix_filing_text_field_type" ON "filing_text_fields" ("filing_id", "field_type_id")'
                )
                logger.info("Created table 'filing_text_fields'")

        finally:
            await conn.close()

        logger.info("Column sync complete.")
    except Exception as e:
        logger.warning(f"Column sync failed: {e}")


async def _migrate_renamed_enum_values():
    """Migrate renamed enum values in existing database rows.

    This handles renaming ON_BOARDING → DOCUMENT_UPLOAD in all relevant tables.
    Idempotent: safe to run on every startup.
    """
    import asyncpg

    rename_map = [
        # (table, column, old_value, new_value)
        ("itr_filings", "status", "ON_BOARDING", "DOCUMENT_UPLOAD"),
        ("filing_state_history", "from_status", "ON_BOARDING", "DOCUMENT_UPLOAD"),
        ("filing_state_history", "to_status", "ON_BOARDING", "DOCUMENT_UPLOAD"),
        # Computation status: APPROVED → CLIENT_APPROVED (two-step approval flow)
        ("filing_computations", "status", "APPROVED", "CLIENT_APPROVED"),
    ]

    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
        )

        try:
            for table, column, old_val, new_val in rename_map:
                # Check if new enum value exists (it should after _sync_pg_enums)
                result = await conn.fetchval(
                    "SELECT 1 FROM pg_enum WHERE enumlabel = $1 AND enumtypid = "
                    "(SELECT atttypid FROM pg_attribute "
                    " JOIN pg_class ON pg_class.oid = pg_attribute.attrelid "
                    " WHERE pg_class.relname = $2 AND pg_attribute.attname = $3)",
                    new_val, table, column,
                )
                if not result:
                    continue  # New enum value not yet available, skip

                count = await conn.fetchval(
                    f'UPDATE "{table}" SET "{column}" = $1 WHERE "{column}" = $2',
                    new_val, old_val,
                )
                if count and int(count.split()[-1]) > 0:
                    logger.info(f"Migrated {count} rows in {table}.{column}: {old_val} → {new_val}")
        finally:
            await conn.close()

        logger.info("Enum value migration complete.")
    except Exception as e:
        logger.warning(f"Enum value migration failed: {e}")


async def _cleanup_manager_tags():
    """Remove deprecated MANAGER-type tags and their executive_tag assignments.

    The MANAGER tag_type has been replaced by the real Manager role.
    Existing rows with tag_type='MANAGER' in the database will crash SQLAlchemy
    because the Python TagType enum no longer contains 'MANAGER'.
    This cleans them up using raw SQL (bypassing the ORM enum check).
    """
    import asyncpg

    try:
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
        )

        try:
            # Delete executive_tags that reference MANAGER-type tags
            deleted_et = await conn.execute(
                "DELETE FROM executive_tags WHERE tag_id IN "
                "(SELECT id FROM tags WHERE tag_type = 'MANAGER')"
            )
            # Delete the MANAGER tags themselves
            deleted_tags = await conn.execute(
                "DELETE FROM tags WHERE tag_type = 'MANAGER'"
            )
            if "DELETE" in (deleted_et or "") or "DELETE" in (deleted_tags or ""):
                logger.info(
                    f"Cleaned up MANAGER tags: {deleted_et}, {deleted_tags}"
                )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"MANAGER tag cleanup failed: {e}")


async def _seed_admin_user():
    """Create the admin (PARTNER) user on first startup if none exists."""
    from sqlalchemy import select, text

    from app.core.security import hash_password
    from app.database import AsyncSessionLocal
    from app.enums import AccountStatus, UserRole
    from app.models.user import User

    try:
        async with AsyncSessionLocal() as db:
            # Advisory lock prevents race between multiple uvicorn workers
            await db.execute(text("SELECT pg_advisory_xact_lock(2)"))

            result = await db.execute(
                select(User).where(User.role == UserRole.PARTNER).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                logger.info("Admin (PARTNER) user already exists — skipping seed.")
                return

            admin = User(
                email=settings.ADMIN_EMAIL,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                full_name=settings.ADMIN_FULL_NAME,
                role=UserRole.PARTNER,
                account_status=AccountStatus.ACTIVE,
                is_active=True,
            )
            db.add(admin)
            await db.commit()
            logger.info(f"Admin user created: {settings.ADMIN_EMAIL}")
    except Exception as e:
        logger.warning(f"Admin seed skipped: {e}")


async def _seed_dashboard_user():
    """Create the DASHBOARD_USER on first startup if configured via env vars."""
    from sqlalchemy import select, text

    from app.core.security import hash_password
    from app.database import AsyncSessionLocal
    from app.enums import AccountStatus, UserRole
    from app.models.user import User

    if not settings.DASHBOARD_USER_EMAIL:
        logger.info("DASHBOARD_USER_EMAIL not set — skipping dashboard user seed.")
        return

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT pg_advisory_xact_lock(3)"))

            result = await db.execute(
                select(User).where(User.email == settings.DASHBOARD_USER_EMAIL).limit(1)
            )
            if result.scalar_one_or_none() is not None:
                logger.info("Dashboard user already exists — skipping seed.")
                return

            dashboard_user = User(
                email=settings.DASHBOARD_USER_EMAIL,
                password_hash=hash_password(settings.DASHBOARD_USER_PASSWORD),
                full_name=settings.DASHBOARD_USER_FULL_NAME,
                role=UserRole.DASHBOARD_USER,
                account_status=AccountStatus.ACTIVE,
                is_active=True,
            )
            db.add(dashboard_user)
            await db.commit()
            logger.info(f"Dashboard user created: {settings.DASHBOARD_USER_EMAIL}")
    except Exception as e:
        logger.warning(f"Dashboard user seed skipped: {e}")
