"""ITR Filing Platform — FastAPI Application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    # ── Startup ──
    await _ensure_database_exists()
    await _create_tables()
    await _seed_admin_user()

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
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
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


async def _ensure_database_exists():
    """Connect to the default 'postgres' DB and create the target database if it doesn't exist."""
    import asyncpg

    try:
        # Connect to the default maintenance database
        conn = await asyncpg.connect(
            host=settings.DATABASE_HOST,
            port=settings.DATABASE_PORT,
            user=settings.DATABASE_USER,
            password=settings.DATABASE_PASSWORD,
            database="postgres",
        )

        # Check if our target database exists
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            settings.DATABASE_NAME,
        )

        if not exists:
            # CREATE DATABASE cannot run inside a transaction
            await conn.execute(f'CREATE DATABASE "{settings.DATABASE_NAME}"')
            logger.info(f"Database '{settings.DATABASE_NAME}' created successfully.")
        else:
            logger.info(f"Database '{settings.DATABASE_NAME}' already exists.")

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
    except Exception as e:
        logger.warning(f"Table creation skipped: {e}")


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
