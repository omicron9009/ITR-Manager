"""ITR Filing Platform — Application Configuration."""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    APP_NAME: str = "AIकर"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # Firm branding (used in emails)
    FIRM_NAME: str = "P G Joshi & Co LLP"
    FIRM_WEBSITE: str = "pgjco.com"
    FIRM_PHONE: str = "0712-2524309"
    FRONTEND_URL: str = "https://workpartners.co.in"

    # Database
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "itr_admin"
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "itr_platform"
    POSTGRES_POOL_SIZE: int = 20
    POSTGRES_MAX_OVERFLOW: int = 10

# Change these in settings.py
    @property
    def POSTGRES_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def POSTGRES_URL_SYNC(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # MinIO
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET_NAME: str = "itr-documents"
    MINIO_USE_SSL: bool = False
    # Public endpoint for presigned URLs (used in browser). Falls back to MINIO_ENDPOINT.
    MINIO_PUBLIC_ENDPOINT: str = ""
    MINIO_PUBLIC_USE_SSL: bool = True
    # When set, bucket-level SSE-S3 encryption is enabled on startup (production only).
    # Leave empty for dev — the standalone MinIO container has no KMS configured.
    MINIO_KMS_SECRET_KEY: str = ""

    # JWT Authentication
    JWT_SECRET_KEY: str = "CHANGE-ME-TO-A-RANDOM-SECRET"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Long-lived token for DASHBOARD_USER role (read-only TV/kiosk dashboards).
    # Default: 30 days (60 * 24 * 30 = 43,200 minutes). Override via env if needed.
    JWT_DASHBOARD_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30
    # Password reset link expiry (minutes)
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 15
    # Rate limit: max reset requests per email per hour
    PASSWORD_RESET_MAX_PER_HOUR: int = 5

    # Admin seed (created on first startup)
    ADMIN_EMAIL: str = "admin@itr-platform.com"
    ADMIN_PASSWORD: str = "admin123"
    ADMIN_FULL_NAME: str = "Platform Admin"

    # Dashboard user seed (read-only analytics access)
    DASHBOARD_USER_EMAIL: str = ""
    DASHBOARD_USER_PASSWORD: str = ""
    DASHBOARD_USER_FULL_NAME: str = "Dashboard Viewer"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000

    # ─── Cache (Redis) ──────────────────────────────────────
    # Toggle the entire cache layer. When false, all cache ops are no-ops.
    CACHE_ENABLED: bool = True
    REDIS_URL: str = "redis://redis:6379/0"

    # TTLs (seconds) — keep short; long TTLs hide bugs
    CACHE_TTL_USER: int = 5            # auth user-by-id
    CACHE_TTL_SCOPE_IDS: int = 30      # manager team / exec clients / partner client ids
    CACHE_TTL_TAGS: int = 5            # tags list (has N+1 exec-count queries, keep cached but short)
    CACHE_TTL_CLIENT_LIST: int = 10    # /clients paginated list
    CACHE_TTL_DASHBOARD: int = 15      # /dashboard/summary
    CACHE_TTL_REPORT: int = 60         # /reports/dashboard (reduced from 300; bumped on mutations)

    # Gzip compression threshold (bytes). 0 disables.
    GZIP_MIN_SIZE: int = 1024

    # ─── WhatsApp / OpenWA gateway ──────────────────────────
    # Fernet key (base64-encoded 32 bytes) used to encrypt the OpenWA admin
    # API key at rest. If not a valid Fernet key, the service derives one
    # via sha256 — but you should set a real one in production.
    WHATSAPP_ENCRYPTION_KEY: str = "CHANGE-ME-WHATSAPP-FERNET-KEY-32B"
    # Default values pre-filled in the Setup form (UI may override).
    WHATSAPP_DEFAULT_BASE_URL: str = "http://openwa-api:2785"
    WHATSAPP_DEFAULT_SESSION_NAME: str = "itr-platform"
    WHATSAPP_HTTP_TIMEOUT_SECONDS: int = 10
    # Watchdog: periodically polls OpenWA session status and auto-reconnects.
    WHATSAPP_WATCHDOG_ENABLED: bool = True
    WHATSAPP_WATCHDOG_INTERVAL_SECONDS: int = 300  # 5 minutes
    # OpenWA bootstrap admin key (used by the operator once to issue a
    # per-app API key that we then store in DB). Not consumed by backend
    # at runtime — exposed here so the deploy can read it from the same .env.
    WHATSAPP_OPENWA_MASTER_KEY: str = ""


settings = Settings()
