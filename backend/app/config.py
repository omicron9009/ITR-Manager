"""ITR Filing Platform — Application Configuration."""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    APP_NAME: str = "ITR Filing Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

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

    # JWT Authentication
    JWT_SECRET_KEY: str = "CHANGE-ME-TO-A-RANDOM-SECRET"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Admin seed (created on first startup)
    ADMIN_EMAIL: str = "admin@itr-platform.com"
    ADMIN_PASSWORD: str = "admin123"
    ADMIN_FULL_NAME: str = "Platform Admin"

    # CORS
    CORS_ORIGINS: List[str] = ["http://localhost:3000"]

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000


settings = Settings()
