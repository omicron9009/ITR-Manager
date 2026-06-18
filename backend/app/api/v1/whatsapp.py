"""API v1 — WhatsApp gateway configuration and client opt-in."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_client, get_current_partner
from app.database import get_db
from app.enums import AuditEventType
from app.models.client_profile import ClientProfile
from app.models.user import User
from app.models.whatsapp_config import WhatsAppConfig
from app.schemas.whatsapp import (
    ClientWhatsAppPreferenceResponse,
    ClientWhatsAppPreferenceUpdate,
    WhatsAppConfigResponse,
    WhatsAppConfigSetupRequest,
    WhatsAppQRResponse,
    WhatsAppSendResponse,
    WhatsAppSessionStatus,
    WhatsAppTestRequest,
)
from app.services import whatsapp_service
from app.services.audit_service import record_audit_event
from app.services.whatsapp_service import WhatsAppServiceError

router = APIRouter()


def _to_response(cfg: WhatsAppConfig) -> WhatsAppConfigResponse:
    return WhatsAppConfigResponse(
        id=cfg.id,
        openwa_base_url=cfg.openwa_base_url,
        session_name=cfg.session_name,
        openwa_session_id=cfg.openwa_session_id,
        session_status=cfg.session_status,
        phone_number_e164=cfg.phone_number_e164,
        connected_at=cfg.connected_at,
        last_qr_at=cfg.last_qr_at,
        last_error=cfg.last_error,
        is_active=cfg.is_active,
        is_configured=True,
        configured_by=cfg.configured_by,
        created_at=cfg.created_at,
        updated_at=cfg.updated_at,
    )


def _handle_wa_error(e: WhatsAppServiceError):
    raise HTTPException(status_code=e.status_code, detail=e.detail)


# ─── PARTNER-ONLY: Configuration ────────────────────────────
@router.get("/config", response_model=WhatsAppConfigResponse)
async def get_whatsapp_config(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    cfg = await whatsapp_service.get_active_config(db)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="WhatsApp not configured. Use POST /whatsapp/setup to configure.",
        )
    return _to_response(cfg)


@router.post("/setup", response_model=WhatsAppConfigResponse)
async def setup_whatsapp(
    payload: WhatsAppConfigSetupRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Save OpenWA gateway URL + admin API key + session name (Partner only).

    The API key is validated against OpenWA `/api/auth/validate` before being
    encrypted and persisted. Any pre-existing configuration is dropped.
    """
    try:
        cfg = await whatsapp_service.save_config(
            db,
            base_url=payload.openwa_base_url,
            api_key=payload.api_key,
            session_name=payload.session_name,
            configured_by=current_user.id,
        )
    except WhatsAppServiceError as e:
        _handle_wa_error(e)

    await record_audit_event(
        db,
        event_type=AuditEventType.WHATSAPP_CONFIGURED,
        actor_id=current_user.id,
        details={"base_url": payload.openwa_base_url, "session_name": payload.session_name},
    )
    return _to_response(cfg)


@router.delete("/config", status_code=204)
async def delete_whatsapp_config(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    await whatsapp_service.delete_config(db)
    await record_audit_event(
        db,
        event_type=AuditEventType.WHATSAPP_DISCONNECTED,
        actor_id=current_user.id,
    )
    return None


# ─── PARTNER-ONLY: Session lifecycle ────────────────────────
@router.post("/session/start", response_model=WhatsAppConfigResponse)
async def start_whatsapp_session(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    try:
        cfg = await whatsapp_service.start_session(db)
    except WhatsAppServiceError as e:
        _handle_wa_error(e)
    await record_audit_event(
        db,
        event_type=AuditEventType.WHATSAPP_SESSION_STARTED,
        actor_id=current_user.id,
        details={"status": cfg.session_status},
    )
    return _to_response(cfg)


@router.post("/session/stop", response_model=WhatsAppConfigResponse)
async def stop_whatsapp_session(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    try:
        cfg = await whatsapp_service.stop_session(db)
    except WhatsAppServiceError as e:
        _handle_wa_error(e)
    await record_audit_event(
        db,
        event_type=AuditEventType.WHATSAPP_SESSION_STOPPED,
        actor_id=current_user.id,
    )
    return _to_response(cfg)


@router.get("/session/status", response_model=WhatsAppSessionStatus)
async def get_whatsapp_session_status(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    try:
        cfg = await whatsapp_service.get_session_status(db)
    except WhatsAppServiceError as e:
        _handle_wa_error(e)
    return WhatsAppSessionStatus(
        session_id=cfg.openwa_session_id,
        status=cfg.session_status,
        phone_number_e164=cfg.phone_number_e164,
        connected_at=cfg.connected_at,
        last_error=cfg.last_error,
    )


@router.get("/session/qr", response_model=WhatsAppQRResponse)
async def get_whatsapp_qr(
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    """Return the latest QR data URL. Poll every ~2s while status is `qr_ready`."""
    try:
        data = await whatsapp_service.get_qr_code(db)
    except WhatsAppServiceError as e:
        _handle_wa_error(e)
    return WhatsAppQRResponse(qr_code=data.get("qr_code"), status=data.get("status"))


# ─── PARTNER-ONLY: Test message ─────────────────────────────
@router.post("/messages/test", response_model=WhatsAppSendResponse)
async def send_whatsapp_test(
    payload: WhatsAppTestRequest,
    current_user: User = Depends(get_current_partner),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await whatsapp_service.send_test_message(
            db, phone_e164=payload.to_phone, text=payload.text
        )
    except WhatsAppServiceError as e:
        await record_audit_event(
            db,
            event_type=AuditEventType.WHATSAPP_TEST_SENT,
            actor_id=current_user.id,
            details={"to": payload.to_phone, "ok": False, "error": e.detail[:300]},
        )
        return WhatsAppSendResponse(sent=False, error=e.detail)

    await record_audit_event(
        db,
        event_type=AuditEventType.WHATSAPP_TEST_SENT,
        actor_id=current_user.id,
        details={"to": payload.to_phone, "ok": True, "message_id": result.get("message_id")},
    )
    return WhatsAppSendResponse(sent=True, message_id=result.get("message_id"))


# ─── CLIENT-ONLY: Opt-in / phone number ─────────────────────
@router.get("/me/preferences", response_model=ClientWhatsAppPreferenceResponse)
async def get_my_whatsapp_preferences(
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    profile = (
        await db.execute(select(ClientProfile).where(ClientProfile.user_id == current_user.id))
    ).scalar_one_or_none()
    return ClientWhatsAppPreferenceResponse(
        phone_number=current_user.phone_number,
        whatsapp_opt_in=bool(profile.whatsapp_opt_in) if profile else False,
    )


@router.put("/me/preferences", response_model=ClientWhatsAppPreferenceResponse)
async def update_my_whatsapp_preferences(
    payload: ClientWhatsAppPreferenceUpdate,
    current_user: User = Depends(get_current_active_client),
    db: AsyncSession = Depends(get_db),
):
    """Client sets/updates their WhatsApp number and opt-in flag.

    The number is stored in `users.phone_number` (shared field). Opt-in alone
    is stored on the client profile. Cannot opt in without a phone number.
    """
    if payload.whatsapp_opt_in:
        effective_phone = payload.phone_number or current_user.phone_number
        if not effective_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A WhatsApp phone number (E.164) is required to opt in.",
            )

    if payload.phone_number is not None:
        current_user.phone_number = payload.phone_number

    profile = (
        await db.execute(select(ClientProfile).where(ClientProfile.user_id == current_user.id))
    ).scalar_one_or_none()
    if profile is None:
        # Auto-create a minimal profile so opt-in works even pre-onboarding.
        profile = ClientProfile(user_id=current_user.id, form_data={})
        db.add(profile)
        await db.flush()

    previous = bool(profile.whatsapp_opt_in)
    profile.whatsapp_opt_in = payload.whatsapp_opt_in
    await db.flush()

    if previous != payload.whatsapp_opt_in:
        await record_audit_event(
            db,
            event_type=AuditEventType.WHATSAPP_CLIENT_OPT_IN_CHANGED,
            actor_id=current_user.id,
            client_id=current_user.id,
            details={"opt_in": payload.whatsapp_opt_in},
        )

    return ClientWhatsAppPreferenceResponse(
        phone_number=current_user.phone_number,
        whatsapp_opt_in=profile.whatsapp_opt_in,
    )
