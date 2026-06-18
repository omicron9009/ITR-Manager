"""API v1 — Dashboard endpoints (Partner, Executive, Client views)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_manager_executive_or_partner, get_current_executive_or_partner, get_current_partner, get_current_user, get_current_dashboard_user_or_partner
from app.database import get_db
from app.enums import AccountStatus, CompletedDocStatus, CompletedDocType, ComputationStatus, DocumentStatus, FilingStatus, UserRole
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
    ClientAnalyticsResponse,
    ClientDashboardResponse,
    ClientFilingDetail,
    ClientFilingOverview,
    ClientStatusBreakdown,
    ComputationSubStateCounter,
    DashboardSummaryResponse,
    DirectoryComputationItem,
    DirectoryCompletedDocItem,
    DirectoryDocumentItem,
    DirectoryOtherDocItem,
    ExecutiveAnalyticsResponse,
    ExecutiveClientDetail,
    ExecutiveClientInfo,
    ExecutiveWorkloadItem,
    ExecutiveWorkloadResponse,
    FilingDirectoryResponse,
    FilingDocSubStateCounter,
    FilingDrillDownItem,
    FilingDrillDownResponse,
    FilingStatusBreakdown,
    FilingStatusClientInfo,
    FilingStatusCounter,
    FYDistribution,
    PartnerAnalyticsResponse,
    PendingVerificationItem,
    PendingVerificationResponse,
    CompletedQueueItem,
    CompletedQueueResponse,
    DismissQueueRequest,
)
from app.models.master_document_type import MasterDocumentType
from app.models.viewer_completed_queue import ViewerCompletedQueue
from app.services.filing_service import calculate_progress_percentage

router = APIRouter()


# ─── Helper: Role-aware computation label ────────────────────
_COMPUTATION_LABELS = {
    None: {
        UserRole.EXECUTIVE: "Pending Upload",
        UserRole.MANAGER: "Pending Executive Upload",
        UserRole.PARTNER: "Pending Upload",
    },
    ComputationStatus.UPLOADED: {
        UserRole.EXECUTIVE: "Manager Approval Pending",
        UserRole.MANAGER: "Your Approval Pending",
        UserRole.PARTNER: "Manager Approval Pending",
    },
    ComputationStatus.MANAGER_APPROVED: {
        UserRole.EXECUTIVE: "Partner Approval Pending",
        UserRole.MANAGER: "Partner Approval Pending",
        UserRole.PARTNER: "Your Approval Pending",
    },
    ComputationStatus.PARTNER_APPROVED: {
        UserRole.EXECUTIVE: "Client Approval Pending",
        UserRole.MANAGER: "Client Approval Pending",
        UserRole.PARTNER: "Client Approval Pending",
    },
    ComputationStatus.CLIENT_APPROVED: {
        UserRole.EXECUTIVE: "Approved",
        UserRole.MANAGER: "Approved",
        UserRole.PARTNER: "Approved",
    },
    ComputationStatus.REJECTED: {
        UserRole.EXECUTIVE: "Client Rejected — Re-upload",
        UserRole.MANAGER: "Client Rejected",
        UserRole.PARTNER: "Client Rejected",
    },
    ComputationStatus.MANAGER_REJECTED: {
        UserRole.EXECUTIVE: "Manager Rejected — Re-upload",
        UserRole.MANAGER: "Rejected by You",
        UserRole.PARTNER: "Manager Rejected",
    },
}


def get_computation_label(role: UserRole, comp_status: "ComputationStatus | None") -> str:
    """Return a role-aware human label for the computation sub-state."""
    role_map = _COMPUTATION_LABELS.get(comp_status)
    if role_map:
        return role_map.get(role, comp_status.value if comp_status else "Unknown")
    return comp_status.value if comp_status else "Unknown"


def get_computation_raw_status(comp_status: "ComputationStatus | None") -> str:
    """Return raw status string (NOT_UPLOADED when no computation exists)."""
    return comp_status.value if comp_status else "NOT_UPLOADED"


# ═══════════════════════════════════════════════════════════════
# PARTNER / EXECUTIVE DASHBOARD
# ═══════════════════════════════════════════════════════════════


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Get dashboard summary with filing counters.
    - Partner: all filings
    - Manager: filings of their team's clients
    - Executive: only assigned client filings
    """
    from app.config import settings as _settings
    from app.core.cache import NS, get_or_compute

    cache_key = f"{current_user.role.value}:{current_user.id}"

    async def _build() -> DashboardSummaryResponse:
        return await _build_dashboard_summary(db, current_user)

    return await get_or_compute(
        NS.DASHBOARD_SUMMARY,
        cache_key,
        _settings.CACHE_TTL_DASHBOARD,
        _build,
    )


async def _build_dashboard_summary(
    db: AsyncSession, current_user: User
) -> DashboardSummaryResponse:
    """Compute dashboard summary. Uses set-based queries — no N+1."""
    from collections import Counter as _Counter

    # ── Base filter (role-scoped) ──
    base_filter = []
    if current_user.role == UserRole.MANAGER:
        from app.services.manager_service import get_manager_team_client_ids
        team_client_ids = await get_manager_team_client_ids(db, current_user.id)
        if team_client_ids:
            base_filter.append(ITRFiling.client_id.in_(team_client_ids))
        else:
            base_filter.append(ITRFiling.client_id == None)  # No results
    elif current_user.role == UserRole.EXECUTIVE:
        base_filter.append(ITRFiling.assigned_executive_id == current_user.id)

    # ── Filing status counts (single GROUP BY query) ──
    status_count_result = await db.execute(
        select(ITRFiling.status, func.count(ITRFiling.id))
        .where(*base_filter)
        .group_by(ITRFiling.status)
    )
    status_count_map = {row[0]: row[1] for row in status_count_result.all()}

    counters = [
        FilingStatusCounter(
            status=st,
            count=status_count_map.get(st, 0),
            label=st.value.replace("_", " ").title(),
        )
        for st in FilingStatus
        if st != FilingStatus.HALTED
    ]

    # ── Total clients ──
    if current_user.role == UserRole.PARTNER:
        client_count_result = await db.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.CLIENT)
        )
    elif current_user.role == UserRole.MANAGER:
        from app.models.manager_client_assignment import ManagerClientAssignment
        client_count_result = await db.execute(
            select(func.count()).select_from(ManagerClientAssignment).where(
                ManagerClientAssignment.manager_id == current_user.id,
                ManagerClientAssignment.is_active == True,
            )
        )
    else:
        client_count_result = await db.execute(
            select(func.count()).select_from(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == current_user.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
    total_clients = client_count_result.scalar() or 0

    # ── Pending verification count (global; same across roles) ──
    pending_result = await db.execute(
        select(func.count()).select_from(User).where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.PENDING_VERIFICATION,
        )
    )
    pending_count = pending_result.scalar() or 0

    # ── Total active filings (derived from status_count_map) ──
    total_active = sum(
        cnt
        for st, cnt in status_count_map.items()
        if st not in (FilingStatus.COMPLETED, FilingStatus.HALTED)
    )

    # ── Computation sub-state counters (single window query — was N+1) ──
    comp_filing_ids_result = await db.execute(
        select(ITRFiling.id).where(ITRFiling.status == FilingStatus.COMPUTATION, *base_filter)
    )
    comp_filing_ids = [row[0] for row in comp_filing_ids_result.all()]

    sub_state_counts: _Counter = _Counter()
    if comp_filing_ids:
        # Latest computation per filing in ONE query (window function).
        rn = func.row_number().over(
            partition_by=FilingComputation.filing_id,
            order_by=FilingComputation.version.desc(),
        ).label("rn")
        subq = (
            select(FilingComputation.filing_id, FilingComputation.status, rn)
            .where(FilingComputation.filing_id.in_(comp_filing_ids))
            .subquery()
        )
        latest_result = await db.execute(
            select(subq.c.filing_id, subq.c.status).where(subq.c.rn == 1)
        )
        latest_map = {row[0]: row[1] for row in latest_result.all()}
        for fid in comp_filing_ids:
            sub_state_counts[latest_map.get(fid)] += 1  # None means not uploaded

    computation_sub_counters = [
        ComputationSubStateCounter(
            sub_status=get_computation_label(current_user.role, comp_st),
            raw_status=get_computation_raw_status(comp_st),
            count=cnt,
        )
        for comp_st, cnt in sub_state_counts.items()
    ]

    # ── Filing (completed docs) sub-state counters (single batch query — was N+1) ──
    filing_phase_ids_result = await db.execute(
        select(ITRFiling.id).where(ITRFiling.status == FilingStatus.FILING, *base_filter)
    )
    filing_phase_ids = [row[0] for row in filing_phase_ids_result.all()]

    filing_doc_sub_counts: _Counter = _Counter()
    required_doc_types = {
        CompletedDocType.ITR_ACKNOWLEDGEMENT,
        CompletedDocType.INVOICE,
        CompletedDocType.ITR_JSON,
        CompletedDocType.ITR_FORM,
        CompletedDocType.TAX_PAID_COMPUTATION,
    }

    if filing_phase_ids:
        from collections import defaultdict
        docs_by_filing: dict = defaultdict(list)
        rows = await db.execute(
            select(
                FilingCompletedDoc.filing_id,
                FilingCompletedDoc.doc_type,
                FilingCompletedDoc.status,
            ).where(FilingCompletedDoc.filing_id.in_(filing_phase_ids))
        )
        for fid, dtype, dstatus in rows.all():
            docs_by_filing[fid].append((dtype, dstatus))

        for fid in filing_phase_ids:
            docs = docs_by_filing.get(fid, [])
            existing_types = {d[0] for d in docs}

            if not required_doc_types.issubset(existing_types):
                filing_doc_sub_counts["PENDING_UPLOAD"] += 1
            else:
                statuses = [s for (t, s) in docs if t in required_doc_types]
                if any(s == CompletedDocStatus.MANAGER_REJECTED for s in statuses):
                    filing_doc_sub_counts["MANAGER_REJECTED"] += 1
                elif all(s == CompletedDocStatus.PARTNER_APPROVED for s in statuses):
                    filing_doc_sub_counts["ALL_PARTNER_APPROVED"] += 1
                elif any(s == CompletedDocStatus.MANAGER_APPROVED for s in statuses) or all(
                    s in (CompletedDocStatus.MANAGER_APPROVED, CompletedDocStatus.PARTNER_APPROVED)
                    for s in statuses
                ):
                    filing_doc_sub_counts["AWAITING_PARTNER_APPROVAL"] += 1
                else:
                    filing_doc_sub_counts["AWAITING_MANAGER_APPROVAL"] += 1

    _FILING_DOC_LABELS = {
        "PENDING_UPLOAD": "Pending Upload by Executive",
        "AWAITING_MANAGER_APPROVAL": "Awaiting Manager Approval",
        "AWAITING_PARTNER_APPROVAL": "Awaiting Partner Approval",
        "ALL_PARTNER_APPROVED": "Approved — Ready for Payment",
        "MANAGER_REJECTED": "Rejected — Re-upload Required",
    }

    filing_doc_sub_counters = [
        FilingDocSubStateCounter(
            sub_status=_FILING_DOC_LABELS.get(st, st),
            raw_status=st,
            count=cnt,
        )
        for st, cnt in filing_doc_sub_counts.items()
    ]

    return DashboardSummaryResponse(
        counters=counters,
        computation_sub_counters=computation_sub_counters,
        filing_doc_sub_counters=filing_doc_sub_counters,
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
        select(User, ClientProfile.referral_source, ClientProfile.referral_source_other, ClientProfile.city)
        .outerjoin(ClientProfile, ClientProfile.user_id == User.id)
        .where(
            User.role == UserRole.CLIENT,
            User.account_status == AccountStatus.PENDING_VERIFICATION,
        )
        .order_by(User.created_at.desc())
    )
    rows = result.all()

    items = [
        PendingVerificationItem(
            id=c.id,
            full_name=c.full_name,
            email=c.email,
            phone_number=c.phone_number,
            registered_at=c.created_at,
            referral_source=ref_source.value if ref_source else None,
            referral_source_other=ref_other,
            city=city,
        )
        for c, ref_source, ref_other, city in rows
    ]

    return PendingVerificationResponse(items=items, total=len(items))


# ─── GET /dashboard/filings-by-status ───────────────────────
@router.get("/filings-by-status", response_model=FilingDrillDownResponse)
async def get_filings_by_status(
    status_filter: FilingStatus = Query(..., alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get drill-down list of filings filtered by status."""
    query = select(ITRFiling).where(ITRFiling.status == status_filter)

    if current_user.role == UserRole.MANAGER:
        from app.services.manager_service import get_manager_team_client_ids
        team_client_ids = await get_manager_team_client_ids(db, current_user.id)
        if team_client_ids:
            query = query.where(ITRFiling.client_id.in_(team_client_ids))
        else:
            query = query.where(ITRFiling.client_id == None)
    elif current_user.role == UserRole.EXECUTIVE:
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

        # Computation sub-status (only relevant for COMPUTATION filings)
        comp_sub_label = None
        if status_filter == FilingStatus.COMPUTATION:
            latest_comp_result = await db.execute(
                select(FilingComputation.status)
                .where(FilingComputation.filing_id == filing.id)
                .order_by(FilingComputation.version.desc())
                .limit(1)
            )
            latest_comp_status = latest_comp_result.scalar()
            comp_sub_label = get_computation_label(current_user.role, latest_comp_status)

        items.append(FilingDrillDownItem(
            filing_id=filing.id,
            client_id=filing.client_id,
            client_name=client.full_name if client else "Unknown",
            client_email=client.email if client else "",
            financial_year=filing.financial_year,
            status=filing.status,
            assigned_executive_name=exec_name,
            computation_sub_status=comp_sub_label,
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

    # Completed Docs visibility rules:
    # - Client: only visible after COMPLETED, and only PARTNER_APPROVED docs
    # - Partner/Executive/Manager: always visible (all statuses)
    completed_items = []
    if current_user.role == UserRole.CLIENT:
        if filing.status == FilingStatus.COMPLETED:
            completed_result = await db.execute(
                select(FilingCompletedDoc).where(
                    FilingCompletedDoc.filing_id == filing_id,
                    FilingCompletedDoc.status == CompletedDocStatus.PARTNER_APPROVED,
                    FilingCompletedDoc.doc_type.in_([
                        CompletedDocType.ITR_ACKNOWLEDGEMENT,
                        CompletedDocType.INVOICE,
                        CompletedDocType.ITR_FORM,
                        CompletedDocType.FINANCIAL_STATEMENT,
                        CompletedDocType.TAX_PAID_COMPUTATION,
                    ]),
                )
            )
            completed_docs = completed_result.scalars().all()
        else:
            completed_docs = []
    elif current_user.role in (UserRole.PARTNER, UserRole.EXECUTIVE, UserRole.MANAGER):
        completed_result = await db.execute(
            select(FilingCompletedDoc).where(FilingCompletedDoc.filing_id == filing_id)
        )
        completed_docs = completed_result.scalars().all()
    else:
        completed_docs = []

    if completed_docs:
        for cd in completed_docs:
            file_result = await db.execute(select(StoredFile).where(StoredFile.id == cd.file_id))
            stored = file_result.scalar_one_or_none()
            completed_items.append(DirectoryCompletedDocItem(
                id=cd.id,
                doc_type=cd.doc_type.value,
                status=cd.status.value if cd.status else "UPLOADED",
                original_filename=stored.original_filename if stored else None,
                uploaded_at=cd.uploaded_at,
            ))

    # Other Docs visibility rules:
    # - Client: only visible after COMPLETED
    # - Partner/Executive: always visible
    from app.models.filing_other_doc import FilingOtherDoc

    other_doc_items = []
    if current_user.role == UserRole.CLIENT:
        if filing.status == FilingStatus.COMPLETED:
            other_result = await db.execute(
                select(FilingOtherDoc).where(FilingOtherDoc.filing_id == filing_id)
                .order_by(FilingOtherDoc.uploaded_at.desc())
            )
            other_docs = other_result.scalars().all()
        else:
            other_docs = []
    elif current_user.role in (UserRole.PARTNER, UserRole.EXECUTIVE):
        other_result = await db.execute(
            select(FilingOtherDoc).where(FilingOtherDoc.filing_id == filing_id)
            .order_by(FilingOtherDoc.uploaded_at.desc())
        )
        other_docs = other_result.scalars().all()
    else:
        other_docs = []

    for od in other_docs:
        file_result = await db.execute(select(StoredFile).where(StoredFile.id == od.file_id))
        stored = file_result.scalar_one_or_none()
        other_doc_items.append(DirectoryOtherDocItem(
            id=od.id,
            file_id=od.file_id,
            label=od.label,
            original_filename=stored.original_filename if stored else None,
            uploaded_at=od.uploaded_at,
        ))

    return FilingDirectoryResponse(
        filing_id=filing.id,
        financial_year=filing.financial_year,
        status=filing.status,
        documents_required=doc_items,
        computations=comp_items,
        completed_docs=completed_items,
        other_docs=other_doc_items,
    )


# ═══════════════════════════════════════════════════════════════
# ANALYTICS DASHBOARDS
# ═══════════════════════════════════════════════════════════════


@router.get("/analytics/partner", response_model=PartnerAnalyticsResponse)
async def get_partner_analytics(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive analytics dashboard for Partner."""
    from sqlalchemy import extract

    # ── Client counts by account status ──
    client_status_result = await db.execute(
        select(User.account_status, func.count(User.id))
        .where(User.role == UserRole.CLIENT)
        .group_by(User.account_status)
    )
    client_status_map = {row[0].value: row[1] for row in client_status_result.all()}
    total_clients = sum(client_status_map.values())
    active_clients = client_status_map.get("ACTIVE", 0)
    pending_clients = client_status_map.get("PENDING_VERIFICATION", 0)
    rejected_clients = client_status_map.get("REJECTED", 0)

    client_status_breakdown = [
        ClientStatusBreakdown(status=s, count=c)
        for s, c in client_status_map.items()
    ]

    # ── Executive counts ──
    exec_count_result = await db.execute(
        select(
            func.count(User.id).filter(User.is_active == True).label("active"),
            func.count(User.id).label("total"),
        ).where(User.role == UserRole.EXECUTIVE)
    )
    exec_counts = exec_count_result.one()

    # ── Filing counts by status ──
    filing_status_result = await db.execute(
        select(ITRFiling.status, func.count(ITRFiling.id))
        .group_by(ITRFiling.status)
    )
    filing_status_map = {row[0].value: row[1] for row in filing_status_result.all()}
    total_filings = sum(filing_status_map.values())
    completed_filings = filing_status_map.get("COMPLETED", 0)
    halted_filings = filing_status_map.get("HALTED", 0)
    active_filings = total_filings - completed_filings - halted_filings

    # ── Filing status breakdown with client details (batch) ──
    from collections import defaultdict
    all_status_filings_result = await db.execute(
        select(ITRFiling).order_by(ITRFiling.updated_at.desc())
    )
    all_status_filings = all_status_filings_result.scalars().all()
    _sb_user_ids = set()
    for _f in all_status_filings:
        _sb_user_ids.add(_f.client_id)
        if _f.assigned_executive_id:
            _sb_user_ids.add(_f.assigned_executive_id)
    if _sb_user_ids:
        _sb_names_result = await db.execute(select(User.id, User.full_name).where(User.id.in_(_sb_user_ids)))
        _sb_names = {row.id: row.full_name for row in _sb_names_result.all()}
    else:
        _sb_names = {}
    _by_status: dict = defaultdict(list)
    for _f in all_status_filings:
        _by_status[_f.status].append(_f)
    filing_status_breakdown = []
    for fs in FilingStatus:
        _filings_slice = _by_status[fs][:50]
        filing_status_breakdown.append(FilingStatusBreakdown(
            status=fs.value,
            count=filing_status_map.get(fs.value, 0),
            clients=[
                FilingStatusClientInfo(
                    client_id=_f.client_id,
                    client_name=_sb_names.get(_f.client_id, "Unknown"),
                    financial_year=_f.financial_year,
                    assigned_executive=_sb_names.get(_f.assigned_executive_id) if _f.assigned_executive_id else None,
                    last_updated=_f.updated_at,
                )
                for _f in _filings_slice
            ],
        ))

    # ── Executive → Client mapping (batch) ──
    exec_result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE).order_by(User.full_name)
    )
    executives = exec_result.scalars().all()

    executive_client_mapping = []
    if executives:
        _exec_ids = [ex.id for ex in executives]
        _all_assign_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id.in_(_exec_ids),
                ExecutiveClientAssignment.is_active == True,
            )
        )
        _all_assignments = _all_assign_result.scalars().all()
        _assign_by_exec: dict = defaultdict(list)
        for _a in _all_assignments:
            _assign_by_exec[_a.executive_id].append(_a)
        _all_client_ids = {_a.client_id for _a in _all_assignments}
        if _all_client_ids:
            _clients_result = await db.execute(select(User).where(User.id.in_(_all_client_ids)))
            _clients_map = {c.id: c for c in _clients_result.scalars().all()}
            _latest_filings_result = await db.execute(
                select(ITRFiling)
                .where(
                    ITRFiling.client_id.in_(_all_client_ids),
                    ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
                )
                .distinct(ITRFiling.client_id)
                .order_by(ITRFiling.client_id, ITRFiling.updated_at.desc())
            )
            _latest_filing_map = {f.client_id: f for f in _latest_filings_result.scalars().all()}
        else:
            _clients_map = {}
            _latest_filing_map = {}
        _exec_counts_result = await db.execute(
            select(
                ITRFiling.assigned_executive_id,
                func.count(ITRFiling.id).filter(
                    ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED])
                ).label("active"),
                func.count(ITRFiling.id).filter(
                    ITRFiling.status == FilingStatus.COMPLETED
                ).label("completed"),
            )
            .where(ITRFiling.assigned_executive_id.in_(_exec_ids))
            .group_by(ITRFiling.assigned_executive_id)
        )
        _exec_counts_map = {row.assigned_executive_id: (row.active or 0, row.completed or 0) for row in _exec_counts_result.all()}
        for ex in executives:
            _ex_assignments = _assign_by_exec.get(ex.id, [])
            _clients_list = []
            for _a in _ex_assignments:
                _cl = _clients_map.get(_a.client_id)
                if _cl:
                    _fl = _latest_filing_map.get(_cl.id)
                    _clients_list.append(ExecutiveClientInfo(
                        client_id=_cl.id,
                        client_name=_cl.full_name,
                        client_email=_cl.email,
                        filing_status=_fl.status.value if _fl else None,
                        financial_year=_fl.financial_year if _fl else None,
                    ))
            _active_c, _completed_c = _exec_counts_map.get(ex.id, (0, 0))
            executive_client_mapping.append(ExecutiveClientDetail(
                executive_id=ex.id,
                executive_name=ex.full_name,
                executive_email=ex.email,
                is_active=ex.is_active,
                clients=_clients_list,
                total_clients=len(_clients_list),
                active_filings=_active_c,
                completed_filings=_completed_c,
            ))

    # ── Unassigned clients ──
    assigned_ids_result = await db.execute(
        select(ExecutiveClientAssignment.client_id).where(ExecutiveClientAssignment.is_active == True)
    )
    assigned_ids = {row[0] for row in assigned_ids_result.all()}

    if assigned_ids:
        unassigned_result = await db.execute(
            select(User).where(
                User.role == UserRole.CLIENT,
                User.account_status == AccountStatus.ACTIVE,
                User.id.notin_(assigned_ids),
            )
        )
    else:
        unassigned_result = await db.execute(
            select(User).where(
                User.role == UserRole.CLIENT,
                User.account_status == AccountStatus.ACTIVE,
            )
        )
    unassigned_clients = [
        ExecutiveClientInfo(client_id=u.id, client_name=u.full_name, client_email=u.email)
        for u in unassigned_result.scalars().all()
    ]

    # ── FY Distribution ──
    fy_result = await db.execute(
        select(
            ITRFiling.financial_year,
            func.count(ITRFiling.id).label("total"),
            func.count(ITRFiling.id).filter(ITRFiling.status == FilingStatus.COMPLETED).label("completed"),
        )
        .group_by(ITRFiling.financial_year)
        .order_by(ITRFiling.financial_year.desc())
    )
    fy_distribution = [
        FYDistribution(
            financial_year=row.financial_year,
            total_filings=row.total,
            completed=row.completed,
            active=row.total - row.completed,
        )
        for row in fy_result.all()
    ]

    # ── Average processing times ──
    avg_completion_result = await db.execute(
        select(
            func.avg(extract('epoch', ITRFiling.completed_at - ITRFiling.initiated_at) / 86400)
        ).where(ITRFiling.completed_at.isnot(None))
    )
    avg_days_total = avg_completion_result.scalar()

    avg_processing_result = await db.execute(
        select(
            func.avg(extract('epoch', ITRFiling.documents_approved_at - ITRFiling.documents_submitted_at) / 86400)
        ).where(
            ITRFiling.documents_approved_at.isnot(None),
            ITRFiling.documents_submitted_at.isnot(None),
        )
    )
    avg_days_processing = avg_processing_result.scalar()

    avg_computation_result = await db.execute(
        select(
            func.avg(extract('epoch', ITRFiling.computation_approved_at - ITRFiling.computation_uploaded_at) / 86400)
        ).where(
            ITRFiling.computation_approved_at.isnot(None),
            ITRFiling.computation_uploaded_at.isnot(None),
        )
    )
    avg_days_computation = avg_computation_result.scalar()

    # ── Recent filings (last 10 state changes, batch) ──
    recent_result = await db.execute(
        select(ITRFiling).order_by(ITRFiling.updated_at.desc()).limit(10)
    )
    _recent_list = recent_result.scalars().all()
    _recent_ids: set = set()
    for _f in _recent_list:
        _recent_ids.add(_f.client_id)
        if _f.assigned_executive_id:
            _recent_ids.add(_f.assigned_executive_id)
    if _recent_ids:
        _recent_names_result = await db.execute(select(User.id, User.full_name).where(User.id.in_(_recent_ids)))
        _recent_names = {row.id: row.full_name for row in _recent_names_result.all()}
    else:
        _recent_names = {}
    recent_filings = [
        FilingStatusClientInfo(
            client_id=_f.client_id,
            client_name=_recent_names.get(_f.client_id, "Unknown"),
            financial_year=_f.financial_year,
            assigned_executive=_recent_names.get(_f.assigned_executive_id) if _f.assigned_executive_id else None,
            last_updated=_f.updated_at,
        )
        for _f in _recent_list
    ]

    return PartnerAnalyticsResponse(
        total_clients=total_clients,
        active_clients=active_clients,
        pending_verification_clients=pending_clients,
        rejected_clients=rejected_clients,
        total_executives=exec_counts.total,
        active_executives=exec_counts.active,
        total_filings=total_filings,
        active_filings=active_filings,
        completed_filings=completed_filings,
        halted_filings=halted_filings,
        executive_client_mapping=executive_client_mapping,
        unassigned_clients=unassigned_clients,
        filing_status_breakdown=filing_status_breakdown,
        client_status_breakdown=client_status_breakdown,
        fy_distribution=fy_distribution,
        avg_days_initiated_to_completed=round(avg_days_total, 1) if avg_days_total else None,
        avg_days_in_processing=round(avg_days_processing, 1) if avg_days_processing else None,
        avg_days_in_computation=round(avg_days_computation, 1) if avg_days_computation else None,
        recent_filings=recent_filings,
    )


@router.get("/analytics/executive", response_model=ExecutiveAnalyticsResponse)
async def get_executive_analytics(
    current_user: User = Depends(get_current_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Analytics dashboard for Executive (scoped to assigned clients)."""
    from sqlalchemy import extract

    executive_id = current_user.id

    # ── Assigned clients ──
    assign_result = await db.execute(
        select(ExecutiveClientAssignment).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    assignments = assign_result.scalars().all()

    _ex_client_ids = [a.client_id for a in assignments]
    if _ex_client_ids:
        _ex_clients_result = await db.execute(select(User).where(User.id.in_(_ex_client_ids)))
        _ex_clients_map = {c.id: c for c in _ex_clients_result.scalars().all()}
        _ex_latest_result = await db.execute(
            select(ITRFiling)
            .where(
                ITRFiling.client_id.in_(_ex_client_ids),
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            )
            .distinct(ITRFiling.client_id)
            .order_by(ITRFiling.client_id, ITRFiling.updated_at.desc())
        )
        _ex_latest_map = {f.client_id: f for f in _ex_latest_result.scalars().all()}
    else:
        _ex_clients_map = {}
        _ex_latest_map = {}
    clients_list = []
    for a in assignments:
        cl = _ex_clients_map.get(a.client_id)
        if cl:
            fl = _ex_latest_map.get(cl.id)
            clients_list.append(ExecutiveClientInfo(
                client_id=cl.id,
                client_name=cl.full_name,
                client_email=cl.email,
                filing_status=fl.status.value if fl else None,
                financial_year=fl.financial_year if fl else None,
            ))

    # ── Filing counts by status (scoped) ──
    filing_status_result = await db.execute(
        select(ITRFiling.status, func.count(ITRFiling.id))
        .where(ITRFiling.assigned_executive_id == executive_id)
        .group_by(ITRFiling.status)
    )
    filing_status_map = {row[0].value: row[1] for row in filing_status_result.all()}
    total_filings = sum(filing_status_map.values())
    completed_filings = filing_status_map.get("COMPLETED", 0)
    halted_filings = filing_status_map.get("HALTED", 0)
    active_filings = total_filings - completed_filings - halted_filings

    # ── Filing status breakdown with client details (batch) ──
    from collections import defaultdict
    _ex_all_filings_result = await db.execute(
        select(ITRFiling).where(ITRFiling.assigned_executive_id == executive_id)
        .order_by(ITRFiling.updated_at.desc())
    )
    _ex_all_filings = _ex_all_filings_result.scalars().all()
    _ex_filing_client_ids = {_f.client_id for _f in _ex_all_filings}
    if _ex_filing_client_ids:
        _ex_filing_names_result = await db.execute(
            select(User.id, User.full_name).where(User.id.in_(_ex_filing_client_ids))
        )
        _ex_filing_names = {row.id: row.full_name for row in _ex_filing_names_result.all()}
    else:
        _ex_filing_names = {}
    _ex_by_status: dict = defaultdict(list)
    for _f in _ex_all_filings:
        _ex_by_status[_f.status].append(_f)
    filing_status_breakdown = []
    for fs in FilingStatus:
        filing_status_breakdown.append(FilingStatusBreakdown(
            status=fs.value,
            count=filing_status_map.get(fs.value, 0),
            clients=[
                FilingStatusClientInfo(
                    client_id=_f.client_id,
                    client_name=_ex_filing_names.get(_f.client_id, "Unknown"),
                    financial_year=_f.financial_year,
                    assigned_executive=current_user.full_name,
                    last_updated=_f.updated_at,
                )
                for _f in _ex_by_status[fs][:50]
            ],
        ))

    # ── FY Distribution (scoped) ──
    fy_result = await db.execute(
        select(
            ITRFiling.financial_year,
            func.count(ITRFiling.id).label("total"),
            func.count(ITRFiling.id).filter(ITRFiling.status == FilingStatus.COMPLETED).label("completed"),
        )
        .where(ITRFiling.assigned_executive_id == executive_id)
        .group_by(ITRFiling.financial_year)
        .order_by(ITRFiling.financial_year.desc())
    )
    fy_distribution = [
        FYDistribution(
            financial_year=row.financial_year,
            total_filings=row.total,
            completed=row.completed,
            active=row.total - row.completed,
        )
        for row in fy_result.all()
    ]

    # ── Average processing times (scoped) ──
    avg_completion_result = await db.execute(
        select(
            func.avg(extract('epoch', ITRFiling.completed_at - ITRFiling.initiated_at) / 86400)
        ).where(
            ITRFiling.assigned_executive_id == executive_id,
            ITRFiling.completed_at.isnot(None),
        )
    )
    avg_days_total = avg_completion_result.scalar()

    avg_processing_result = await db.execute(
        select(
            func.avg(extract('epoch', ITRFiling.documents_approved_at - ITRFiling.documents_submitted_at) / 86400)
        ).where(
            ITRFiling.assigned_executive_id == executive_id,
            ITRFiling.documents_approved_at.isnot(None),
            ITRFiling.documents_submitted_at.isnot(None),
        )
    )
    avg_days_processing = avg_processing_result.scalar()

    # ── Document stats across all assigned filings ──
    doc_stats_result = await db.execute(
        select(
            func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.PENDING_UPLOAD).label("pending"),
            func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.REJECTED).label("rejected"),
            func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.APPROVED).label("approved"),
        ).where(
            FilingDocument.filing_id.in_(
                select(ITRFiling.id).where(ITRFiling.assigned_executive_id == executive_id)
            )
        )
    )
    doc_stats = doc_stats_result.one()

    # ── Recent filings (last 10, batch) ──
    _ex_recent_list = sorted(_ex_all_filings, key=lambda x: x.updated_at, reverse=True)[:10]
    recent_filings = [
        FilingStatusClientInfo(
            client_id=_f.client_id,
            client_name=_ex_filing_names.get(_f.client_id, "Unknown"),
            financial_year=_f.financial_year,
            assigned_executive=current_user.full_name,
            last_updated=_f.updated_at,
        )
        for _f in _ex_recent_list
    ]

    return ExecutiveAnalyticsResponse(
        executive_name=current_user.full_name,
        executive_email=current_user.email,
        total_assigned_clients=len(clients_list),
        clients=clients_list,
        total_filings=total_filings,
        active_filings=active_filings,
        completed_filings=completed_filings,
        halted_filings=halted_filings,
        filing_status_breakdown=filing_status_breakdown,
        fy_distribution=fy_distribution,
        avg_days_initiated_to_completed=round(avg_days_total, 1) if avg_days_total else None,
        avg_days_in_processing=round(avg_days_processing, 1) if avg_days_processing else None,
        total_documents_pending=doc_stats.pending or 0,
        total_documents_rejected=doc_stats.rejected or 0,
        total_documents_approved=doc_stats.approved or 0,
        recent_filings=recent_filings,
    )


@router.get("/analytics/client", response_model=ClientAnalyticsResponse)
async def get_client_analytics(
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Analytics dashboard for Client (own data only)."""
    from datetime import datetime as dt

    # Profile
    profile_result = await db.execute(
        select(ClientProfile).where(ClientProfile.user_id == current_user.id)
    )
    profile = profile_result.scalar_one_or_none()

    # All filings
    filings_result = await db.execute(
        select(ITRFiling).where(ITRFiling.client_id == current_user.id)
        .order_by(ITRFiling.financial_year.desc())
    )
    filings = filings_result.scalars().all()

    filing_details = []
    total_docs = total_approved = total_pending = total_rejected = 0

    for f in filings:
        # Document counts
        doc_counts = await db.execute(
            select(
                func.count(FilingDocument.id).label("total"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.APPROVED).label("approved"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.PENDING_UPLOAD).label("pending"),
                func.count(FilingDocument.id).filter(FilingDocument.status == DocumentStatus.REJECTED).label("rejected"),
            ).where(FilingDocument.filing_id == f.id)
        )
        counts = doc_counts.one()
        total_docs += counts.total or 0
        total_approved += counts.approved or 0
        total_pending += counts.pending or 0
        total_rejected += counts.rejected or 0

        # Executive name
        exec_name = None
        if f.assigned_executive_id:
            e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
            exec_name = e_result.scalar()

        # Latest computation status
        comp_result = await db.execute(
            select(FilingComputation.status).where(
                FilingComputation.filing_id == f.id
            ).order_by(FilingComputation.version.desc()).limit(1)
        )
        comp_status = comp_result.scalar()

        days_since = 0
        if f.initiated_at:
            from datetime import timezone as _tz
            _now = dt.now(_tz.utc)
            _initiated = f.initiated_at if f.initiated_at.tzinfo else f.initiated_at.replace(tzinfo=_tz.utc)
            days_since = (_now - _initiated).days

        filing_details.append(ClientFilingDetail(
            filing_id=f.id,
            financial_year=f.financial_year,
            status=f.status.value,
            progress_percentage=calculate_progress_percentage(f.status),
            initiated_at=f.initiated_at,
            completed_at=f.completed_at,
            last_updated=f.updated_at,
            assigned_executive_name=exec_name,
            documents_total=counts.total or 0,
            documents_approved=counts.approved or 0,
            documents_pending=counts.pending or 0,
            documents_rejected=counts.rejected or 0,
            computation_status=comp_status.value if comp_status else None,
            days_since_initiated=days_since,
        ))

    # Notification counts
    notif_result = await db.execute(
        select(
            func.count(Notification.id).label("total"),
            func.count(Notification.id).filter(Notification.is_read == False).label("unread"),
        ).where(Notification.user_id == current_user.id)
    )
    notif_counts = notif_result.one()

    completed_count = len([f for f in filings if f.status == FilingStatus.COMPLETED])

    return ClientAnalyticsResponse(
        client_name=current_user.full_name,
        client_email=current_user.email,
        account_status=current_user.account_status.value,
        registered_at=current_user.created_at,
        pan_number=profile.pan_number if profile else None,
        total_filings=len(filings),
        active_filings=len(filings) - completed_count,
        completed_filings=completed_count,
        filings=filing_details,
        total_notifications=notif_counts.total or 0,
        unread_notifications=notif_counts.unread or 0,
        total_documents=total_docs,
        total_approved=total_approved,
        total_pending=total_pending,
        total_rejected=total_rejected,
    )


# ═══════════════════════════════════════════════════════════════
# VIEWER COMPLETED QUEUE (Dashboard User / Partner)
# ═══════════════════════════════════════════════════════════════


@router.get("/completed-queue", response_model=CompletedQueueResponse)
async def get_completed_queue(
    current_user: User = Depends(get_current_dashboard_user_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Get all pending (undismissed) completed filings for the logged-in viewer."""
    result = await db.execute(
        select(ViewerCompletedQueue)
        .where(
            ViewerCompletedQueue.viewer_id == current_user.id,
            ViewerCompletedQueue.dismissed_at.is_(None),
        )
        .order_by(ViewerCompletedQueue.completed_at.desc())
    )
    rows = result.scalars().all()

    items = []
    for row in rows:
        # Get completed_by name
        completed_by_name = None
        if row.completed_by:
            user_result = await db.execute(select(User.full_name).where(User.id == row.completed_by))
            completed_by_name = user_result.scalar_one_or_none()

        items.append(CompletedQueueItem(
            id=row.id,
            filing_id=row.filing_id,
            client_name=row.client_name,
            financial_year=row.financial_year,
            completed_at=row.completed_at,
            completed_by_name=completed_by_name,
            executive_id=row.executive_id,
            executive_name=row.executive_name,
            manager_id=row.manager_id,
            manager_name=row.manager_name,
        ))

    return CompletedQueueResponse(items=items, count=len(items))


@router.post("/completed-queue/dismiss", response_model=dict)
async def dismiss_completed_queue_item(
    body: DismissQueueRequest,
    current_user: User = Depends(get_current_dashboard_user_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Dismiss a single item from the completed queue."""
    from datetime import datetime, timezone
    from fastapi import HTTPException, status

    result = await db.execute(
        select(ViewerCompletedQueue).where(
            ViewerCompletedQueue.id == body.queue_id,
            ViewerCompletedQueue.viewer_id == current_user.id,
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue item not found")

    item.dismissed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Item dismissed"}


@router.post("/completed-queue/dismiss-all", response_model=dict)
async def dismiss_all_completed_queue(
    current_user: User = Depends(get_current_dashboard_user_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """Dismiss all pending items from the completed queue."""
    from datetime import datetime, timezone
    from sqlalchemy import update

    await db.execute(
        update(ViewerCompletedQueue)
        .where(
            ViewerCompletedQueue.viewer_id == current_user.id,
            ViewerCompletedQueue.dismissed_at.is_(None),
        )
        .values(dismissed_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"message": "All items dismissed"}
