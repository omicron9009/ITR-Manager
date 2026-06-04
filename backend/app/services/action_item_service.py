"""Service — Action Items (computed dynamically from current state)."""

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import (
    AccountStatus,
    ActionItemPriority,
    ActionItemType,
    CompletedDocType,
    ComputationStatus,
    DocumentStatus,
    FilingStatus,
    UserRole,
)
from app.models.client_profile import ClientProfile
from app.models.executive_assignment import ExecutiveClientAssignment
from app.models.filing import ITRFiling
from app.models.filing_completed_doc import FilingCompletedDoc
from app.models.filing_computation import FilingComputation
from app.models.filing_document import FilingDocument
from app.models.manager_executive_assignment import ManagerExecutiveAssignment
from app.models.user import User
from app.schemas.action_item import ActionItemResponse


# Filing states that are "active" (produce action items)
_ACTIVE_STATES = [
    FilingStatus.INITIATED,
    FilingStatus.DOCUMENT_UPLOAD,
    FilingStatus.PROCESSING,
    FilingStatus.COMPUTATION,
    FilingStatus.FILING,
    FilingStatus.PAYMENT,
]

# Required completed doc types for PAYMENT -> COMPLETED transition
_REQUIRED_COMPLETED_DOCS = {
    CompletedDocType.ITR_ACKNOWLEDGEMENT,
    CompletedDocType.INVOICE,
    CompletedDocType.ITR_JSON,
    CompletedDocType.ITR_FORM,
    CompletedDocType.TAX_PAID_COMPUTATION,
}


async def get_action_items(
    db: AsyncSession,
    user: User,
    type_filter: Optional[ActionItemType] = None,
    filing_id_filter: Optional[UUID] = None,
) -> list[ActionItemResponse]:
    """Compute action items for the given user based on their role."""
    if user.role == UserRole.PARTNER:
        items = await _get_partner_items(db, filing_id_filter)
    elif user.role == UserRole.MANAGER:
        items = await _get_manager_items(db, user.id, filing_id_filter)
    elif user.role == UserRole.EXECUTIVE:
        items = await _get_executive_items(db, user.id, filing_id_filter)
    elif user.role == UserRole.CLIENT:
        items = await _get_client_items(db, user.id, filing_id_filter)
    else:
        items = []

    if type_filter:
        items = [i for i in items if i.type == type_filter]

    return items


# ---------------------------------------------------------------------------
# Partner action items
# ---------------------------------------------------------------------------


async def _get_partner_items(
    db: AsyncSession, filing_id_filter: Optional[UUID] = None
) -> list[ActionItemResponse]:
    items: list[ActionItemResponse] = []

    if not filing_id_filter:
        # 1. VERIFY_CLIENT — pending verification clients
        result = await db.execute(
            select(User).where(
                User.role == UserRole.CLIENT,
                User.account_status == AccountStatus.PENDING_VERIFICATION,
            )
        )
        pending_clients = result.scalars().all()
        for client in pending_clients:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.VERIFY_CLIENT,
                    title="Verify client registration",
                    description=f"{client.full_name} ({client.email}) is awaiting verification",
                    priority=ActionItemPriority.HIGH,
                    related_client_id=client.id,
                    action_url="/clients/pending",
                )
            )

        # 2. ASSIGN_EXECUTIVE — active clients with no executive assignment
        assigned_subq = (
            select(ExecutiveClientAssignment.client_id)
            .where(ExecutiveClientAssignment.is_active == True)
            .subquery()
        )
        result = await db.execute(
            select(User).where(
                User.role == UserRole.CLIENT,
                User.account_status == AccountStatus.ACTIVE,
                ~User.id.in_(select(assigned_subq.c.client_id)),
            )
        )
        unassigned_clients = result.scalars().all()
        for client in unassigned_clients:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.ASSIGN_EXECUTIVE,
                    title="Assign executive to client",
                    description=f"{client.full_name} has no assigned executive",
                    priority=ActionItemPriority.MEDIUM,
                    related_client_id=client.id,
                    action_url=f"/clients/{client.id}/assign",
                )
            )

    # Filing-based items
    filing_items = await _get_filing_items_for_staff(db, filing_id_filter, scoped_client_ids=None)
    items.extend(filing_items)

    # PARTNER_APPROVE_COMPUTATION — computations awaiting partner approval
    stmt = (
        select(ITRFiling)
        .options(
            selectinload(ITRFiling.client),
            selectinload(ITRFiling.computations),
        )
        .where(ITRFiling.status == FilingStatus.COMPUTATION)
    )
    if filing_id_filter:
        stmt = stmt.where(ITRFiling.id == filing_id_filter)

    result = await db.execute(stmt)
    filings_for_approval = result.scalars().unique().all()
    for filing in filings_for_approval:
        client_name = filing.client.full_name if filing.client else "Client"
        # Partner can approve from UPLOADED (bypass) or MANAGER_APPROVED
        needs_partner_approval = any(
            c.status in (ComputationStatus.UPLOADED, ComputationStatus.MANAGER_APPROVED)
            for c in filing.computations
        )
        if needs_partner_approval:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.PARTNER_APPROVE_COMPUTATION,
                    title="Approve computation for client",
                    description=f"Computation for {client_name} FY {filing.financial_year} awaits partner approval",
                    priority=ActionItemPriority.HIGH,
                    related_filing_id=filing.id,
                    related_client_id=filing.client_id,
                    financial_year=filing.financial_year,
                    action_url=f"/filings/{filing.id}/computation",
                )
            )

    return items


# ---------------------------------------------------------------------------
# Executive action items (scoped to assigned clients)
# ---------------------------------------------------------------------------


async def _get_executive_items(
    db: AsyncSession, executive_id: UUID, filing_id_filter: Optional[UUID] = None
) -> list[ActionItemResponse]:
    # Get this executive's active client IDs
    result = await db.execute(
        select(ExecutiveClientAssignment.client_id).where(
            ExecutiveClientAssignment.executive_id == executive_id,
            ExecutiveClientAssignment.is_active == True,
        )
    )
    client_ids = [row[0] for row in result.all()]
    if not client_ids and not filing_id_filter:
        return []

    items = await _get_filing_items_for_staff(db, filing_id_filter, scoped_client_ids=client_ids)
    # Executives cannot mark payment received — filter out that action item
    items = [i for i in items if i.type != ActionItemType.MARK_PAYMENT_RECEIVED]
    return items


# ---------------------------------------------------------------------------
# Manager action items (scoped to their team's clients)
# ---------------------------------------------------------------------------


async def _get_manager_items(
    db: AsyncSession, manager_id: UUID, filing_id_filter: Optional[UUID] = None
) -> list[ActionItemResponse]:
    items: list[ActionItemResponse] = []

    # Get team executive IDs
    result = await db.execute(
        select(ManagerExecutiveAssignment.executive_id).where(
            ManagerExecutiveAssignment.manager_id == manager_id,
            ManagerExecutiveAssignment.is_active == True,
        )
    )
    team_exec_ids = [row[0] for row in result.all()]

    # Get team client IDs
    if team_exec_ids:
        result = await db.execute(
            select(ExecutiveClientAssignment.client_id).where(
                ExecutiveClientAssignment.executive_id.in_(team_exec_ids),
                ExecutiveClientAssignment.is_active == True,
            )
        )
        team_client_ids = [row[0] for row in result.all()]
    else:
        team_client_ids = []

    if not filing_id_filter:
        # ASSIGN_CLIENT_TO_EXECUTIVE — active clients with no executive in this team
        # (Manager should assign new clients to their executives)
        assigned_subq = (
            select(ExecutiveClientAssignment.client_id)
            .where(ExecutiveClientAssignment.is_active == True)
            .subquery()
        )
        result = await db.execute(
            select(User).where(
                User.role == UserRole.CLIENT,
                User.account_status == AccountStatus.ACTIVE,
                ~User.id.in_(select(assigned_subq.c.client_id)),
            )
        )
        unassigned_clients = result.scalars().all()
        for client in unassigned_clients:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.ASSIGN_CLIENT_TO_EXECUTIVE,
                    title="Assign client to executive",
                    description=f"{client.full_name} has no assigned executive",
                    priority=ActionItemPriority.MEDIUM,
                    related_client_id=client.id,
                    action_url=f"/clients/{client.id}/assign",
                )
            )

    # Manager-specific: MANAGER_APPROVE_COMPUTATION — computations awaiting manager approval
    if team_client_ids or filing_id_filter:
        stmt = (
            select(ITRFiling)
            .options(
                selectinload(ITRFiling.client),
                selectinload(ITRFiling.computations),
            )
            .where(ITRFiling.status == FilingStatus.COMPUTATION)
        )
        if filing_id_filter:
            stmt = stmt.where(ITRFiling.id == filing_id_filter)
        elif team_client_ids:
            stmt = stmt.where(ITRFiling.client_id.in_(team_client_ids))

        result = await db.execute(stmt)
        filings = result.scalars().unique().all()

        for filing in filings:
            client_name = filing.client.full_name if filing.client else "Client"
            has_uploaded_comp = any(
                c.status == ComputationStatus.UPLOADED for c in filing.computations
            )
            if has_uploaded_comp:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.MANAGER_APPROVE_COMPUTATION,
                        title="Review computation",
                        description=f"Computation for {client_name} FY {filing.financial_year} awaits your approval",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/computation",
                    )
                )

    # Also include standard staff filing items for their team
    staff_items = await _get_filing_items_for_staff(
        db, filing_id_filter, scoped_client_ids=team_client_ids if team_client_ids else None
    )
    items.extend(staff_items)

    return items


# ---------------------------------------------------------------------------
# Staff filing items (shared logic for Partner & Executive)
# ---------------------------------------------------------------------------


async def _get_filing_items_for_staff(
    db: AsyncSession,
    filing_id_filter: Optional[UUID],
    scoped_client_ids: Optional[list[UUID]],
) -> list[ActionItemResponse]:
    """Compute filing-related action items for Partner/Executive."""
    items: list[ActionItemResponse] = []

    # Query active filings with relationships
    stmt = (
        select(ITRFiling)
        .options(
            selectinload(ITRFiling.client),
            selectinload(ITRFiling.documents),
            selectinload(ITRFiling.computations),
            selectinload(ITRFiling.completed_docs),
        )
        .where(ITRFiling.status.in_(_ACTIVE_STATES))
    )
    if filing_id_filter:
        stmt = stmt.where(ITRFiling.id == filing_id_filter)
    if scoped_client_ids is not None:
        stmt = stmt.where(ITRFiling.client_id.in_(scoped_client_ids))

    result = await db.execute(stmt)
    filings = result.scalars().unique().all()

    for filing in filings:
        client_name = filing.client.full_name if filing.client else "Client"

        # 3. SEND_DOCUMENT_CHECKLIST — INITIATED with executive assigned
        if filing.status == FilingStatus.INITIATED and filing.assigned_executive_id:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.SEND_DOCUMENT_CHECKLIST,
                    title="Send document checklist",
                    description=f"Send document checklist to {client_name} for FY {filing.financial_year}",
                    priority=ActionItemPriority.HIGH,
                    related_filing_id=filing.id,
                    related_client_id=filing.client_id,
                    financial_year=filing.financial_year,
                    action_url=f"/filings/{filing.id}/documents/assign",
                )
            )

        # 4. REVIEW_DOCUMENTS — has UPLOADED docs awaiting review
        if filing.status in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
            uploaded_docs = [
                d for d in filing.documents if d.status == DocumentStatus.UPLOADED
            ]
            if uploaded_docs:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.REVIEW_DOCUMENTS,
                        title="Review uploaded documents",
                        description=f"{client_name} has {len(uploaded_docs)} document(s) awaiting review for FY {filing.financial_year}",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        metadata={"pending_review_count": len(uploaded_docs)},
                        action_url=f"/filings/{filing.id}/documents",
                    )
                )

            # 5. MOVE_TO_COMPUTATION — all docs approved
            all_approved = (
                filing.documents
                and all(d.status == DocumentStatus.APPROVED for d in filing.documents)
            )
            if all_approved:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.MOVE_TO_COMPUTATION,
                        title="Move filing to computation",
                        description=f"All documents approved for {client_name} FY {filing.financial_year} — ready for computation",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/move-to-computation",
                    )
                )

        # 6. UPLOAD_COMPUTATION — in COMPUTATION, no active (UPLOADED) computation
        if filing.status == FilingStatus.COMPUTATION:
            has_uploaded_comp = any(
                c.status == ComputationStatus.UPLOADED for c in filing.computations
            )
            has_client_approved_comp = any(
                c.status in (ComputationStatus.CLIENT_APPROVED, ComputationStatus.APPROVED)
                for c in filing.computations
            )
            has_manager_rejected_comp = any(
                c.status == ComputationStatus.MANAGER_REJECTED for c in filing.computations
            )
            has_partner_rejected_comp = any(
                c.status == ComputationStatus.REJECTED and c.rejected_by is not None
                and c.manager_approved_by is not None  # was approved by manager then rejected by partner
                for c in filing.computations
            )

            # REVISE_COMPUTATION — computation was rejected, executive needs to re-upload
            if has_manager_rejected_comp and not has_uploaded_comp:
                rejected_comp = next(
                    (c for c in filing.computations if c.status == ComputationStatus.MANAGER_REJECTED), None
                )
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.REVISE_COMPUTATION,
                        title="Revise computation (rejected by manager)",
                        description=f"Computation for {client_name} FY {filing.financial_year} was rejected. "
                                    f"Reason: {rejected_comp.manager_rejection_reason if rejected_comp else 'N/A'}. "
                                    f"Please upload a revised version.",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        metadata={"rejection_reason": rejected_comp.manager_rejection_reason if rejected_comp else None},
                        action_url=f"/filings/{filing.id}/computation/upload",
                    )
                )

            if not has_uploaded_comp and not has_client_approved_comp and not has_manager_rejected_comp:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.UPLOAD_COMPUTATION,
                        title="Upload computation",
                        description=f"Upload tax computation for {client_name} FY {filing.financial_year}",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/computation/upload",
                    )
                )

            # 7. MOVE_TO_FILING — computation client-approved + tax paid
            if has_client_approved_comp and filing.is_tax_paid:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.MOVE_TO_FILING,
                        title="Move filing to ITR submission",
                        description=f"Computation approved and tax paid for {client_name} FY {filing.financial_year} — ready to file",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/transition",
                    )
                )

        # 8. UPLOAD_COMPLETED_DOCS — in FILING, missing required completed docs
        if filing.status == FilingStatus.FILING:
            existing_types = {cd.doc_type for cd in filing.completed_docs}
            missing = _REQUIRED_COMPLETED_DOCS - existing_types
            if missing:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.UPLOAD_COMPLETED_DOCS,
                        title="Upload filed documents",
                        description=f"Upload {len(missing)} remaining document(s) for {client_name} FY {filing.financial_year}",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        metadata={
                            "missing_types": [t.value for t in missing],
                            "uploaded_count": len(existing_types),
                            "required_count": len(_REQUIRED_COMPLETED_DOCS),
                        },
                        action_url=f"/filings/{filing.id}/completed-docs",
                    )
                )

        # 9. MARK_PAYMENT_RECEIVED — in PAYMENT, all completed docs present
        if filing.status == FilingStatus.PAYMENT:
            existing_types = {cd.doc_type for cd in filing.completed_docs}
            if _REQUIRED_COMPLETED_DOCS.issubset(existing_types):
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.MARK_PAYMENT_RECEIVED,
                        title="Mark payment received",
                        description=f"Confirm payment received from {client_name} for FY {filing.financial_year}",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=filing.client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/mark-payment",
                    )
                )

    return items


# ---------------------------------------------------------------------------
# Client action items
# ---------------------------------------------------------------------------


async def _get_client_items(
    db: AsyncSession, client_id: UUID, filing_id_filter: Optional[UUID] = None
) -> list[ActionItemResponse]:
    items: list[ActionItemResponse] = []

    if not filing_id_filter:
        # 10. COMPLETE_ONBOARDING — active account, form not submitted
        result = await db.execute(
            select(ClientProfile).where(ClientProfile.user_id == client_id)
        )
        profile = result.scalars().first()
        if profile and profile.form_submitted_at is None:
            items.append(
                ActionItemResponse(
                    type=ActionItemType.COMPLETE_ONBOARDING,
                    title="Complete onboarding form",
                    description="Please fill out the onboarding form to start your ITR filing",
                    priority=ActionItemPriority.HIGH,
                    related_client_id=client_id,
                    action_url="/onboarding/form",
                )
            )
        elif not profile:
            # Profile not created yet — check if account is active
            result = await db.execute(select(User).where(User.id == client_id))
            user = result.scalars().first()
            if user and user.account_status == AccountStatus.ACTIVE:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.COMPLETE_ONBOARDING,
                        title="Complete onboarding form",
                        description="Please fill out the onboarding form to start your ITR filing",
                        priority=ActionItemPriority.HIGH,
                        related_client_id=client_id,
                        action_url="/onboarding/form",
                    )
                )

    # Filing-based items
    stmt = (
        select(ITRFiling)
        .options(
            selectinload(ITRFiling.documents),
            selectinload(ITRFiling.computations),
        )
        .where(
            ITRFiling.client_id == client_id,
            ITRFiling.status.in_(_ACTIVE_STATES),
        )
    )
    if filing_id_filter:
        stmt = stmt.where(ITRFiling.id == filing_id_filter)

    result = await db.execute(stmt)
    filings = result.scalars().unique().all()

    for filing in filings:
        # 11. UPLOAD_DOCUMENTS — has PENDING_UPLOAD or REJECTED docs
        if filing.status in (FilingStatus.DOCUMENT_UPLOAD, FilingStatus.PROCESSING):
            pending_docs = [
                d
                for d in filing.documents
                if d.status in (DocumentStatus.PENDING_UPLOAD, DocumentStatus.REJECTED)
            ]
            if pending_docs:
                rejected_count = sum(
                    1 for d in pending_docs if d.status == DocumentStatus.REJECTED
                )
                pending_count = len(pending_docs) - rejected_count
                desc_parts = []
                if pending_count:
                    desc_parts.append(f"{pending_count} pending upload")
                if rejected_count:
                    desc_parts.append(f"{rejected_count} rejected (re-upload needed)")
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.UPLOAD_DOCUMENTS,
                        title="Upload required documents",
                        description=f"FY {filing.financial_year}: {', '.join(desc_parts)}",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=client_id,
                        financial_year=filing.financial_year,
                        metadata={
                            "pending_upload_count": pending_count,
                            "rejected_count": rejected_count,
                        },
                        action_url=f"/filings/{filing.id}/documents",
                    )
                )

            # 12. SUBMIT_DOCUMENTS — all docs uploaded, none pending/rejected
            all_uploaded_or_approved = filing.documents and all(
                d.status in (DocumentStatus.UPLOADED, DocumentStatus.APPROVED)
                for d in filing.documents
            )
            has_any_uploaded = any(
                d.status == DocumentStatus.UPLOADED for d in filing.documents
            )
            if all_uploaded_or_approved and has_any_uploaded:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.SUBMIT_DOCUMENTS,
                        title="Submit documents for review",
                        description=f"FY {filing.financial_year}: All documents uploaded — submit for executive review",
                        priority=ActionItemPriority.MEDIUM,
                        related_filing_id=filing.id,
                        related_client_id=client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/submit-documents",
                    )
                )

        # 13. REVIEW_COMPUTATION — computation partner-approved, awaiting client approval
        if filing.status == FilingStatus.COMPUTATION:
            has_partner_approved_comp = any(
                c.status == ComputationStatus.PARTNER_APPROVED for c in filing.computations
            )
            if has_partner_approved_comp:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.REVIEW_COMPUTATION,
                        title="Review tax computation",
                        description=f"FY {filing.financial_year}: A new computation is ready for your review",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/computation",
                    )
                )

            # 14. CONFIRM_TAX_PAID — computation client-approved but tax not paid
            has_client_approved_comp = any(
                c.status in (ComputationStatus.CLIENT_APPROVED, ComputationStatus.APPROVED)
                for c in filing.computations
            )
            if has_client_approved_comp and not filing.is_tax_paid:
                items.append(
                    ActionItemResponse(
                        type=ActionItemType.CONFIRM_TAX_PAID,
                        title="Confirm tax payment",
                        description=f"FY {filing.financial_year}: Please confirm that you have paid the tax",
                        priority=ActionItemPriority.HIGH,
                        related_filing_id=filing.id,
                        related_client_id=client_id,
                        financial_year=filing.financial_year,
                        action_url=f"/filings/{filing.id}/computation/approve",
                    )
                )

    return items
