"""ITR Filing Platform — FastAPI Application."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="ITR Filing Management Platform API — manages end-to-end ITR filing lifecycle for CA practices.",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
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


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    # Ensure PostgreSQL database exists
    await _ensure_database_exists()

    # Run pending Alembic migrations
    await _run_migrations()

    # Ensure MinIO bucket exists
    try:
        from app.services.storage_service import ensure_bucket_exists
        ensure_bucket_exists()
    except Exception:
        logger.warning("MinIO bucket initialization skipped — service may not be available")


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


async def _run_migrations():
    """Run Alembic migrations programmatically on startup."""
    from alembic import command
    from alembic.config import Config

    try:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

        # Run in a thread to avoid blocking the event loop (alembic is sync)
        import asyncio
        await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
        logger.info("Database migrations applied successfully.")
    except Exception as e:
        logger.warning(f"Migration step skipped: {e}")
