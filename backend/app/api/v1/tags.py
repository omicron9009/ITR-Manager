"""API v1 — Tag management and tag-based analytics endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_executive, get_current_manager_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import TagType, UserRole
from app.models.user import User
from app.schemas.tag import (
    ExecutiveTagAssignRequest,
    ExecutiveTagBulkAssignRequest,
    ExecutiveTagResponse,
    ExecutiveTagsListResponse,
    ExecutiveTagsView,
    LocationDetailResponse,
    LocationSummaryItem,
    LocationSummaryResponse,
    TagBrief,
    TagCreateRequest,
    TagListResponse,
    TagResponse,
    TagUpdateRequest,
)
from app.services.tag_service import (
    assign_tag_to_executive,
    bulk_assign_tag,
    create_tag,
    deactivate_tag,
    get_all_executives_with_tags,
    get_executive_tags,
    get_location_detail,
    get_location_summary,
    get_tag_executive_count,
    list_tags,
    remove_tag_from_executive,
    update_tag,
)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# TAG CRUD (Partner Only)
# ═══════════════════════════════════════════════════════════════


@router.post("", response_model=TagResponse, status_code=201)
async def create_new_tag(
    body: TagCreateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tag (Manager or Location). Partner only."""
    tag = await create_tag(
        db=db,
        name=body.name,
        tag_type=body.tag_type,
        created_by=current_user.id,
        description=body.description,
    )
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    exec_count = await get_tag_executive_count(db, tag.id)
    return TagResponse(
        id=tag.id,
        name=tag.name,
        tag_type=tag.tag_type,
        description=tag.description,
        is_active=tag.is_active,
        created_at=tag.created_at,
        executive_count=exec_count,
    )


@router.get("", response_model=TagListResponse)
async def list_all_tags(
    tag_type: Optional[TagType] = Query(None, alias="type"),
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """List all tags, optionally filtered by type. Partner only."""
    from app.config import settings as _settings
    from app.core.cache import NS, get_or_compute

    async def _build() -> TagListResponse:
        tags = await list_tags(db, tag_type=tag_type)
        items = []
        for tag in tags:
            exec_count = await get_tag_executive_count(db, tag.id)
            items.append(
                TagResponse(
                    id=tag.id,
                    name=tag.name,
                    tag_type=tag.tag_type,
                    description=tag.description,
                    is_active=tag.is_active,
                    created_at=tag.created_at,
                    executive_count=exec_count,
                )
            )
        return TagListResponse(items=items, total=len(items))

    return await get_or_compute(
        NS.TAGS,
        f"list:type={tag_type.value if tag_type else 'all'}",
        _settings.CACHE_TTL_MASTER_DATA,
        _build,
    )


@router.patch("/{tag_id}", response_model=TagResponse)
async def update_existing_tag(
    tag_id: UUID,
    body: TagUpdateRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Update a tag's name, description, or active status. Partner only."""
    tag = await update_tag(
        db=db,
        tag_id=tag_id,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
    )
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    exec_count = await get_tag_executive_count(db, tag.id)
    return TagResponse(
        id=tag.id,
        name=tag.name,
        tag_type=tag.tag_type,
        description=tag.description,
        is_active=tag.is_active,
        created_at=tag.created_at,
        executive_count=exec_count,
    )


@router.delete("/{tag_id}", response_model=dict)
async def delete_tag(
    tag_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a tag (set is_active=False). Partner only."""
    tag = await deactivate_tag(db, tag_id)
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    return {"message": f"Tag '{tag.name}' has been deactivated"}


# ═══════════════════════════════════════════════════════════════
# TAG ASSIGNMENT (Partner Only)
# ═══════════════════════════════════════════════════════════════


@router.post("/assign", response_model=ExecutiveTagResponse)
async def assign_tag(
    body: ExecutiveTagAssignRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign a tag to an executive. Manager/Partner."""
    assignment = await assign_tag_to_executive(
        db=db,
        executive_id=body.executive_id,
        tag_id=body.tag_id,
        assigned_by=current_user.id,
    )
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    # Fetch names for response
    from sqlalchemy import select
    from app.models.tag import Tag

    tag_result = await db.execute(select(Tag).where(Tag.id == assignment.tag_id))
    tag = tag_result.scalar_one()
    exec_result = await db.execute(select(User).where(User.id == assignment.executive_id))
    executive = exec_result.scalar_one()

    return ExecutiveTagResponse(
        id=assignment.id,
        executive_id=assignment.executive_id,
        executive_name=executive.full_name,
        tag_id=assignment.tag_id,
        tag_name=tag.name,
        tag_type=tag.tag_type,
        assigned_at=assignment.assigned_at,
        is_active=assignment.is_active,
    )


@router.post("/bulk-assign", response_model=dict)
async def bulk_assign_tag_endpoint(
    body: ExecutiveTagBulkAssignRequest,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Assign a tag to multiple executives at once. Manager/Partner."""
    assignments = await bulk_assign_tag(
        db=db,
        executive_ids=body.executive_ids,
        tag_id=body.tag_id,
        assigned_by=current_user.id,
    )
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    return {
        "message": f"Tag assigned to {len(assignments)} executive(s)",
        "assigned_count": len(assignments),
        "requested_count": len(body.executive_ids),
    }


@router.delete("/assign/{executive_id}/{tag_id}", response_model=dict)
async def unassign_tag(
    executive_id: UUID,
    tag_id: UUID,
    current_user: User = Depends(get_current_manager_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Remove a tag from an executive. Manager/Partner."""
    await remove_tag_from_executive(db, executive_id, tag_id)
    from app.core.cache import NS, bump_version
    await bump_version(NS.TAGS)
    return {"message": "Tag removed from executive"}


@router.get("/executives", response_model=ExecutiveTagsListResponse)
async def list_executives_with_tags(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """List all executives with their assigned tags. Partner only."""
    items_data = await get_all_executives_with_tags(db)
    items = [
        ExecutiveTagsView(
            executive_id=item["executive_id"],
            executive_name=item["executive_name"],
            location_tags=[TagBrief(**t) for t in item["location_tags"]],
        )
        for item in items_data
    ]
    return ExecutiveTagsListResponse(items=items, total=len(items))


@router.get("/executives/{executive_id}", response_model=ExecutiveTagsView)
async def get_executive_tags_endpoint(
    executive_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get tags for a specific executive. Partner only."""
    from sqlalchemy import select

    exec_result = await db.execute(
        select(User).where(User.id == executive_id, User.role == UserRole.EXECUTIVE)
    )
    executive = exec_result.scalar_one_or_none()
    if not executive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found")

    tags_data = await get_executive_tags(db, executive_id)
    return ExecutiveTagsView(
        executive_id=executive.id,
        executive_name=executive.full_name,
        location_tags=[TagBrief(**t) for t in tags_data["location_tags"]],
    )


# ═══════════════════════════════════════════════════════════════
# SELF-VIEW (Executive)
# ═══════════════════════════════════════════════════════════════


@router.get("/my-tags", response_model=ExecutiveTagsView)
async def get_my_tags(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Executive views their own tags. Also accessible by Partner."""
    if current_user.role not in (UserRole.EXECUTIVE, UserRole.PARTNER):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    target_id = current_user.id
    if current_user.role == UserRole.PARTNER:
        # Partner can also use this endpoint — returns empty since they're not an executive
        return ExecutiveTagsView(
            executive_id=current_user.id,
            executive_name=current_user.full_name,
            location_tags=[],
        )

    tags_data = await get_executive_tags(db, target_id)
    return ExecutiveTagsView(
        executive_id=current_user.id,
        executive_name=current_user.full_name,
        location_tags=[TagBrief(**t) for t in tags_data["location_tags"]],
    )


# ═══════════════════════════════════════════════════════════════
# SUMMARY / ANALYTICS (Partner Only)
# ═══════════════════════════════════════════════════════════════


@router.get("/summary/location", response_model=LocationSummaryResponse)
async def location_summary(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Per-location summary: executive count, filing stats. Partner only."""
    data = await get_location_summary(db)
    items = [LocationSummaryItem(**item) for item in data]
    return LocationSummaryResponse(items=items, total=len(items))


@router.get("/summary/location/{tag_id}", response_model=LocationDetailResponse)
async def location_detail(
    tag_id: UUID,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Detailed view for one location: executives, filings. Partner only."""
    data = await get_location_detail(db, tag_id)
    return LocationDetailResponse(**data)
