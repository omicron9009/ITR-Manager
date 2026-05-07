"""Service — Filing state machine transitions."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    DuplicateFilingError,
    ExecutiveNotAssignedError,
    InvalidStateTransitionError,
)
from app.enums import AuditEventType, FilingStatus, VALID_FILING_TRANSITIONS
from app.models.filing import ITRFiling
from app.models.filing_state_history import FilingStateHistory
from app.services.audit_service import _sanitize_ascii, record_audit_event


def calculate_progress_percentage(status: FilingStatus) -> int:
    """Calculate progress percentage based on filing status."""
    progress_map = {
        FilingStatus.INITIATED: 10,
        FilingStatus.ON_BOARDING: 25,
        FilingStatus.PROCESSING: 40,
        FilingStatus.COMPUTATION: 60,
        FilingStatus.FILING: 75,
        FilingStatus.PAYMENT: 90,
        FilingStatus.COMPLETED: 100,
        FilingStatus.HALTED: 0,
    }
    return progress_map.get(status, 0)


async def check_duplicate_filing(db: AsyncSession, client_id: UUID, financial_year: str) -> None:
    """Check if a filing already exists for this client + FY combination."""
    result = await db.execute(
        select(ITRFiling).where(
            ITRFiling.client_id == client_id,
            ITRFiling.financial_year == financial_year,
        )
    )
    if result.scalar_one_or_none():
        raise DuplicateFilingError(financial_year)


async def transition_filing_status(
    db: AsyncSession,
    filing: ITRFiling,
    to_status: FilingStatus,
    changed_by: UUID,
    remarks: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> ITRFiling:
    """
    Transition a filing to a new status with validation.
    Records state history and audit log.
    """
    from_status = filing.status

    # Validate transition
    if from_status == FilingStatus.COMPLETED:
        raise InvalidStateTransitionError(from_status.value, to_status.value)

    valid_targets = VALID_FILING_TRANSITIONS.get(from_status, [])
    if to_status not in valid_targets:
        raise InvalidStateTransitionError(from_status.value, to_status.value)

    # Special validation: ON_BOARDING requires an assigned executive
    if to_status == FilingStatus.ON_BOARDING and not filing.assigned_executive_id:
        raise ExecutiveNotAssignedError()

    # Update filing status
    filing.status = to_status
    filing.updated_by = changed_by

    # Set milestone timestamps
    now = datetime.utcnow()
    if to_status == FilingStatus.ON_BOARDING:
        filing.onboarding_completed_at = now
    elif to_status == FilingStatus.COMPUTATION:
        filing.documents_approved_at = now
    elif to_status == FilingStatus.FILING:
        filing.computation_approved_at = now
    elif to_status == FilingStatus.PAYMENT:
        filing.filed_at = now
    elif to_status == FilingStatus.COMPLETED:
        filing.payment_received_at = now
        filing.completed_at = now
    elif to_status == FilingStatus.HALTED:
        filing.halted_at = now
        filing.halted_by = changed_by

    # Record state history
    history = FilingStateHistory(
        filing_id=filing.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        remarks=_sanitize_ascii(remarks) if remarks else remarks,
    )
    db.add(history)

    # Record audit log
    await record_audit_event(
        db=db,
        event_type=AuditEventType.FILING_STATE_CHANGED,
        actor_id=changed_by,
        client_id=filing.client_id,
        filing_id=filing.id,
        details={
            "from_status": from_status.value,
            "to_status": to_status.value,
            "remarks": remarks,
        },
        ip_address=ip_address,
    )

    await db.flush()
    return filing
