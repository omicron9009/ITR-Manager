"""ITR Filing Platform — FastAPI Application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.core.security import get_current_user
import sys

from prometheus_fastapi_instrumentator import Instrumentator # type: ignore

logging.basicConfig(
    stream=sys.stdout, 
    level=logging.DEBUG,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger("app")
logger.setLevel(logging.DEBUG)



@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # ── Startup ──
    await _ensure_database_exists()
    await _create_tables()
    await _seed_admin_user()
    await _seed_dashboard_user()
    

    try:
        from app.services.storage_service import ensure_bucket_exists
        ensure_bucket_exists()
    except Exception:
        logger.warning("MinIO bucket initialization skipped — service may not be available")

    yield
    # ── Shutdown (nothing needed) ──


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

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
        CompletedDocType, FormFieldType, AuditEventType, NotificationChannel, UserRole,
        TagType,
    )
    enum_map = {
        "user_role": UserRole,
        "account_status": AccountStatus,
        "filing_status": FilingStatus,
        "document_status": DocumentStatus,
        "computation_status": ComputationStatus,
        "completed_doc_type": CompletedDocType,
        "form_field_type": FormFieldType,
        "audit_event_type": AuditEventType,
        "notification_channel": NotificationChannel,
        "tag_type": TagType,
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
