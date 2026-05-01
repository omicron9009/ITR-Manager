"""Service — MinIO storage operations (pre-signed URLs, file metadata)."""

import uuid
from datetime import timedelta
from typing import Optional

from minio import Minio

from app.config import settings

# Lazy-initialized MinIO client
_minio_client: Optional[Minio] = None


def _get_client() -> Minio:
    """Get or create the MinIO client (lazy initialization)."""
    global _minio_client
    if _minio_client is None:
        _minio_client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_USE_SSL,
        )
    return _minio_client


def ensure_bucket_exists():
    """Ensure the default bucket exists."""
    client = _get_client()
    if not client.bucket_exists(settings.MINIO_BUCKET_NAME):
        client.make_bucket(settings.MINIO_BUCKET_NAME)


def generate_object_key(client_id: str, financial_year: str, folder: str, filename: str) -> str:
    """
    Generate a structured object key for MinIO.
    Pattern: clients/{client_id}/ITR-{FY}/{folder}/{uuid}_{filename}
    """
    unique_prefix = str(uuid.uuid4())[:8]
    return f"clients/{client_id}/ITR-{financial_year}/{folder}/{unique_prefix}_{filename}"


def generate_pan_object_key(client_id: str, filename: str) -> str:
    """Generate object key for PAN document uploads."""
    unique_prefix = str(uuid.uuid4())[:8]
    return f"clients/{client_id}/pan/{unique_prefix}_{filename}"


def get_presigned_upload_url(
    object_key: str,
    content_type: str,
    expires: timedelta = timedelta(hours=1),
) -> str:
    """Generate a pre-signed PUT URL for file upload."""
    url = _get_client().presigned_put_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_key,
        expires=expires,
    )
    return url


def get_presigned_download_url(
    object_key: str,
    expires: timedelta = timedelta(hours=1),
    filename: Optional[str] = None,
) -> str:
    """Generate a pre-signed GET URL for file download."""
    from minio.commonconfig import CopySource
    from urllib.parse import quote

    response_headers = {}
    if filename:
        response_headers["response-content-disposition"] = f'attachment; filename="{quote(filename)}"'

    url = _get_client().presigned_get_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_key,
        expires=expires,
    )
    return url
