"""API v1 — Main router aggregating all sub-routers."""

from fastapi import APIRouter

from app.api.v1 import (
    action_items,
    auth,
    clients,
    computations,
    dashboard,
    documents,
    email,
    executives,
    filings,
    managers,
    notifications,
    onboarding,
    audit,
    storage,
    tags,
    reports,
)

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(clients.router, prefix="/clients", tags=["Clients"])
api_router.include_router(executives.router, prefix="/executives", tags=["Executives"])
api_router.include_router(managers.router, prefix="/managers", tags=["Managers"])
api_router.include_router(filings.router, prefix="/filings", tags=["Filings"])
api_router.include_router(documents.router, prefix="/documents", tags=["Documents"])
api_router.include_router(computations.router, prefix="/computations", tags=["Computations"])
api_router.include_router(onboarding.router, prefix="/onboarding", tags=["Onboarding"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["Dashboard"])
api_router.include_router(audit.router, prefix="/audit", tags=["Audit"])
api_router.include_router(storage.router, prefix="/storage", tags=["Storage"])
api_router.include_router(email.router, prefix="/email", tags=["Email Configuration"])
api_router.include_router(tags.router, prefix="/tags", tags=["Tags"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(action_items.router, prefix="/action-items", tags=["Action Items"])
