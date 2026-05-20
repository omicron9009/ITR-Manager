"""ITR Filing Platform — FastAPI Application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.core.security import get_current_user
import sys

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

            # Add FK constraint for rejected_by if not present
            fk_exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_name = 'fk_filing_computations_rejected_by' "
                "AND table_name = 'filing_computations'"
            )
            if not fk_exists:
                col_exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'filing_computations' AND column_name = 'rejected_by'"
                )
                if col_exists:
                    await conn.execute(
                        'ALTER TABLE "filing_computations" '
                        'ADD CONSTRAINT "fk_filing_computations_rejected_by" '
                        'FOREIGN KEY ("rejected_by") REFERENCES "users"("id") ON DELETE SET NULL'
                    )
                    logger.info("Added FK constraint 'fk_filing_computations_rejected_by'")
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
