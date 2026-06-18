"""Manage partner WhatsApp/OpenWA configuration through the backend API.

This script wraps the partner-only WhatsApp routes that already exist in the
backend:

    POST   /api/v1/auth/login
    GET    /api/v1/auth/me
    GET    /api/v1/whatsapp/config
    POST   /api/v1/whatsapp/setup
    DELETE /api/v1/whatsapp/config
    POST   /api/v1/whatsapp/session/start
    POST   /api/v1/whatsapp/session/stop
    GET    /api/v1/whatsapp/session/status
    GET    /api/v1/whatsapp/session/qr
    POST   /api/v1/whatsapp/messages/test

Examples
--------
Configure OpenWA and start the partner session:

    python scripts/manage_whatsapp_partner.py bootstrap ^
        --partner-email admin@itr-platform.com ^
        --partner-password admin123 ^
        --openwa-base-url http://openwa-api:2785 ^
        --openwa-api-key <partner-api-key> ^
        --session-name itr-platform ^
        --qr-output tmp\\whatsapp-qr.png

Poll until the session is ready:

    python scripts/manage_whatsapp_partner.py wait-ready ^
        --partner-email admin@itr-platform.com ^
        --partner-password admin123 ^
        --qr-output tmp\\whatsapp-qr.png

Use an existing bearer token instead of logging in:

    python scripts/manage_whatsapp_partner.py status ^
        --access-token <jwt>
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except ImportError:  # pragma: no cover
    sys.stderr.write("httpx is required. Install backend requirements first.\n")
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ENV_PATH = REPO_ROOT / "backend" / ".env"


def _load_simple_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


BACKEND_ENV = _load_simple_env_file(BACKEND_ENV_PATH)


def _default_from_env(*keys: str, fallback: Optional[str] = None) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value not in (None, ""):
            return value
        value = BACKEND_ENV.get(key)
        if value not in (None, ""):
            return value
    return fallback


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _normalize_prefix(prefix: str) -> str:
    prefix = (prefix or "").strip()
    if not prefix:
        return ""
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def _stderr(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class PartnerApiClient:
    def __init__(
        self,
        *,
        api_base_url: str,
        api_prefix: str,
        timeout_seconds: float,
        verify_ssl: bool,
        access_token: Optional[str] = None,
    ):
        self.api_prefix = _normalize_prefix(api_prefix)
        self.client = httpx.Client(
            base_url=api_base_url.rstrip("/"),
            timeout=timeout_seconds,
            verify=verify_ssl,
            headers={"Accept": "application/json"},
        )
        if access_token:
            self.set_access_token(access_token)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "PartnerApiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def set_access_token(self, token: str) -> None:
        self.client.headers["Authorization"] = f"Bearer {token}"

    def _build_path(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.api_prefix}{path}"

    @staticmethod
    def _extract_error_detail(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text.strip() or f"HTTP {response.status_code}"

        if isinstance(payload, dict):
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return detail.strip()
            if isinstance(detail, list):
                parts: list[str] = []
                for item in detail:
                    if isinstance(item, dict):
                        loc = ".".join(str(part) for part in item.get("loc", []))
                        msg = str(item.get("msg", "validation error"))
                        parts.append(f"{loc}: {msg}" if loc else msg)
                    else:
                        parts.append(str(item))
                if parts:
                    return "; ".join(parts)
            message = payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

        return json.dumps(payload, ensure_ascii=True)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, Any]] = None,
        expected_statuses: Optional[set[int]] = None,
    ) -> Any:
        target = self._build_path(path)
        try:
            response = self.client.request(method, target, json=json_body)
        except httpx.HTTPError as exc:
            raise ApiError(f"Request to {target} failed: {exc}") from exc

        if expected_statuses is None:
            ok = response.is_success
        else:
            ok = response.status_code in expected_statuses

        if not ok:
            detail = self._extract_error_detail(response)
            raise ApiError(
                f"{method.upper()} {target} returned {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        if response.status_code == 204 or not response.content:
            return None

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            return response.json()
        return response.text

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        payload = self.request(
            "POST",
            "/auth/login",
            json_body={"email": email, "password": password},
        )
        token = payload.get("access_token")
        if not token:
            raise ApiError("Login succeeded but no access_token was returned.")
        self.set_access_token(token)
        return payload

    def get_me(self) -> dict[str, Any]:
        result = self.request("GET", "/auth/me")
        if not isinstance(result, dict):
            raise ApiError("GET /auth/me returned an unexpected response.")
        return result


def _decode_qr_data_url(qr_code: str) -> bytes:
    if "," not in qr_code:
        raise ValueError("QR payload is not a valid data URL.")
    header, encoded = qr_code.split(",", 1)
    if ";base64" not in header:
        raise ValueError("QR payload is not base64 encoded.")
    return base64.b64decode(encoded)


def _write_qr_png(qr_code: str, output_path: str) -> str:
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_decode_qr_data_url(qr_code))
    return str(target)


def _emit_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=True))


def _authenticate(client: PartnerApiClient, args: argparse.Namespace) -> dict[str, Any]:
    login_payload: Optional[dict[str, Any]] = None

    if args.access_token:
        client.set_access_token(args.access_token)
    else:
        _stderr(f"Logging in as partner {args.partner_email} ...")
        login_payload = client.login(email=args.partner_email, password=args.partner_password)
        recovery_codes = login_payload.get("recovery_codes") or []
        if recovery_codes:
            _stderr("Recovery codes were issued on login. Save them securely:")
            for code in recovery_codes:
                _stderr(f"  {code}")

    me = client.get_me()
    role = str(me.get("role") or "")
    if role != "PARTNER":
        raise ApiError(f"Authenticated user is not a PARTNER (role={role!r}).", status_code=403)

    return {"user": me, "login": login_payload}


def _setup_whatsapp(client: PartnerApiClient, args: argparse.Namespace) -> dict[str, Any]:
    _stderr("Configuring WhatsApp/OpenWA settings ...")
    result = client.request(
        "POST",
        "/whatsapp/setup",
        json_body={
            "openwa_base_url": args.openwa_base_url,
            "api_key": args.openwa_api_key,
            "session_name": args.session_name,
        },
    )
    if not isinstance(result, dict):
        raise ApiError("Unexpected response from WhatsApp setup endpoint.")
    return result


def _fetch_qr(
    client: PartnerApiClient,
    *,
    output_path: Optional[str],
    include_data_url: bool,
) -> dict[str, Any]:
    result = client.request("GET", "/whatsapp/session/qr")
    if not isinstance(result, dict):
        raise ApiError("Unexpected response from WhatsApp QR endpoint.")

    qr_code = result.get("qr_code")
    payload: dict[str, Any] = {
        "status": result.get("status"),
        "qr_available": bool(qr_code),
    }

    if qr_code and output_path:
        try:
            payload["saved_to"] = _write_qr_png(str(qr_code), output_path)
        except Exception as exc:
            raise ApiError(f"Failed to write QR PNG to {output_path}: {exc}") from exc
    if qr_code and include_data_url:
        payload["qr_code"] = qr_code

    return payload


def _wait_until_ready(client: PartnerApiClient, args: argparse.Namespace) -> dict[str, Any]:
    _stderr("Polling WhatsApp session status until ready ...")
    started = time.monotonic()
    attempts = 0
    last_saved_qr: Optional[str] = None
    last_qr_available = False
    last_qr_payload: Optional[dict[str, Any]] = None

    while True:
        attempts += 1
        status_payload = client.request("GET", "/whatsapp/session/status")
        if not isinstance(status_payload, dict):
            raise ApiError("Unexpected response from WhatsApp status endpoint.")
        current_status = str(status_payload.get("status") or "")

        if current_status.lower() == "ready":
            return {
                "ready": True,
                "attempts": attempts,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "status": status_payload,
                "last_saved_qr": last_saved_qr,
                "last_qr_available": last_qr_available,
                "last_qr": last_qr_payload,
            }

        qr_payload = _fetch_qr(
            client,
            output_path=args.qr_output,
            include_data_url=args.print_data_url,
        )
        last_qr_payload = qr_payload
        last_qr_available = bool(qr_payload.get("qr_available"))
        if qr_payload.get("saved_to"):
            last_saved_qr = str(qr_payload["saved_to"])

        elapsed = time.monotonic() - started
        if elapsed >= args.max_wait_seconds:
            location = f", saved_qr={last_saved_qr}" if last_saved_qr else ""
            raise ApiError(
                f"Timed out after {args.max_wait_seconds:.0f}s waiting for WhatsApp session "
                f"to become ready. Last status={current_status!r}{location}.",
                status_code=408,
            )

        time.sleep(args.poll_seconds)


def _command_setup(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    return _setup_whatsapp(client, args)


def _command_config(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    return client.request("GET", "/whatsapp/config")


def _command_delete_config(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    _stderr("Deleting WhatsApp configuration ...")
    client.request("DELETE", "/whatsapp/config", expected_statuses={204})
    return {"deleted": True}


def _command_start(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    _stderr("Starting WhatsApp session ...")
    return client.request("POST", "/whatsapp/session/start")


def _command_stop(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    _stderr("Stopping WhatsApp session ...")
    return client.request("POST", "/whatsapp/session/stop")


def _command_status(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    return client.request("GET", "/whatsapp/session/status")


def _command_qr(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    return _fetch_qr(
        client,
        output_path=args.output,
        include_data_url=args.print_data_url,
    )


def _command_wait_ready(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    return _wait_until_ready(client, args)


def _command_test(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    _stderr(f"Sending WhatsApp test message to {args.to_phone} ...")
    return client.request(
        "POST",
        "/whatsapp/messages/test",
        json_body={"to_phone": args.to_phone, "text": args.text},
    )


def _command_bootstrap(client: PartnerApiClient, args: argparse.Namespace) -> Any:
    results: dict[str, Any] = {}
    results["config"] = _setup_whatsapp(client, args)

    _stderr("Creating/starting WhatsApp session ...")
    start_result = client.request("POST", "/whatsapp/session/start")
    results["start"] = start_result

    if args.wait_ready:
        results["wait_ready"] = _wait_until_ready(client, args)
    else:
        results["qr"] = _fetch_qr(
            client,
            output_path=args.qr_output,
            include_data_url=args.print_data_url,
        )

    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--api-base-url",
        default=_default_from_env("ITR_API_BASE_URL", fallback="http://127.0.0.1:8000"),
        help="Backend API base URL. Default: %(default)s",
    )
    parser.add_argument(
        "--api-prefix",
        default=_default_from_env("ITR_API_PREFIX", "API_V1_PREFIX", fallback="/api/v1"),
        help="API prefix mounted by the backend. Default: %(default)s",
    )
    parser.add_argument(
        "--partner-email",
        default=_default_from_env("ITR_PARTNER_EMAIL", "ADMIN_EMAIL"),
        help="Partner login email. Falls back to backend/.env ADMIN_EMAIL.",
    )
    parser.add_argument(
        "--partner-password",
        default=_default_from_env("ITR_PARTNER_PASSWORD", "ADMIN_PASSWORD"),
        help="Partner login password. Falls back to backend/.env ADMIN_PASSWORD.",
    )
    parser.add_argument(
        "--access-token",
        default=_default_from_env("ITR_ACCESS_TOKEN"),
        help="Existing bearer token. If set, login is skipped.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=float(_default_from_env("ITR_API_TIMEOUT_SECONDS", fallback="15")),
        help="HTTP timeout in seconds. Default: %(default)s",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="Get the active WhatsApp configuration.")

    setup_parser = subparsers.add_parser("setup", help="Create or replace WhatsApp configuration.")
    setup_parser.add_argument(
        "--openwa-base-url",
        default=_default_from_env(
            "ITR_OPENWA_BASE_URL",
            "OPENWA_BASE_URL",
            "WHATSAPP_DEFAULT_BASE_URL",
            fallback="http://openwa-api:2785",
        ),
        help="OpenWA base URL from the backend's point of view.",
    )
    setup_parser.add_argument(
        "--openwa-api-key",
        default=_default_from_env("ITR_OPENWA_API_KEY", "OPENWA_API_KEY"),
        help="OpenWA X-API-Key to store for notification delivery.",
    )
    setup_parser.add_argument(
        "--session-name",
        default=_default_from_env(
            "ITR_WHATSAPP_SESSION_NAME",
            "OPENWA_SESSION_NAME",
            "WHATSAPP_DEFAULT_SESSION_NAME",
            fallback="itr-platform",
        ),
        help="WhatsApp session name.",
    )

    subparsers.add_parser("delete-config", help="Delete the active WhatsApp configuration.")
    subparsers.add_parser("start", help="Create/start the WhatsApp session.")
    subparsers.add_parser("stop", help="Stop the WhatsApp session.")
    subparsers.add_parser("status", help="Get the WhatsApp session status.")

    qr_parser = subparsers.add_parser("qr", help="Fetch the latest WhatsApp QR.")
    qr_parser.add_argument(
        "--output",
        help="Write the QR data URL to a PNG file instead of only reporting availability.",
    )
    qr_parser.add_argument(
        "--print-data-url",
        action="store_true",
        help="Include the raw data URL in the JSON output.",
    )

    wait_parser = subparsers.add_parser("wait-ready", help="Poll until the WhatsApp session is ready.")
    wait_parser.add_argument(
        "--poll-seconds",
        type=_positive_float,
        default=2.0,
        help="Polling interval in seconds. Default: %(default)s",
    )
    wait_parser.add_argument(
        "--max-wait-seconds",
        type=_positive_float,
        default=120.0,
        help="Maximum wait time before failing. Default: %(default)s",
    )
    wait_parser.add_argument(
        "--qr-output",
        help="Optional PNG file path to keep refreshing with the latest QR code.",
    )
    wait_parser.add_argument(
        "--print-data-url",
        action="store_true",
        help="Include the raw data URL in intermediate QR payloads.",
    )

    test_parser = subparsers.add_parser("test", help="Send a WhatsApp test message.")
    test_parser.add_argument("--to-phone", required=True, help="Destination phone in E.164 format.")
    test_parser.add_argument("--text", help="Optional custom message body.")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Configure WhatsApp, start the session, and optionally wait until ready.",
    )
    bootstrap_parser.add_argument(
        "--openwa-base-url",
        default=_default_from_env(
            "ITR_OPENWA_BASE_URL",
            "OPENWA_BASE_URL",
            "WHATSAPP_DEFAULT_BASE_URL",
            fallback="http://openwa-api:2785",
        ),
        help="OpenWA base URL from the backend's point of view.",
    )
    bootstrap_parser.add_argument(
        "--openwa-api-key",
        default=_default_from_env("ITR_OPENWA_API_KEY", "OPENWA_API_KEY"),
        help="OpenWA X-API-Key to store for notification delivery.",
    )
    bootstrap_parser.add_argument(
        "--session-name",
        default=_default_from_env(
            "ITR_WHATSAPP_SESSION_NAME",
            "OPENWA_SESSION_NAME",
            "WHATSAPP_DEFAULT_SESSION_NAME",
            fallback="itr-platform",
        ),
        help="WhatsApp session name.",
    )
    bootstrap_parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="After starting, poll until the session reaches status=ready.",
    )
    bootstrap_parser.add_argument(
        "--poll-seconds",
        type=_positive_float,
        default=2.0,
        help="Polling interval used with --wait-ready. Default: %(default)s",
    )
    bootstrap_parser.add_argument(
        "--max-wait-seconds",
        type=_positive_float,
        default=120.0,
        help="Maximum wait time used with --wait-ready. Default: %(default)s",
    )
    bootstrap_parser.add_argument(
        "--qr-output",
        help="Optional PNG file path to save the latest QR code.",
    )
    bootstrap_parser.add_argument(
        "--print-data-url",
        action="store_true",
        help="Include the raw QR data URL in the JSON output.",
    )

    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.access_token:
        if not args.partner_email or not args.partner_password:
            parser.error(
                "Provide --access-token or both --partner-email and --partner-password."
            )

    if args.command in {"setup", "bootstrap"}:
        if not args.openwa_base_url:
            parser.error(f"{args.command} requires --openwa-base-url.")
        if not args.openwa_api_key:
            parser.error(f"{args.command} requires --openwa-api-key.")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    _validate_args(parser, args)

    handlers = {
        "config": _command_config,
        "setup": _command_setup,
        "delete-config": _command_delete_config,
        "start": _command_start,
        "stop": _command_stop,
        "status": _command_status,
        "qr": _command_qr,
        "wait-ready": _command_wait_ready,
        "test": _command_test,
        "bootstrap": _command_bootstrap,
    }

    try:
        with PartnerApiClient(
            api_base_url=args.api_base_url,
            api_prefix=args.api_prefix,
            timeout_seconds=args.timeout_seconds,
            verify_ssl=not args.insecure,
            access_token=args.access_token,
        ) as client:
            auth_info = _authenticate(client, args)
            result = handlers[args.command](client, args)

            payload = {
                "authenticated_user": {
                    "email": auth_info["user"].get("email"),
                    "role": auth_info["user"].get("role"),
                },
                "command": args.command,
                "result": result,
            }
            _emit_json(payload)
        return 0
    except ApiError as exc:
        _stderr(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        _stderr("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
