"""Service — Recovery code generation, verification, and management."""

import secrets
from datetime import datetime
from uuid import UUID

import bcrypt
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_code import RecoveryCode
from app.models.user import User

# Charset without ambiguous characters (no 0/O, 1/I/L)
_CHARSET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8
_CODE_COUNT = 8


def _generate_code() -> str:
    """Generate a single random recovery code."""
    return "".join(secrets.choice(_CHARSET) for _ in range(_CODE_LENGTH))


def _hash_code(code: str) -> str:
    """Hash a recovery code using bcrypt."""
    return bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_code(plaintext: str, hashed: str) -> bool:
    """Verify a plaintext code against its bcrypt hash."""
    return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))


async def generate_recovery_codes(db: AsyncSession, user_id: UUID) -> list[str]:
    """
    Generate recovery codes for a user, store hashed versions in DB,
    set recovery_codes_issued flag, and return plaintext codes.
    """
    # Delete any existing codes for this user
    await db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user_id))

    plaintext_codes: list[str] = []
    for _ in range(_CODE_COUNT):
        code = _generate_code()
        plaintext_codes.append(code)
        db.add(RecoveryCode(
            user_id=user_id,
            code_hash=_hash_code(code),
        ))

    # Mark user as having received codes
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()
    user.recovery_codes_issued = True

    await db.flush()
    return plaintext_codes


async def verify_recovery_code(db: AsyncSession, user_id: UUID, plaintext_code: str) -> bool:
    """
    Verify a recovery code for a user. If valid, mark it as used.
    Returns True if the code is valid and unused, False otherwise.
    """
    result = await db.execute(
        select(RecoveryCode).where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.is_used == False,
        )
    )
    unused_codes = result.scalars().all()

    normalized = plaintext_code.strip().upper()
    for rc in unused_codes:
        if _verify_code(normalized, rc.code_hash):
            rc.is_used = True
            rc.used_at = datetime.utcnow()
            await db.flush()
            return True

    return False


async def get_unused_code_count(db: AsyncSession, user_id: UUID) -> int:
    """Get the number of remaining unused recovery codes for a user."""
    result = await db.execute(
        select(func.count()).select_from(RecoveryCode).where(
            RecoveryCode.user_id == user_id,
            RecoveryCode.is_used == False,
        )
    )
    return result.scalar() or 0
