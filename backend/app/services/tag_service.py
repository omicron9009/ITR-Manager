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


async def get_tag_client_count(db: AsyncSession, tag_id: UUID) -> int:
    """Count clients assigned to a partner tag."""
    from app.models.client_profile import ClientProfile
    result = await db.execute(
        select(func.count()).select_from(ClientProfile).where(
            ClientProfile.partner_tag_id == tag_id,
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

    # PARTNER tags can only be assigned to clients, not executives
    if tag.tag_type == TagType.PARTNER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="PARTNER tags can only be assigned to clients, not executives",
        )

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

    location_tags = []
    for et, tag in rows:
        tag_brief = {"id": tag.id, "name": tag.name, "tag_type": tag.tag_type}
        if tag.tag_type == TagType.LOCATION:
            location_tags.append(tag_brief)

    return {"location_tags": location_tags}


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


async def get_location_summary(db: AsyncSession) -> list[dict]:
    """Get summary for all location tags with executive counts and filing stats."""
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
            "total_filings": total_filings,
            "active_filings": active_filings,
            "completed_filings": completed_filings,
            "halted_filings": halted_filings,
        })
    return items


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
        "total_filings": total_filings,
        "active_filings": active_filings,
        "completed_filings": completed_filings,
        "halted_filings": halted,
        "executives": executives,
        "recent_filings": recent_filings,
    }


# ═══════════════════════════════════════════════════════════════
# PARTNER TAG ANALYTICS
# ═══════════════════════════════════════════════════════════════


async def _get_filing_stats_for_clients(db: AsyncSession, client_ids: list[UUID]) -> dict:
    """Get aggregate filing stats for a set of clients."""
    if not client_ids:
        return {"total_filings": 0, "active_filings": 0, "completed_filings": 0, "halted_filings": 0}

    result = await db.execute(
        select(ITRFiling.status, func.count()).where(
            ITRFiling.client_id.in_(client_ids),
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


async def get_partner_tag_summary(db: AsyncSession) -> list[dict]:
    """Get summary for all partner tags with client counts and filing stats."""
    from app.models.client_profile import ClientProfile

    tags_result = await db.execute(
        select(Tag).where(Tag.tag_type == TagType.PARTNER, Tag.is_active == True).order_by(Tag.name)
    )
    partner_tags = tags_result.scalars().all()

    items = []
    for tag in partner_tags:
        # Get client IDs with this partner tag
        client_result = await db.execute(
            select(ClientProfile.user_id).where(ClientProfile.partner_tag_id == tag.id)
        )
        client_ids = [row[0] for row in client_result.all()]

        stats = await _get_filing_stats_for_clients(db, client_ids)

        items.append({
            "tag_id": tag.id,
            "tag_name": tag.name,
            "client_count": len(client_ids),
            **stats,
        })
    return items


async def get_partner_tag_detail(db: AsyncSession, tag_id: UUID) -> dict:
    """Detailed view for a single partner tag: clients and recent filings."""
    from app.models.client_profile import ClientProfile

    tag_result = await db.execute(
        select(Tag).where(Tag.id == tag_id, Tag.tag_type == TagType.PARTNER)
    )
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Partner tag not found")

    # Get clients with this tag
    client_result = await db.execute(
        select(ClientProfile.user_id).where(ClientProfile.partner_tag_id == tag.id)
    )
    client_ids = [row[0] for row in client_result.all()]

    # Fetch client details
    clients_data = []
    if client_ids:
        users_result = await db.execute(
            select(User).where(User.id.in_(client_ids)).order_by(User.full_name)
        )
        users = users_result.scalars().all()

        # Batch-fetch latest active filing per client
        filings_result = await db.execute(
            select(ITRFiling.client_id, ITRFiling.financial_year, ITRFiling.status).where(
                ITRFiling.client_id.in_(client_ids),
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            ).order_by(ITRFiling.created_at.desc())
        )
        latest_filing_by_client: dict = {}
        for cid, fy, st in filings_result.all():
            if cid not in latest_filing_by_client:
                latest_filing_by_client[cid] = (fy, st)

        for user in users:
            latest = latest_filing_by_client.get(user.id)
            clients_data.append({
                "client_id": user.id,
                "client_name": user.full_name,
                "account_status": user.account_status.value,
                "active_filing_year": latest[0] if latest else None,
                "filing_status": latest[1].value if latest else None,
            })

    # Recent filings for these clients
    recent_filings = []
    if client_ids:
        filings_result = await db.execute(
            select(ITRFiling, User).join(User, ITRFiling.client_id == User.id).where(
                ITRFiling.client_id.in_(client_ids),
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

    stats = await _get_filing_stats_for_clients(db, client_ids)

    return {
        "tag_id": tag.id,
        "tag_name": tag.name,
        "client_count": len(client_ids),
        **stats,
        "clients": clients_data,
        "recent_filings": recent_filings,
    }


# ═══════════════════════════════════════════════════════════════
# MANAGER TAG ASSIGNMENTS
# ═══════════════════════════════════════════════════════════════


async def assign_tag_to_manager(
    db: AsyncSession, manager_id: UUID, tag_id: UUID, assigned_by: UUID
) -> "ManagerTag":
    """Assign a location tag to a manager. Reactivates if previously removed."""
    from app.models.manager_tag import ManagerTag

    # Validate tag exists and is LOCATION type
    tag_result = await db.execute(select(Tag).where(Tag.id == tag_id))
    tag = tag_result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    if tag.tag_type != TagType.LOCATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only LOCATION tags can be assigned to managers",
        )

    # Validate manager exists
    mgr_result = await db.execute(select(User).where(User.id == manager_id, User.role == UserRole.MANAGER))
    if not mgr_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Manager not found")

    # Check for existing (possibly deactivated) assignment
    existing_result = await db.execute(
        select(ManagerTag).where(
            ManagerTag.manager_id == manager_id,
            ManagerTag.tag_id == tag_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        if existing.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Tag already assigned to this manager",
            )
        existing.is_active = True
        existing.assigned_by = assigned_by
        from datetime import datetime, timezone
        existing.assigned_at = datetime.now(timezone.utc)
        await db.flush()
        return existing

    assignment = ManagerTag(
        manager_id=manager_id,
        tag_id=tag_id,
        assigned_by=assigned_by,
    )
    db.add(assignment)
    await db.flush()
    return assignment


async def remove_tag_from_manager(db: AsyncSession, manager_id: UUID, tag_id: UUID):
    """Remove a tag from a manager (soft-delete)."""
    from app.models.manager_tag import ManagerTag

    result = await db.execute(
        select(ManagerTag).where(
            ManagerTag.manager_id == manager_id,
            ManagerTag.tag_id == tag_id,
            ManagerTag.is_active == True,
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


async def get_manager_tags(db: AsyncSession, manager_id: UUID) -> list[dict]:
    """Get all active location tags for a manager."""
    from app.models.manager_tag import ManagerTag

    result = await db.execute(
        select(ManagerTag, Tag).join(Tag, ManagerTag.tag_id == Tag.id).where(
            ManagerTag.manager_id == manager_id,
            ManagerTag.is_active == True,
            Tag.is_active == True,
        )
    )
    rows = result.all()
    return [{"id": tag.id, "name": tag.name, "tag_type": tag.tag_type} for _, tag in rows]
