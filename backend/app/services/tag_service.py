"""Service — Tag management and tag-based analytics."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import FilingStatus, TagType, UserRole
from app.models.executive_tag import ExecutiveTag
from app.models.filing import ITRFiling
from app.models.tag import Tag
from app.models.user import User


# ═══════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════


async def create_tag(
    db: AsyncSession,
    name: str,
    tag_type: TagType,
    created_by: UUID,
    description: str | None = None,
) -> Tag:
    """Create a new tag. Raises 409 if duplicate name+type."""
    existing = await db.execute(
        select(Tag).where(Tag.name == name, Tag.tag_type == tag_type)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tag '{name}' of type '{tag_type.value}' already exists",
        )

    tag = Tag(
        name=name,
        tag_type=tag_type,
        description=description,
        created_by=created_by,
    )
    db.add(tag)
    await db.flush()
    await db.refresh(tag)
    return tag


async def update_tag(
    db: AsyncSession,
    tag_id: UUID,
    name: str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> Tag:
    """Update tag fields. Raises 404 if not found, 409 if name conflicts."""
    result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")

    if name is not None and name != tag.name:
        # Check for duplicate
        dup = await db.execute(
            select(Tag).where(Tag.name == name, Tag.tag_type == tag.tag_type, Tag.id != tag_id)
        )
        if dup.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tag '{name}' of type '{tag.tag_type.value}' already exists",
            )
        tag.name = name

    if description is not None:
        tag.description = description
    if is_active is not None:
        tag.is_active = is_active

    await db.flush()
    await db.refresh(tag)
    return tag


async def deactivate_tag(db: AsyncSession, tag_id: UUID) -> Tag:
    """Soft-delete a tag."""
    return await update_tag(db, tag_id, is_active=False)


async def list_tags(db: AsyncSession, tag_type: TagType | None = None) -> list[Tag]:
    """List all tags, optionally filtered by type."""
    stmt = select(Tag).order_by(Tag.tag_type, Tag.name)
    if tag_type is not None:
        stmt = stmt.where(Tag.tag_type == tag_type)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_tag_executive_count(db: AsyncSession, tag_id: UUID) -> int:
    """Count active executives assigned to a tag."""
    result = await db.execute(
        select(func.count()).select_from(ExecutiveTag).where(
            ExecutiveTag.tag_id == tag_id,
            ExecutiveTag.is_active == True,
        )
    )
    return result.scalar() or 0


# ═══════════════════════════════════════════════════════════════
# ASSIGNMENT
# ═══════════════════════════════════════════════════════════════


async def assign_tag_to_executive(
    db: AsyncSession,
    executive_id: UUID,
    tag_id: UUID,
    assigned_by: UUID,
) -> ExecutiveTag:
    """Assign a tag to an executive. Raises 409 if already assigned."""
    # Verify executive exists and has EXECUTIVE role
    exec_result = await db.execute(
        select(User).where(User.id == executive_id, User.role == UserRole.EXECUTIVE)
    )
    executive = exec_result.scalar_one_or_none()
    if not executive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Executive not found")

    # Verify tag exists and is active
    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id, Tag.is_active == True))
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found or inactive")

    # Check existing assignment
    existing = await db.execute(
        select(ExecutiveTag).where(
            ExecutiveTag.executive_id == executive_id,
            ExecutiveTag.tag_id == tag_id,
        )
    )
    existing_assignment = existing.scalar_one_or_none()

    if existing_assignment:
        if existing_assignment.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tag already assigned to this executive",
            )
        # Reactivate
        existing_assignment.is_active = True
        existing_assignment.assigned_by = assigned_by
        await db.flush()
        await db.refresh(existing_assignment)
        return existing_assignment

    assignment = ExecutiveTag(
        executive_id=executive_id,
        tag_id=tag_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)
    await db.flush()
    await db.refresh(assignment)
    return assignment


async def bulk_assign_tag(
    db: AsyncSession,
    executive_ids: list[UUID],
    tag_id: UUID,
    assigned_by: UUID,
) -> list[ExecutiveTag]:
    """Assign a tag to multiple executives. Skips already-assigned ones."""
    results = []
    for exec_id in executive_ids:
        try:
            assignment = await assign_tag_to_executive(db, exec_id, tag_id, assigned_by)
            results.append(assignment)
        except HTTPException as e:
            if e.status_code == status.HTTP_409_CONFLICT:
                continue  # Skip already assigned
            raise
    return results


async def remove_tag_from_executive(
    db: AsyncSession,
    executive_id: UUID,
    tag_id: UUID,
) -> None:
    """Soft-remove a tag from an executive."""
    result = await db.execute(
        select(ExecutiveTag).where(
            ExecutiveTag.executive_id == executive_id,
            ExecutiveTag.tag_id == tag_id,
            ExecutiveTag.is_active == True,
        )
    )
    assignment = result.scalar_one_or_none()
    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag assignment not found",
        )
    assignment.is_active = False
    await db.flush()


async def get_executive_tags(db: AsyncSession, executive_id: UUID) -> dict:
    """Get all active tags for a specific executive, grouped by type."""
    result = await db.execute(
        select(ExecutiveTag, Tag).join(Tag, ExecutiveTag.tag_id == Tag.id).where(
            ExecutiveTag.executive_id == executive_id,
            ExecutiveTag.is_active == True,
            Tag.is_active == True,
        )
    )
    rows = result.all()

    manager_tags = []
    location_tags = []
    for et, tag in rows:
        tag_brief = {"id": tag.id, "name": tag.name, "tag_type": tag.tag_type}
        if tag.tag_type == TagType.MANAGER:
            manager_tags.append(tag_brief)
        elif tag.tag_type == TagType.LOCATION:
            location_tags.append(tag_brief)

    return {"manager_tags": manager_tags, "location_tags": location_tags}


async def get_all_executives_with_tags(db: AsyncSession) -> list[dict]:
    """Get all executives with their tags. Used by Partner."""
    exec_result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE).order_by(User.full_name)
    )
    executives = exec_result.scalars().all()

    items = []
    for ex in executives:
        tags_data = await get_executive_tags(db, ex.id)
        items.append({
            "executive_id": ex.id,
            "executive_name": ex.full_name,
            "manager_tags": tags_data["manager_tags"],
            "location_tags": tags_data["location_tags"],
        })
    return items


# ═══════════════════════════════════════════════════════════════
# ANALYTICS
# ═══════════════════════════════════════════════════════════════


async def _get_filing_stats_for_executive(db: AsyncSession, executive_id: UUID) -> dict:
    """Get filing counts for a single executive."""
    result = await db.execute(
        select(ITRFiling.status, func.count()).where(
            ITRFiling.assigned_executive_id == executive_id,
        ).group_by(ITRFiling.status)
    )
    rows = result.all()

    total = 0
    active = 0
    completed = 0
    halted = 0
    for filing_status, count in rows:
        total += count
        if filing_status == FilingStatus.COMPLETED:
            completed += count
        elif filing_status == FilingStatus.HALTED:
            halted += count
        else:
            active += count

    return {
        "total_filings": total,
        "active_filings": active,
        "completed_filings": completed,
        "halted_filings": halted,
    }


async def _get_executives_for_tag(db: AsyncSession, tag_id: UUID) -> list[dict]:
    """Get all active executives assigned to a tag with their filing stats."""
    result = await db.execute(
        select(ExecutiveTag, User).join(User, ExecutiveTag.executive_id == User.id).where(
            ExecutiveTag.tag_id == tag_id,
            ExecutiveTag.is_active == True,
        )
    )
    rows = result.all()

    executives = []
    for et, user in rows:
        stats = await _get_filing_stats_for_executive(db, user.id)
        executives.append({
            "executive_id": user.id,
            "executive_name": user.full_name,
            "active_filings": stats["active_filings"],
            "completed_filings": stats["completed_filings"],
            "total_filings": stats["total_filings"],
        })
    return executives


async def get_manager_summary(db: AsyncSession) -> list[dict]:
    """Get summary for all manager tags with executive counts and filing stats."""
    tags_result = await db.execute(
        select(Tag).where(Tag.tag_type == TagType.MANAGER, Tag.is_active == True).order_by(Tag.name)
    )
    manager_tags = tags_result.scalars().all()

    items = []
    for tag in manager_tags:
        executives = await _get_executives_for_tag(db, tag.id)
        total_filings = sum(e["total_filings"] for e in executives)
        active_filings = sum(e["active_filings"] for e in executives)
        completed_filings = sum(e["completed_filings"] for e in executives)
        halted = total_filings - active_filings - completed_filings

        items.append({
            "tag_id": tag.id,
            "manager_name": tag.name,
            "executive_count": len(executives),
            "total_filings": total_filings,
            "active_filings": active_filings,
            "completed_filings": completed_filings,
            "halted_filings": halted,
            "executives": executives,
        })
    return items


async def get_location_summary(db: AsyncSession) -> list[dict]:
    """Get summary for all location tags with executive and manager counts."""
    tags_result = await db.execute(
        select(Tag).where(Tag.tag_type == TagType.LOCATION, Tag.is_active == True).order_by(Tag.name)
    )
    location_tags = tags_result.scalars().all()

    items = []
    for tag in location_tags:
        # Get executives in this location
        exec_result = await db.execute(
            select(ExecutiveTag.executive_id).where(
                ExecutiveTag.tag_id == tag.id,
                ExecutiveTag.is_active == True,
            )
        )
        executive_ids = [row[0] for row in exec_result.all()]

        # Count distinct managers for executives in this location
        manager_count = 0
        if executive_ids:
            mgr_result = await db.execute(
                select(func.count(func.distinct(ExecutiveTag.tag_id))).where(
                    ExecutiveTag.executive_id.in_(executive_ids),
                    ExecutiveTag.is_active == True,
                    ExecutiveTag.tag_id.in_(
                        select(Tag.id).where(Tag.tag_type == TagType.MANAGER, Tag.is_active == True)
                    ),
                )
            )
            manager_count = mgr_result.scalar() or 0

        # Aggregate filing stats
        total_filings = 0
        active_filings = 0
        completed_filings = 0
        halted_filings = 0
        for exec_id in executive_ids:
            stats = await _get_filing_stats_for_executive(db, exec_id)
            total_filings += stats["total_filings"]
            active_filings += stats["active_filings"]
            completed_filings += stats["completed_filings"]
            halted_filings += stats["halted_filings"]

        items.append({
            "tag_id": tag.id,
            "location_name": tag.name,
            "executive_count": len(executive_ids),
            "manager_count": manager_count,
            "total_filings": total_filings,
            "active_filings": active_filings,
            "completed_filings": completed_filings,
            "halted_filings": halted_filings,
        })
    return items


async def get_hierarchy_summary(db: AsyncSession) -> list[dict]:
    """Build full hierarchy: Location → Managers → Executives → filing stats."""
    # Get all location tags
    loc_result = await db.execute(
        select(Tag).where(Tag.tag_type == TagType.LOCATION, Tag.is_active == True).order_by(Tag.name)
    )
    location_tags = loc_result.scalars().all()

    hierarchy = []
    for loc_tag in location_tags:
        # Get executive IDs in this location
        exec_in_loc = await db.execute(
            select(ExecutiveTag.executive_id).where(
                ExecutiveTag.tag_id == loc_tag.id,
                ExecutiveTag.is_active == True,
            )
        )
        location_exec_ids = set(row[0] for row in exec_in_loc.all())

        if not location_exec_ids:
            hierarchy.append({
                "tag_id": loc_tag.id,
                "location_name": loc_tag.name,
                "managers": [],
                "total_executives": 0,
                "total_filings": 0,
                "completed_filings": 0,
            })
            continue

        # Get all manager tags that have executives in this location
        mgr_tags_result = await db.execute(
            select(Tag).where(
                Tag.tag_type == TagType.MANAGER,
                Tag.is_active == True,
                Tag.id.in_(
                    select(ExecutiveTag.tag_id).where(
                        ExecutiveTag.executive_id.in_(location_exec_ids),
                        ExecutiveTag.is_active == True,
                        ExecutiveTag.tag_id.in_(
                            select(Tag.id).where(Tag.tag_type == TagType.MANAGER)
                        ),
                    )
                ),
            ).order_by(Tag.name)
        )
        mgr_tags = mgr_tags_result.scalars().all()

        managers_list = []
        loc_total_filings = 0
        loc_completed_filings = 0

        for mgr_tag in mgr_tags:
            # Executives who have BOTH this location AND this manager tag
            mgr_exec_result = await db.execute(
                select(ExecutiveTag.executive_id).where(
                    ExecutiveTag.tag_id == mgr_tag.id,
                    ExecutiveTag.is_active == True,
                    ExecutiveTag.executive_id.in_(location_exec_ids),
                )
            )
            mgr_exec_ids = [row[0] for row in mgr_exec_result.all()]

            mgr_executives = []
            mgr_total = 0
            mgr_completed = 0
            for exec_id in mgr_exec_ids:
                # Get user info
                user_result = await db.execute(select(User).where(User.id == exec_id))
                user = user_result.scalar_one_or_none()
                if not user:
                    continue
                stats = await _get_filing_stats_for_executive(db, exec_id)
                mgr_executives.append({
                    "executive_id": user.id,
                    "executive_name": user.full_name,
                    "active_filings": stats["active_filings"],
                    "completed_filings": stats["completed_filings"],
                    "total_filings": stats["total_filings"],
                })
                mgr_total += stats["total_filings"]
                mgr_completed += stats["completed_filings"]

            managers_list.append({
                "tag_id": mgr_tag.id,
                "manager_name": mgr_tag.name,
                "executives": mgr_executives,
                "total_filings": mgr_total,
                "completed_filings": mgr_completed,
            })
            loc_total_filings += mgr_total
            loc_completed_filings += mgr_completed

        hierarchy.append({
            "tag_id": loc_tag.id,
            "location_name": loc_tag.name,
            "managers": managers_list,
            "total_executives": len(location_exec_ids),
            "total_filings": loc_total_filings,
            "completed_filings": loc_completed_filings,
        })

    return hierarchy


async def get_manager_detail(db: AsyncSession, tag_id: UUID) -> dict:
    """Detailed view for a single manager tag."""
    tag_result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.tag_type == TagType.MANAGER)
    )
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager tag not found")

    executives = await _get_executives_for_tag(db, tag.id)
    executive_ids = [e["executive_id"] for e in executives]

    # Get recent filings for these executives
    recent_filings = []
    if executive_ids:
        filings_result = await db.execute(
            select(ITRFiling, User).join(User, ITRFiling.client_id == User.id).where(
                ITRFiling.assigned_executive_id.in_(executive_ids),
            ).order_by(ITRFiling.updated_at.desc()).limit(20)
        )
        for filing, client in filings_result.all():
            recent_filings.append({
                "filing_id": filing.id,
                "client_name": client.full_name,
                "financial_year": filing.financial_year,
                "status": filing.status.value,
                "last_updated": filing.updated_at,
            })

    total_filings = sum(e["total_filings"] for e in executives)
    active_filings = sum(e["active_filings"] for e in executives)
    completed_filings = sum(e["completed_filings"] for e in executives)
    halted = total_filings - active_filings - completed_filings

    return {
        "tag_id": tag.id,
        "manager_name": tag.name,
        "executive_count": len(executives),
        "total_filings": total_filings,
        "active_filings": active_filings,
        "completed_filings": completed_filings,
        "halted_filings": halted,
        "executives": executives,
        "recent_filings": recent_filings,
    }


async def get_location_detail(db: AsyncSession, tag_id: UUID) -> dict:
    """Detailed view for a single location tag."""
    tag_result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.tag_type == TagType.LOCATION)
    )
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Location tag not found")

    executives = await _get_executives_for_tag(db, tag.id)
    executive_ids = [e["executive_id"] for e in executives]

    # Count distinct managers
    manager_count = 0
    if executive_ids:
        mgr_result = await db.execute(
            select(func.count(func.distinct(ExecutiveTag.tag_id))).where(
                ExecutiveTag.executive_id.in_(executive_ids),
                ExecutiveTag.is_active == True,
                ExecutiveTag.tag_id.in_(
                    select(Tag.id).where(Tag.tag_type == TagType.MANAGER, Tag.is_active == True)
                ),
            )
        )
        manager_count = mgr_result.scalar() or 0

    # Recent filings
    recent_filings = []
    if executive_ids:
        filings_result = await db.execute(
            select(ITRFiling, User).join(User, ITRFiling.client_id == User.id).where(
                ITRFiling.assigned_executive_id.in_(executive_ids),
            ).order_by(ITRFiling.updated_at.desc()).limit(20)
        )
        for filing, client in filings_result.all():
            recent_filings.append({
                "filing_id": filing.id,
                "client_name": client.full_name,
                "financial_year": filing.financial_year,
                "status": filing.status.value,
                "last_updated": filing.updated_at,
            })

    total_filings = sum(e["total_filings"] for e in executives)
    active_filings = sum(e["active_filings"] for e in executives)
    completed_filings = sum(e["completed_filings"] for e in executives)
    halted = total_filings - active_filings - completed_filings

    return {
        "tag_id": tag.id,
        "location_name": tag.name,
        "executive_count": len(executives),
        "manager_count": manager_count,
        "total_filings": total_filings,
        "active_filings": active_filings,
        "completed_filings": completed_filings,
        "halted_filings": halted,
        "executives": executives,
        "recent_filings": recent_filings,
    }
