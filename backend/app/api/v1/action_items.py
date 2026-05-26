"""API v1 — Action Items endpoints (dynamically computed)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.enums import ActionItemType
from app.models.user import User
from app.schemas.action_item import ActionItemListResponse
from app.services.action_item_service import get_action_items

router = APIRouter()


@router.get("", response_model=ActionItemListResponse)
async def list_action_items(
    type: Optional[ActionItemType] = Query(None, description="Filter by action item type"),
    filing_id: Optional[UUID] = Query(None, description="Filter by filing ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get action items (what you need to do) for the current user.

    Action items are computed dynamically from the current state of filings,
    documents, computations, and client accounts. They are role-scoped:
    - Partner: sees all actionable items across all clients
    - Executive: sees items for assigned clients only
    - Client: sees their own pending actions
    """
    items = await get_action_items(
        db=db,
        user=current_user,
        type_filter=type,
        filing_id_filter=filing_id,
    )

    # Build counts_by_type
    counts: dict[str, int] = {}
    for item in items:
        counts[item.type.value] = counts.get(item.type.value, 0) + 1

    return ActionItemListResponse(
        items=items,
        total=len(items),
        counts_by_type=counts,
    )
