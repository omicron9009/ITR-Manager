"""Service — Internal Working document helpers."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import InternalWorkingDocType, MANDATORY_INTERNAL_WORKING_TYPES
from app.models.internal_working_doc import InternalWorkingDoc


async def check_mandatory_internal_workings(db: AsyncSession, filing_id: UUID) -> tuple[bool, set[InternalWorkingDocType]]:
    """Check if all mandatory internal working doc types are uploaded (active, non-superseded).

    Returns:
        (all_ready, missing_types) — all_ready is True when AIS, TIS, and 26AS all have
        at least one active doc. missing_types lists what's still needed.

    Backward compatibility: If there are legacy docs with doc_type=NULL (uploaded before
    this feature), we consider the mandatory requirement satisfied (grandfathered).
    """
    result = await db.execute(
        select(InternalWorkingDoc.doc_type)
        .where(
            InternalWorkingDoc.filing_id == filing_id,
            InternalWorkingDoc.superseded_at.is_(None),
        )
    )
    active_doc_types = [row[0] for row in result.all()]

    # Grandfathering: if ANY active doc has NULL doc_type (legacy), consider it satisfied
    if any(dt is None for dt in active_doc_types):
        return True, set()

    # No internal docs at all
    if not active_doc_types:
        return False, MANDATORY_INTERNAL_WORKING_TYPES.copy()

    # Check which mandatory types are present
    present_types = set(active_doc_types)
    missing = MANDATORY_INTERNAL_WORKING_TYPES - present_types

    return len(missing) == 0, missing
