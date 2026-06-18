"""Service — WhatsApp delivery via OpenWA gateway.

The Partner configures one global session via the UI; clients individually
opt-in (and provide a phone number) to receive notifications via WhatsApp.

Outbound flow:
  app code → create_notification(...) → notification_service fans out to
    in-app + email (existing) + whatsapp (this service, when client opted-in)

Network:
  backend-api  -- HTTP + X-API-Key -->  openwa-api (internal docker network)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from datetime import datetime
from typing import Optional

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.whatsapp_config import WhatsAppConfig

logger = logging.getLogger(__name__)


class WhatsAppServiceError(Exception):
    """Raised on OpenWA API errors so callers can surface a clean message."""

    def __init__(self, detail: str, status_code: int = 502):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# ───────────────────────────────────────────────────────────
# Encryption helpers (Fernet — 32-byte key, base64-encoded)
# ───────────────────────────────────────────────────────────
def _get_fernet() -> Fernet:
    key = settings.WHATSAPP_ENCRYPTION_KEY
    if not key:
        raise WhatsAppServiceError(
            "WHATSAPP_ENCRYPTION_KEY is not set; cannot encrypt/decrypt OpenWA API key.",
            status_code=500,
        )
    # Accept either a urlsafe-base64 32-byte key or any string we hash to one.
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError):
        # Allow non-Fernet-format keys by deriving a Fernet key from sha256
        import hashlib

        derived = base64.urlsafe_b64encode(hashlib.sha256(key.encode("utf-8")).digest())
        return Fernet(derived)


def encrypt_api_key(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_api_key(enc: str) -> str:
    try:
        return _get_fernet().decrypt(enc.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise WhatsAppServiceError(
            "Stored OpenWA API key cannot be decrypted (encryption key changed?). Re-run /whatsapp/setup.",
            status_code=500,
        ) from e


# ───────────────────────────────────────────────────────────
# Phone & body formatting
# ───────────────────────────────────────────────────────────
def normalize_to_e164(phone: str, default_cc: str = "+91") -> str:
    """Normalize a stored phone number to E.164 format.

    Handles the common cases for Indian mobile numbers stored without a country code:
      9876543210      → +919876543210   (10-digit, no cc — most existing production rows)
      09876543210     → +919876543210   (leading zero stripped)
      919876543210    → +919876543210   (cc digits present but no + prefix)
      +919876543210   → +919876543210   (already E.164, no-op)
      +91 98765 43210 → +919876543210   (spaces stripped)
    """
    if not phone:
        return phone
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("+"):
        return cleaned
    cleaned = cleaned.lstrip("0")
    if len(cleaned) == 10:
        return f"{default_cc}{cleaned}"
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return f"+{cleaned}"
    return f"{default_cc}{cleaned}"


def format_chat_id(phone_e164: str) -> str:
    """+919876543210 → 919876543210@c.us (OpenWA chat ID format)."""
    phone_e164 = normalize_to_e164(phone_e164)
    digits = "".join(c for c in phone_e164 if c.isdigit())
    if not digits:
        raise WhatsAppServiceError(f"Invalid phone number: {phone_e164!r}", status_code=400)
    return f"{digits}@c.us"


def format_whatsapp_body(
    title: str,
    message: str,
    *,
    cta_label: Optional[str] = None,
    action_url_path: Optional[str] = None,
    firm_name: Optional[str] = None,
) -> str:
    """Build a plain-text WhatsApp message with markdown.

    WhatsApp formatting:
        *bold*   _italic_   ~strike~   ```code```

    Output shape:

        *<title>*

        <message>

        🔗 <cta_label>
        <full_url>

        — <firm_name>
    """
    # Strip control chars / CRLF that could break OpenWA
    def _clean(s: str) -> str:
        return "".join(c for c in (s or "") if c == "\n" or c.isprintable()).strip()

    title_t = _clean(title)
    message_t = _clean(message)
    parts: list[str] = []
    if title_t:
        parts.append(f"*{title_t}*")
        parts.append("")
    if message_t:
        parts.append(message_t)

    if cta_label and action_url_path:
        full_url = settings.FRONTEND_URL.rstrip("/") + "/" + action_url_path.lstrip("/")
        parts.append("")
        parts.append(f"🔗 *{_clean(cta_label)}*")
        parts.append(full_url)

    if firm_name:
        parts.append("")
        parts.append(f"— _{_clean(firm_name)}_")

    body = "\n".join(parts).strip()
    # WhatsApp text limit is 4096; keep margin.
    if len(body) > 3900:
        body = body[:3897] + "..."
    return body


# ───────────────────────────────────────────────────────────
# Config helpers
# ───────────────────────────────────────────────────────────
async def get_active_config(db: AsyncSession) -> Optional[WhatsAppConfig]:
    result = await db.execute(
        select(WhatsAppConfig)
        .where(WhatsAppConfig.is_active == True)  # noqa: E712
        .order_by(WhatsAppConfig.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _persist_session_state(
    db: AsyncSession,
    cfg: WhatsAppConfig,
    *,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    phone: Optional[str] = None,
    last_error: Optional[str] = None,
    bump_qr: bool = False,
    mark_connected: bool = False,
) -> None:
    if session_id is not None:
        cfg.openwa_session_id = session_id
    if status is not None:
        cfg.session_status = status
    if phone is not None:
        cfg.phone_number_e164 = phone
    if last_error is not None:
        cfg.last_error = last_error
    if bump_qr:
        cfg.last_qr_at = datetime.utcnow()
    if mark_connected and cfg.connected_at is None:
        cfg.connected_at = datetime.utcnow()
    await db.flush()


# ───────────────────────────────────────────────────────────
# OpenWA HTTP client
# ───────────────────────────────────────────────────────────
def _openwa_client(cfg: WhatsAppConfig) -> httpx.AsyncClient:
    api_key = decrypt_api_key(cfg.api_key_encrypted)
    return httpx.AsyncClient(
        base_url=cfg.openwa_base_url.rstrip("/"),
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        timeout=settings.WHATSAPP_HTTP_TIMEOUT_SECONDS,
    )


async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    """One retry on 5xx / network errors."""
    last_exc: Optional[Exception] = None
    for attempt in range(2):
        try:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code >= 500 and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            return resp
        except (httpx.RequestError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt == 0:
                await asyncio.sleep(0.5)
                continue
            raise WhatsAppServiceError(f"OpenWA gateway unreachable: {e}", status_code=502) from e
    if last_exc:
        raise WhatsAppServiceError(f"OpenWA gateway unreachable: {last_exc}", status_code=502)
    return resp  # type: ignore[possibly-undefined]


def _raise_for_openwa(resp: httpx.Response, action: str) -> None:
    if resp.is_success:
        return
    detail = ""
    try:
        body = resp.json()
        detail = body.get("message") or body.get("detail") or str(body)
    except Exception:
        detail = resp.text[:300]
    raise WhatsAppServiceError(
        f"OpenWA {action} failed ({resp.status_code}): {detail}",
        status_code=resp.status_code if resp.status_code < 500 else 502,
    )


# ───────────────────────────────────────────────────────────
# Public service surface
# ───────────────────────────────────────────────────────────
async def validate_api_key(base_url: str, api_key: str) -> None:
    """POST /api/auth/validate to confirm the key is good before persisting."""
    base = base_url.rstrip("/")
    async with httpx.AsyncClient(timeout=settings.WHATSAPP_HTTP_TIMEOUT_SECONDS) as c:
        try:
            resp = await c.post(f"{base}/api/auth/validate", headers={"X-API-Key": api_key})
        except (httpx.RequestError, httpx.TimeoutException) as e:
            raise WhatsAppServiceError(f"Cannot reach OpenWA at {base}: {e}", status_code=502) from e
    if resp.status_code == 401:
        raise WhatsAppServiceError("OpenWA rejected the API key (401).", status_code=400)
    if not resp.is_success:
        raise WhatsAppServiceError(
            f"OpenWA validate returned {resp.status_code}: {resp.text[:200]}",
            status_code=502,
        )


async def save_config(
    db: AsyncSession,
    *,
    base_url: str,
    api_key: str,
    session_name: str,
    configured_by,
) -> WhatsAppConfig:
    """Validate the key, drop any existing config, and persist a new one."""
    await validate_api_key(base_url, api_key)

    # Drop any existing config (singleton)
    existing = (await db.execute(select(WhatsAppConfig))).scalars().all()
    for row in existing:
        await db.delete(row)
    await db.flush()

    cfg = WhatsAppConfig(
        openwa_base_url=base_url.rstrip("/"),
        api_key_encrypted=encrypt_api_key(api_key),
        session_name=session_name,
        configured_by=configured_by,
        session_status="created",
    )
    db.add(cfg)
    await db.flush()

    # Bump cache for whatsapp config
    try:
        from app.core.cache import NS, bump_version
        await bump_version(NS.WHATSAPP_CONFIG)
    except Exception:
        pass

    return cfg


async def delete_config(db: AsyncSession) -> None:
    cfg = await get_active_config(db)
    if cfg is None:
        return
    # Best-effort: stop and delete the session on OpenWA
    if cfg.openwa_session_id:
        try:
            async with _openwa_client(cfg) as c:
                await _request(c, "POST", f"/api/sessions/{cfg.openwa_session_id}/stop")
                await _request(c, "DELETE", f"/api/sessions/{cfg.openwa_session_id}")
        except Exception as e:
            logger.warning("OpenWA session cleanup on delete_config failed: %s", e)
    await db.delete(cfg)
    try:
        from app.core.cache import NS, bump_version
        await bump_version(NS.WHATSAPP_CONFIG)
    except Exception:
        pass


# ── Session lifecycle ──────────────────────────────────────
async def ensure_session(db: AsyncSession) -> WhatsAppConfig:
    """Create the OpenWA session if not present yet. Idempotent."""
    cfg = await get_active_config(db)
    if cfg is None:
        raise WhatsAppServiceError("WhatsApp not configured. Run /whatsapp/setup first.", status_code=400)

    if cfg.openwa_session_id:
        return cfg

    async with _openwa_client(cfg) as c:
        resp = await _request(c, "POST", "/api/sessions", json={"name": cfg.session_name})
        if resp.status_code == 409:
            # Session name already exists — fetch the list and find ours
            list_resp = await _request(c, "GET", "/api/sessions")
            _raise_for_openwa(list_resp, "list sessions")
            for s in list_resp.json() or []:
                if s.get("name") == cfg.session_name:
                    await _persist_session_state(
                        db, cfg, session_id=s.get("id"), status=s.get("status") or "created"
                    )
                    return cfg
        _raise_for_openwa(resp, "create session")
        data = resp.json()
        await _persist_session_state(
            db, cfg, session_id=data.get("id"), status=data.get("status") or "created"
        )
    return cfg


async def start_session(db: AsyncSession) -> WhatsAppConfig:
    cfg = await ensure_session(db)
    async with _openwa_client(cfg) as c:
        resp = await _request(c, "POST", f"/api/sessions/{cfg.openwa_session_id}/start")
        if resp.status_code == 400:
            # already started — fetch status
            sresp = await _request(c, "GET", f"/api/sessions/{cfg.openwa_session_id}")
            _raise_for_openwa(sresp, "get session")
            d = sresp.json()
            await _persist_session_state(db, cfg, status=d.get("status"), phone=d.get("phone"))
            return cfg
        _raise_for_openwa(resp, "start session")
        data = resp.json()
        await _persist_session_state(
            db, cfg, status=data.get("status"), phone=data.get("phone")
        )
    return cfg


async def stop_session(db: AsyncSession) -> WhatsAppConfig:
    cfg = await get_active_config(db)
    if cfg is None or not cfg.openwa_session_id:
        raise WhatsAppServiceError("No active WhatsApp session.", status_code=400)
    async with _openwa_client(cfg) as c:
        resp = await _request(c, "POST", f"/api/sessions/{cfg.openwa_session_id}/stop")
        _raise_for_openwa(resp, "stop session")
        data = resp.json()
        await _persist_session_state(db, cfg, status=data.get("status") or "disconnected")
    return cfg


async def get_session_status(db: AsyncSession) -> WhatsAppConfig:
    cfg = await get_active_config(db)
    if cfg is None:
        raise WhatsAppServiceError("WhatsApp not configured.", status_code=400)
    if not cfg.openwa_session_id:
        return cfg
    async with _openwa_client(cfg) as c:
        resp = await _request(c, "GET", f"/api/sessions/{cfg.openwa_session_id}")
        if resp.status_code == 404:
            await _persist_session_state(db, cfg, status="created", session_id=None)
            cfg.openwa_session_id = None
            return cfg
        _raise_for_openwa(resp, "get session")
        data = resp.json()
        status = data.get("status")
        phone = data.get("phone") if isinstance(data.get("phone"), str) else None
        last_error = data.get("lastError") if isinstance(data.get("lastError"), str) else None
        mark_connected = status == "ready"
        await _persist_session_state(
            db,
            cfg,
            status=status,
            phone=phone,
            last_error=last_error,
            mark_connected=mark_connected,
        )
    return cfg


async def get_qr_code(db: AsyncSession) -> dict:
    cfg = await ensure_session(db)
    async with _openwa_client(cfg) as c:
        resp = await _request(c, "GET", f"/api/sessions/{cfg.openwa_session_id}/qr")
        if resp.status_code == 400:
            # QR not ready yet OR session already authenticated
            sresp = await _request(c, "GET", f"/api/sessions/{cfg.openwa_session_id}")
            _raise_for_openwa(sresp, "get session")
            d = sresp.json()
            await _persist_session_state(db, cfg, status=d.get("status"), phone=d.get("phone"))
            return {"qr_code": None, "status": d.get("status") or cfg.session_status}
        _raise_for_openwa(resp, "get QR code")
        data = resp.json()
        await _persist_session_state(
            db, cfg, status=data.get("status") or cfg.session_status, bump_qr=True
        )
        return {"qr_code": data.get("qrCode"), "status": data.get("status") or cfg.session_status}


# ── Messaging ──────────────────────────────────────────────
async def send_text(
    db: AsyncSession,
    *,
    phone_e164: str,
    text: str,
) -> dict:
    cfg = await get_active_config(db)
    if cfg is None or not cfg.openwa_session_id:
        raise WhatsAppServiceError("WhatsApp session not initialized.", status_code=400)
    if cfg.session_status != "ready":
        raise WhatsAppServiceError(
            f"WhatsApp session not ready (status={cfg.session_status}).", status_code=400
        )

    chat_id = format_chat_id(phone_e164)
    async with _openwa_client(cfg) as c:
        resp = await _request(
            c,
            "POST",
            f"/api/sessions/{cfg.openwa_session_id}/messages/send-text",
            json={"chatId": chat_id, "text": text},
        )
        _raise_for_openwa(resp, "send text")
        data = resp.json()
        return {"message_id": data.get("messageId"), "timestamp": data.get("timestamp")}


async def send_test_message(db: AsyncSession, *, phone_e164: str, text: Optional[str]) -> dict:
    body = text or format_whatsapp_body(
        title="Test message",
        message=f"This is a test from {settings.FIRM_NAME}. If you see this, the gateway works.",
        firm_name=settings.FIRM_NAME,
    )
    return await send_text(db, phone_e164=phone_e164, text=body)
