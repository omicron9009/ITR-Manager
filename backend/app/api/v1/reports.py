"""API v1 — Comprehensive reports (DASHBOARD_USER / Partner)."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_dashboard_user_or_partner
from app.database import get_db
from app.models.user import User
from app.schemas.reports import ComprehensiveReportResponse
from app.services.report_service import build_comprehensive_report, generate_excel_report

router = APIRouter()


@router.get("/dashboard", response_model=ComprehensiveReportResponse)
async def get_comprehensive_dashboard(
    fy: Optional[str] = Query(None, description="Filter by financial year (e.g. 2024-2025)"),
    current_user: User = Depends(get_current_dashboard_user_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Comprehensive analytics dashboard — single route, full data.

    Includes: overall summary, FY-wise breakdown, manager distribution,
    location distribution (with hierarchy), pending report (longest-pending first),
    executive/manager/location leaderboards.

    Optional ?fy=2024-2025 filter narrows filing data to a specific financial year.
    """
    from app.config import settings as _settings
    from app.core.cache import NS, get_or_compute

    async def _build() -> ComprehensiveReportResponse:
        data = await build_comprehensive_report(db, financial_year=fy)
        return ComprehensiveReportResponse(**data)

    return await get_or_compute(
        NS.REPORT,
        f"dashboard:fy={fy or 'all'}",
        _settings.CACHE_TTL_REPORT,
        _build,
    )


@router.get("/download")
async def download_comprehensive_report(
    fy: Optional[str] = Query(None, description="Filter by financial year (e.g. 2024-2025)"),
    current_user: User = Depends(get_current_dashboard_user_or_partner),
    db: AsyncSession = Depends(get_db),
):
    """
    Download comprehensive report as Excel (.xlsx) file.

    Multi-sheet workbook: Overall, FY-Wise, Manager Distribution,
    Location Distribution, Pending Report, Executive/Manager/Location Leaderboards.
    """
    excel_buffer = await generate_excel_report(db, financial_year=fy)

    filename = "ITR_Comprehensive_Report"
    if fy:
        filename += f"_FY_{fy}"
    filename += ".xlsx"

    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
