"""Core — Permission checks for scoped access control."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import UserRole
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.user import User


async def check_executive_client_access(
    db: AsyncSession,
    executive_id: UUID,
    client_id: UUID,
) -> bool:
    """Verify that an Executive is assigned to a specific Client."""
    result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.client_id == client_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    return result.scalar_one_or_none() is not None


async def enforce_client_access(
    db: AsyncSession,
    current_user: User,
    client_id: UUID,
) -> None:
    """
    Enforce that the current user can access the given client's data.
    - Partner: always allowed
    - Executive: only if assigned to the client
    - Client: only if it's their own ID
    """
    if current_user.role == UserRole.PARTNER:
        return  # Partner can access all clients

    if current_user.role == UserRole.EXECUTIVE:
        has_access = await check_executive_client_access(db, current_user.id, client_id)
        if not has_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this client",
            )
        return

    if current_user.role == UserRole.CLIENT:
        if current_user.id != client_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access restricted to your own data",
            )
        return

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def enforce_filing_access(
    db: AsyncSession,
    current_user: User,
    filing_client_id: UUID,
) -> None:
    """Enforce access to a specific filing based on client ownership."""
    await enforce_client_access(db, current_user, filing_client_id)
