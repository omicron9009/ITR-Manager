"""API v1 — Dashboard endpoints (Partner, Executive, Client views)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_executive_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AccountStatus, DocumentStatus, FilingStatus, UserRole
from app.models.client_profile import ClientProfile
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.filing_computation import FilingComputation
from app.models.filing_completed_doc import FilingCompletedDoc
from app.models.filing_document import FilingDocument
from app.models.notification import Notification
from app.models.stored_file import StoredFile
from app.models.user import User
from app.schemas.dashboard import (
    ClientDashboardResponse,
    ClientFilingOverview,
    DashboardSummaryResponse,
    DirectoryComputationItem,
    DirectoryCompletedDocItem,
    DirectoryDocumentItem,
    ExecutiveWorkloadItem,
    ExecutiveWorkloadResponse,
    FilingDirectoryResponse,
    FilingDrillDownItem,
    FilingDrillDownResponse,
    FilingStatusCounter,
    PendingVerificationItem,
    PendingVerificationResponse,
)
from app.models.master_document_type import MasterDocumentType
from app.services.filing_service import calculate_progress_percentage

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# PARTNER / EXECUTIVE DASHBOARD
# ═══════════════════════════════════════════════════════════════


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Get dashboard summary with filing counters.
    - Partner: all filings
    - Executive: only assigned client filings
    """
    # Build base query depending on role
    base_filter = []
    if current_user.role == UserRole.EXECUTIVE:
        base_filter.append(ITRFiling.assigned_executive_id == current_user.id)

    # Filing status counts
    counters = []
    for status in FilingStatus:
        if status == FilingStatus.HALTED:
            continue
        count_query = select(func.count()).select_from(ITRFiling).where(
            ITRFiling.status == status, *base_filter
        )
        result = await db.execute(count_query)
        count = result.scalar() or 0
        counters.append(FilingStatusCounter(
            status=status,
            count=count,
            label=status.value.replace("_", " ").title(),
        ))

    # Total clients
    if current_user.role == UserRole.PARTNER:
        client_count_result = await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.CLIENT)
        )
    else:
        client_count_result = await db.execute(
            select(func.count()).select_from(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == current_user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
    total_clients = client_count_result.scalar() or 0

    # Pending verification count
    pending_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.PENDING_VERIFICATION,
        )
    )
    pending_count = pending_result.scalar() or 0

    # Total active filings
    active_result = await db.execute(
        select(func.count()).select_from(ITRFiling).where(
            ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            *base_filter,
        )
    )
    total_active = active_result.scalar() or 0

    return DashboardSummaryResponse(
        counters=counters,
        total_clients=total_clients,
        pending_verification_count=pending_count,
        total_active_filings=total_active,
    )


# ─── GET /dashboard/pending-verification (Partner Only) ─────
@router.get("/pending-verification", response_model=PendingVerificationResponse)
async def get_pending_verifications(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get list of clients awaiting verification (Partner only)."""
    result = await db.execute(
        select(User).where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.PENDING_VERIFICATION,
        ).order_by(User.created_at.desc())
    )
    clients = result.scalars().all()

    items = [
        PendingVerificationItem(
            id=c.id,
            full_name=c.full_name,
            email=c.email,
            pan_document_id=c.pan_document_id,
            registered_at=c.created_at,
        )
        for c in clients
    ]

    return PendingVerificationResponse(items=items, total=len(items))


# ─── GET /dashboard/filings-by-status ───────────────────────
@router.get("/filings-by-status", response_model=FilingDrillDownResponse)
async def get_filings_by_status(
    status_filter: FilingStatus = Query(..., alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get drill-down list of filings filtered by status."""
    query = select(ITRFiling).where(ITRFiling.status == status_filter)

    if current_user.role == UserRole.EXECUTIVE:
        query = query.where(ITRFiling.assigned_executive_id == current_user.id)

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    query = query.order_by(ITRFiling.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    filings = result.scalars().all()

    items = []
    for filing in filings:
        client_result = await db.execute(select(User).where(User.id == filing.client_id))
        client = client_result.scalar_one_or_none()

        exec_name = None
        if filing.assigned_executive_id:
            exec_result = await db.execute(select(User).where(User.id == filing.assigned_executive_id))
            exec_user = exec_result.scalar_one_or_none()
            exec_name = exec_user.full_name if exec_user else None

        items.append(FilingDrillDownItem(
            filing_id=filing.id,
            client_id=filing.client_id,
            client_name=client.full_name if client else "Unknown",
            client_email=client.email if client else "",
            financial_year=filing.financial_year,
            status=filing.status,
            assigned_executive_name=exec_name,
            last_updated=filing.updated_at,
        ))

    return FilingDrillDownResponse(status=status_filter, items=items, total=total)


# ─── GET /dashboard/executive-workload (Partner Only) ────────
@router.get("/executive-workload", response_model=ExecutiveWorkloadResponse)
async def get_executive_workload(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get workload summary for all executives (Partner only)."""
    result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE, User.is_active == True)
    )
    executives = result.scalars().all()

    items = []
    for exec_user in executives:
        client_count_result = await db.execute(
            select(func.count()).select_from(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == exec_user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        client_count = client_count_result.scalar() or 0

        filing_count_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == exec_user.id,
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            )
        )
        filing_count = filing_count_result.scalar() or 0

        items.append(ExecutiveWorkloadItem(
            executive_id=exec_user.id,
            executive_name=exec_user.full_name,
            assigned_clients=client_count,
            active_filings=filing_count,
        ))

    return ExecutiveWorkloadResponse(items=items)


# ═══════════════════════════════════════════════════════════════
# CLIENT DASHBOARD
# ═══════════════════════════════════════════════════════════════


@router.get("/client", response_model=ClientDashboardResponse)
async def get_client_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the client's dashboard data."""
    client_id = current_user.id if current_user.role == UserRole.CLIENT else None
    if not client_id:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Use /dashboard/summary for admin")

    # Get profile
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == client_id)
    )
    profile = profile_result.scalar_one_or_none()

    # Get filings
    filings_result = await db.execute(
        select(ITRFiling).where(ITRFiling.client_id == client_id).order_by(ITRFiling.financial_year.desc())
    )
    filings = filings_result.scalars().all()

    filing_overviews = []
    for filing in filings:
        # Get document counts
        doc_counts = await db.execute(
            select(
                func.count(FilingDocument.id).label("total"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.APPROVED).label("approved"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.PENDING_UPLOAD).label("pending"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.REJECTED).label("rejected"),
            ).where(FilingDocument.filing_id == filing.id)
        )
        counts = doc_counts.one()

        filing_overviews.append(ClientFilingOverview(
            filing_id=filing.id,
            financial_year=filing.financial_year,
            status=filing.status,
            progress_percentage=calculate_progress_percentage(filing.status),
            initiated_at=filing.initiated_at,
            last_updated=filing.updated_at,
            documents_total=counts.total or 0,
            documents_approved=counts.approved or 0,
            documents_pending=counts.pending or 0,
            documents_rejected=counts.rejected or 0,
        ))

    # Unread notifications
    unread_result = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == client_id, Notification.is_read == False
        )
    )
    unread_count = unread_result.scalar() or 0

    return ClientDashboardResponse(
        account_status=current_user.account_status.value,
        full_name=current_user.full_name,
        email=current_user.email,
        pan_number=profile.pan_number if profile else None,
        registered_at=current_user.created_at,
        active_filings=filing_overviews,
        unread_notification_count=unread_count,
    )


# ═══════════════════════════════════════════════════════════════
# DIRECTORY VIEW (per Filing)
# ═══════════════════════════════════════════════════════════════


@router.get("/directory/{filing_id}", response_model=FilingDirectoryResponse)
async def get_filing_directory(
    filing_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the directory view for a specific filing (documents, computations, completed docs)."""
    filing_result = await db.execute(select(ITRFiling).where(ITRFiling.id == filing_id))
    filing = filing_result.scalar_one_or_none()
    if not filing:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Filing not found")

    await enforce_client_access(db, current_user, filing.client_id)

    # Documents Required
    docs_result = await db.execute(
        select(FilingDocument).where(FilingDocument.filing_id == filing_id)
    )
    docs = docs_result.scalars().all()

    doc_items = []
    for doc in docs:
        type_result = await db.execute(select(MasterDocumentType).where(MasterDocumentType.id == doc.document_type_id))
        doc_type = type_result.scalar_one_or_none()

        filename = None
        if doc.file_id:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == doc.file_id))
            stored = file_result.scalar_one_or_none()
            filename = stored.original_filename if stored else None

        doc_items.append(DirectoryDocumentItem(
            id=doc.id,
            document_type_name=doc_type.name if doc_type else "Unknown",
            status=doc.status.value,
            file_id=doc.file_id,
            original_filename=filename,
            uploaded_at=doc.uploaded_at,
        ))

    # Computations
    comps_result = await db.execute(
        select(FilingComputation)
        .where(FilingComputation.filing_id == filing_id)
        .order_by(FilingComputation.version.desc())
    )
    comps = comps_result.scalars().all()

    comp_items = []
    for comp in comps:
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == comp.file_id))
        stored = file_result.scalar_one_or_none()
        comp_items.append(DirectoryComputationItem(
            id=comp.id,
            version=comp.version,
            status=comp.status.value,
            original_filename=stored.original_filename if stored else None,
            uploaded_at=comp.uploaded_at,
        ))

    # Completed Docs (only visible if filing is COMPLETED or user is Partner/Executive)
    completed_items = []
    if filing.status == FilingStatus.COMPLETED or current_user.role in (UserRole.PARTNER, UserRole.EXECUTIVE):
        completed_result = await db.execute(
            select(FilingCompletedDoc).where(FilingCompletedDoc.filing_id == filing_id)
        )
        completed_docs = completed_result.scalars().all()

        for cd in completed_docs:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == cd.file_id))
            stored = file_result.scalar_one_or_none()
            completed_items.append(DirectoryCompletedDocItem(
                id=cd.id,
                doc_type=cd.doc_type.value,
                original_filename=stored.original_filename if stored else None,
                uploaded_at=cd.uploaded_at,
            ))

    return FilingDirectoryResponse(
        filing_id=filing.id,
        financial_year=filing.financial_year,
        status=filing.status,
        documents_required=doc_items,
        computations=comp_items,
        completed_docs=completed_items,
    )
