"""API v1 — Dashboard endpoints (Partner, Executive, Client views)."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import enforce_client_access
from app.core.security import get_current_active_client, get_current_manager_executive_or_partner, get_current_executive_or_partner, get_current_partner, get_current_user
from app.database import get_db
from app.enums import AccountStatus, CompletedDocType, DocumentStatus, FilingStatus, UserRole
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
    DashboardSummaryResponse,
    DirectoryComputationItem,
    DirectoryCompletedDocItem,
    DirectoryDocumentItem,
    ExecutiveAnalyticsResponse,
    ExecutiveClientDetail,
    ExecutiveClientInfo,
    ExecutiveWorkloadItem,
    ExecutiveWorkloadResponse,
    FilingDirectoryResponse,
    FilingDrillDownItem,
    FilingDrillDownResponse,
    FilingStatusBreakdown,
    FilingStatusClientInfo,
    FilingStatusCounter,
    FYDistribution,
    PartnerAnalyticsResponse,
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
    current_user: User = Depends(get_current_manager_executive_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Get dashboard summary with filing counters.
    - Partner: all filings
    - Manager: filings of their team's clients
    - Executive: only assigned client filings
    """
    # Build base query depending on role
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
            phone_number=c.phone_number,
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

    # Completed Docs visibility rules:
    # - Client: only visible after COMPLETED, and only Ack + Invoice (not ITR JSON)
    # - Partner/Executive: always visible
    completed_items = []
    if current_user.role == UserRole.CLIENT:
        if filing.status == FilingStatus.COMPLETED:
            completed_result = await db.execute(
                select(FilingCompletedDoc).where(
                    FilingCompletedDoc.filing_id == filing_id,
                    FilingCompletedDoc.doc_type.in_([CompletedDocType.ITR_ACKNOWLEDGEMENT, CompletedDocType.INVOICE]),
                )
            )
            completed_docs = completed_result.scalars().all()
        else:
            completed_docs = []
    elif current_user.role in (UserRole.PARTNER, UserRole.EXECUTIVE):
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

    # ── Filing status breakdown with client details ──
    filing_status_breakdown = []
    for fs in FilingStatus:
        filings_result = await db.execute(
            select(ITRFiling).where(ITRFiling.status == fs)
            .order_by(ITRFiling.updated_at.desc()).limit(50)
        )
        filings_list = filings_result.scalars().all()
        clients_info = []
        for f in filings_list:
            c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
            c_name = c_result.scalar() or "Unknown"
            e_name = None
            if f.assigned_executive_id:
                e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
                e_name = e_result.scalar()
            clients_info.append(FilingStatusClientInfo(
                client_id=f.client_id,
                client_name=c_name,
                financial_year=f.financial_year,
                assigned_executive=e_name,
                last_updated=f.updated_at,
            ))
        filing_status_breakdown.append(FilingStatusBreakdown(
            status=fs.value,
            count=filing_status_map.get(fs.value, 0),
            clients=clients_info,
        ))

    # ── Executive → Client mapping ──
    exec_result = await db.execute(
        select(User).where(User.role == UserRole.EXECUTIVE).order_by(User.full_name)
    )
    executives = exec_result.scalars().all()

    executive_client_mapping = []
    for ex in executives:
        assign_result = await db.execute(
            select(ExecutiveClientAssignment).where(
                ExecutiveClientAssignment.executive_id == ex.id,
                ExecutiveClientAssignment.is_active == True,
            )
        )
        assignments = assign_result.scalars().all()
        clients_list = []
        for a in assignments:
            cl_result = await db.execute(select(User).where(User.id == a.client_id))
            cl = cl_result.scalar_one_or_none()
            if cl:
                fl_result = await db.execute(
                    select(ITRFiling).where(
                        ITRFiling.client_id == cl.id,
                        ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
                    ).order_by(ITRFiling.updated_at.desc()).limit(1)
                )
                fl = fl_result.scalar_one_or_none()
                clients_list.append(ExecutiveClientInfo(
                    client_id=cl.id,
                    client_name=cl.full_name,
                    client_email=cl.email,
                    filing_status=fl.status.value if fl else None,
                    financial_year=fl.financial_year if fl else None,
                ))

        exec_active_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == ex.id,
                ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
            )
        )
        exec_completed_result = await db.execute(
            select(func.count()).select_from(ITRFiling).where(
                ITRFiling.assigned_executive_id == ex.id,
                ITRFiling.status == FilingStatus.COMPLETED,
            )
        )

        executive_client_mapping.append(ExecutiveClientDetail(
            executive_id=ex.id,
            executive_name=ex.full_name,
            executive_email=ex.email,
            is_active=ex.is_active,
            clients=clients_list,
            total_clients=len(clients_list),
            active_filings=exec_active_result.scalar() or 0,
            completed_filings=exec_completed_result.scalar() or 0,
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

    # ── Recent filings (last 10 state changes) ──
    recent_result = await db.execute(
        select(ITRFiling).order_by(ITRFiling.updated_at.desc()).limit(10)
    )
    recent_filings = []
    for f in recent_result.scalars().all():
        c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
        c_name = c_result.scalar() or "Unknown"
        e_name = None
        if f.assigned_executive_id:
            e_result = await db.execute(select(User.full_name).where(User.id == f.assigned_executive_id))
            e_name = e_result.scalar()
        recent_filings.append(FilingStatusClientInfo(
            client_id=f.client_id,
            client_name=c_name,
            financial_year=f.financial_year,
            assigned_executive=e_name,
            last_updated=f.updated_at,
        ))

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

    clients_list = []
    for a in assignments:
        cl_result = await db.execute(select(User).where(User.id == a.client_id))
        cl = cl_result.scalar_one_or_none()
        if cl:
            fl_result = await db.execute(
                select(ITRFiling).where(
                    ITRFiling.client_id == cl.id,
                    ITRFiling.status.notin_([FilingStatus.COMPLETED, FilingStatus.HALTED]),
                ).order_by(ITRFiling.updated_at.desc()).limit(1)
            )
            fl = fl_result.scalar_one_or_none()
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

    # ── Filing status breakdown with client details ──
    filing_status_breakdown = []
    for fs in FilingStatus:
        filings_result = await db.execute(
            select(ITRFiling).where(
                ITRFiling.status == fs,
                ITRFiling.assigned_executive_id == executive_id,
            ).order_by(ITRFiling.updated_at.desc()).limit(50)
        )
        filings_list = filings_result.scalars().all()
        clients_info = []
        for f in filings_list:
            c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
            c_name = c_result.scalar() or "Unknown"
            clients_info.append(FilingStatusClientInfo(
                client_id=f.client_id,
                client_name=c_name,
                financial_year=f.financial_year,
                assigned_executive=current_user.full_name,
                last_updated=f.updated_at,
            ))
        filing_status_breakdown.append(FilingStatusBreakdown(
            status=fs.value,
            count=filing_status_map.get(fs.value, 0),
            clients=clients_info,
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

    # ── Recent filings (last 10) ──
    recent_result = await db.execute(
        select(ITRFiling).where(ITRFiling.assigned_executive_id == executive_id)
        .order_by(ITRFiling.updated_at.desc()).limit(10)
    )
    recent_filings = []
    for f in recent_result.scalars().all():
        c_result = await db.execute(select(User.full_name).where(User.id == f.client_id))
        c_name = c_result.scalar() or "Unknown"
        recent_filings.append(FilingStatusClientInfo(
            client_id=f.client_id,
            client_name=c_name,
            financial_year=f.financial_year,
            assigned_executive=current_user.full_name,
            last_updated=f.updated_at,
        ))

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
            initiated_naive = f.initiated_at.replace(tzinfo=None) if f.initiated_at.tzinfo else f.initiated_at
            days_since = (dt.utcnow() - initiated_naive).days

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
