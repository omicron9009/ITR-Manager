"""Core — File upload validation helpers."""
import re
from fastapi import HTTPException, status

# Maximum file size: 10 MB
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Allowed file extensions (lowercase, without dot)
ALLOWED_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "csv",
    "png",
    "jpg",
    "jpeg",
}

# Allowed MIME types
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/csv",
    "image/png",
    "image/jpeg",
}


def validate_file_type(filename: str, content_type: str) -> None:
    """Validate that the file extension and content type are allowed.

    Raises HTTPException 422 if the file type is not permitted.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '.{ext}' is not allowed. Allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Content type '{content_type}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_CONTENT_TYPES))}",
        )


def validate_file_size(file_size: int) -> None:
    """Validate that the file size does not exceed the maximum limit.

    Raises HTTPException 422 if file is too large.
    """
    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File size ({file_size} bytes) exceeds maximum allowed size of 10 MB.",
        )


def sanitize_filename(filename: str) -> str:
    """Sanitize a user-provided filename to prevent path traversal and injection.

    - Strips directory components (path traversal)
    - Removes null bytes
    - Removes HTML/script-dangerous characters
    - Collapses whitespace
    - Truncates to 255 characters

    Raises HTTPException 422 if the filename is empty after sanitization.
    """
    # Remove null bytes
    filename = filename.replace("\x00", "")
    # Take only the basename (strip any path separators)
    filename = filename.replace("\\", "/")
    filename = filename.rsplit("/", 1)[-1]
    # Remove characters dangerous for HTML or filesystem: < > " ' & | ; ` $ { }
    filename = re.sub(r'[<>"\'&|;`${}]', "", filename)
    # Collapse whitespace
    filename = re.sub(r"\s+", " ", filename).strip()
    # Truncate
    filename = filename[:255]

    if not filename or filename == "." or filename == "..":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid filename after sanitization.",
        )
    return filename
