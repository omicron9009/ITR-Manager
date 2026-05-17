"""Service — MinIO storage operations (pre-signed URLs, file metadata)."""

import io
import re
import uuid
from datetime import timedelta
from typing import Optional

from minio import Minio

from app.config import settings

# Lazy-initialized MinIO clients
_minio_client: Optional[Minio] = None
_minio_public_client: Optional[Minio] = None


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


def _get_public_client() -> Minio:
    """Get or create a MinIO client using the public endpoint for presigned URLs."""
    global _minio_public_client
    if _minio_public_client is None:
        endpoint = settings.MINIO_PUBLIC_ENDPOINT or settings.MINIO_ENDPOINT
        _minio_public_client = Minio(
            endpoint,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_USE_SSL,
        )
    return _minio_public_client


def ensure_bucket_exists():
    """Ensure the default bucket exists."""
    client = _get_client()
    if not client.bucket_exists(settings.MINIO_BUCKET_NAME):
        client.make_bucket(settings.MINIO_BUCKET_NAME)


def _sanitize_name_for_path(name: str) -> str:
    """Sanitize a client name for use in file paths."""
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name.replace(' ', '_'))
    return sanitized or "client"


def build_client_dir(client_id: str, client_name: str) -> str:
    """Build the canonical client directory prefix: {SanitizedName}_{client_id}."""
    sanitized = _sanitize_name_for_path(client_name)
    return f"{sanitized}_{client_id}"


def generate_object_key(client_id: str, financial_year: str, folder: str, filename: str, client_name: str = "") -> str:
    """
    Generate a structured object key for MinIO.
    Pattern: clients/{Name}_{client_id}/ITR-{FY}/{folder}/{uuid}_{filename}
    """
    unique_prefix = str(uuid.uuid4())[:8]
    if client_name:
        client_dir = build_client_dir(client_id, client_name)
    else:
        client_dir = client_id
    return f"clients/{client_dir}/ITR-{financial_year}/{folder}/{unique_prefix}_{filename}"


def generate_onboarding_object_key(
    client_id: str, client_name: str, field_label: str, filename: str,
) -> str:
    """
    Generate object key for onboarding form file uploads.
    Pattern: clients/{Name}_{client_id}/onboarding/{Label}_{client_id}_{uuid}.{ext}
    """
    unique_prefix = str(uuid.uuid4())[:8]
    client_dir = build_client_dir(client_id, client_name)
    sanitized_label = _sanitize_name_for_path(field_label)
    # Preserve original file extension
    ext = ""
    if "." in filename:
        ext = "." + filename.rsplit(".", 1)[1]
    safe_name = f"{sanitized_label}_{client_id}_{unique_prefix}{ext}"
    return f"clients/{client_dir}/onboarding/{safe_name}"


def create_client_directory(client_id: str, client_name: str) -> str:
    """
    Create the base client directory in MinIO by uploading a .keep placeholder.
    Called when the Partner activates a client account.
    Returns the client directory prefix.
    """
    client_dir = build_client_dir(client_id, client_name)
    object_key = f"clients/{client_dir}/.keep"
    client = _get_client()
    client.put_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_key,
        data=io.BytesIO(b""),
        length=0,
    )
    return f"clients/{client_dir}"


def validate_object_key_prefix(object_key: str, client_id: str, client_name: str, expected_folder: str) -> None:
    """
    Validate that a user-supplied object_key starts with the expected prefix.
    Raises ValueError if the key doesn't match.
    """
    client_dir = build_client_dir(client_id, client_name)
    expected_prefix = f"clients/{client_dir}/{expected_folder}/"
    if not object_key.startswith(expected_prefix):
        raise ValueError(
            f"Invalid object key. Expected prefix: {expected_prefix}"
        )


def get_presigned_upload_url(
    object_key: str,
    content_type: str,
    expires: timedelta = timedelta(hours=1),
) -> str:
    """Generate a pre-signed PUT URL for file upload."""
    url = _get_public_client().presigned_put_object(
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
    from urllib.parse import quote

    response_headers = {}
    if filename:
        response_headers["response-content-disposition"] = f'attachment; filename="{quote(filename)}"'

    url = _get_public_client().presigned_get_object(
        bucket_name=settings.MINIO_BUCKET_NAME,
        object_name=object_key,
        expires=expires,
    )
    return url
