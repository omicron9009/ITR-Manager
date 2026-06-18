"""Core — Shared cache layer.

Two tiers:
  - L1: in-process `cachetools.TTLCache` (per-worker, fastest)
  - L2: Redis (shared across workers, survives restarts within TTL)

Namespace versioning: every cached value lives under a versioned key
(``itr:<ns>:v<n>:<key>``). Calling ``bump_version(ns)`` atomically
invalidates every entry in that namespace without scanning Redis.

Fail-open: if Redis is unreachable or ``CACHE_ENABLED`` is false, all
cache operations become no-ops and the underlying function is called
normally — APIs never break because the cache is down.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import pickle
from typing import Any, Awaitable, Callable, Optional, TypeVar

from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

try:  # Redis is optional at import time
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except Exception:  # pragma: no cover
    Redis = None  # type: ignore
    RedisError = Exception  # type: ignore


logger = logging.getLogger("app.cache")

# Sentinel used to distinguish "miss" from a cached `None`/`[]` value.
_MISS: Any = object()

# Module-level state
_redis: Optional["Redis"] = None
_l1: TTLCache = TTLCache(maxsize=4096, ttl=300)
_version_l1: TTLCache = TTLCache(maxsize=512, ttl=1)

T = TypeVar("T")


# ─── Lifecycle ──────────────────────────────────────────────
async def init_cache() -> None:
    """Initialize Redis client at startup. Safe to call multiple times."""
    global _redis
    if not settings.CACHE_ENABLED:
        logger.info("Cache disabled (CACHE_ENABLED=false)")
        return
    if Redis is None:
        logger.warning("redis package not installed — cache disabled")
        return
    try:
        client = Redis.from_url(settings.REDIS_URL, decode_responses=False)
        await client.ping()
        _redis = client
        logger.info("Redis cache connected at %s", settings.REDIS_URL)
    except Exception as e:
        logger.warning("Redis unreachable, cache disabled: %s", e)
        _redis = None


async def close_cache() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
        _redis = None


def is_enabled() -> bool:
    return settings.CACHE_ENABLED and _redis is not None


# ─── Key helpers ────────────────────────────────────────────
def _full_key(namespace: str, version: int, raw: str) -> str:
    return f"itr:{namespace}:v{version}:{raw}"


def _stable_args_key(args: tuple, kwargs: dict, ignore: set[str]) -> str:
    """Build a deterministic key from call args, skipping AsyncSession & ignored names."""
    parts: list[str] = []
    for a in args:
        if isinstance(a, AsyncSession):
            continue
        parts.append(repr(a))
    for k, v in sorted(kwargs.items()):
        if k in ignore or isinstance(v, AsyncSession):
            continue
        parts.append(f"{k}={v!r}")
    blob = "|".join(parts)
    if len(blob) > 200:
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return blob or "_"


# ─── Versioning (for namespace-wide invalidation) ───────────
async def _get_version(namespace: str) -> int:
    cached = _version_l1.get(namespace)
    if cached is not None:
        return cached
    version = 1
    if _redis is not None:
        try:
            v = await _redis.get(f"itr:_ver:{namespace}")
            if v is not None:
                try:
                    version = int(v)
                except Exception:
                    version = 1
        except RedisError:
            pass
    _version_l1[namespace] = version
    return version


async def bump_version(namespace: str) -> None:
    """Invalidate every entry in ``namespace`` atomically."""
    # Drop local L1 entries for this namespace
    prefix = f"itr:{namespace}:"
    for k in [k for k in list(_l1.keys()) if k.startswith(prefix)]:
        _l1.pop(k, None)
    _version_l1.pop(namespace, None)

    if _redis is None:
        return
    try:
        await _redis.incr(f"itr:_ver:{namespace}")
    except RedisError as e:
        logger.warning("Cache bump_version(%s) failed — stale entries may persist until TTL: %s", namespace, e)


# ─── Low-level get/set ──────────────────────────────────────
async def cache_get(namespace: str, key: str, *, use_l1: bool = True) -> Any:
    """Return cached value or ``_MISS`` sentinel."""
    if not settings.CACHE_ENABLED:
        return _MISS
    version = await _get_version(namespace)
    full = _full_key(namespace, version, key)

    if use_l1 and full in _l1:
        return _l1[full]

    if _redis is None:
        return _MISS
    try:
        raw = await _redis.get(full)
    except RedisError:
        return _MISS
    if raw is None:
        return _MISS
    try:
        value = pickle.loads(raw)
    except Exception as e:
        logger.debug("Cache unpickle failed for %s: %s", full, e)
        return _MISS
    if use_l1:
        _l1[full] = value
    return value


async def cache_set(
    namespace: str,
    key: str,
    value: Any,
    ttl: int,
    *,
    use_l1: bool = True,
) -> None:
    if not settings.CACHE_ENABLED:
        return
    version = await _get_version(namespace)
    full = _full_key(namespace, version, key)
    if use_l1:
        _l1[full] = value
    if _redis is None:
        return
    try:
        await _redis.set(full, pickle.dumps(value), ex=ttl)
    except RedisError as e:
        logger.debug("Cache set failed: %s", e)
    except Exception as e:
        logger.debug("Cache pickle failed: %s", e)


# ─── Decorator ──────────────────────────────────────────────
def cached(
    namespace: str,
    ttl: int = 60,
    *,
    skip_args: tuple[str, ...] = ("db",),
    key_builder: Optional[Callable[..., str]] = None,
    use_l1: bool = True,
):
    """Decorator: cache the result of an async function under ``namespace``.

    - ``db`` and any ``AsyncSession`` positional args are stripped from the key.
    - ``key_builder(*args, **kwargs) -> str`` overrides the default arg-based key.
    - Falls through to the underlying call when caching is disabled / Redis is down.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            if not settings.CACHE_ENABLED:
                return await fn(*args, **kwargs)
            try:
                if key_builder is not None:
                    raw = key_builder(*args, **kwargs)
                else:
                    raw = _stable_args_key(args, kwargs, set(skip_args))
            except Exception:
                return await fn(*args, **kwargs)

            hit = await cache_get(namespace, raw, use_l1=use_l1)
            if hit is not _MISS:
                return hit  # type: ignore[return-value]

            result = await fn(*args, **kwargs)
            try:
                await cache_set(namespace, raw, result, ttl, use_l1=use_l1)
            except Exception as e:  # never fail the request because cache write failed
                logger.debug("Cache write failed in @cached(%s): %s", namespace, e)
            return result

        wrapper.__cache_namespace__ = namespace  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ─── Convenience helper for route-level caching ─────────────
async def get_or_compute(
    namespace: str,
    key: str,
    ttl: int,
    builder: Callable[[], Awaitable[T]],
    *,
    use_l1: bool = True,
) -> T:
    """Typed helper: return cached value, or call ``builder()`` and cache it."""
    if not settings.CACHE_ENABLED:
        return await builder()
    hit = await cache_get(namespace, key, use_l1=use_l1)
    if hit is not _MISS:
        return hit  # type: ignore[return-value]
    value = await builder()
    try:
        await cache_set(namespace, key, value, ttl, use_l1=use_l1)
    except Exception as e:
        logger.debug("Cache write failed in get_or_compute(%s): %s", namespace, e)
    return value


# ─── Namespace constants (single source of truth) ───────────
class NS:
    USER_BY_ID = "user_by_id"
    MANAGER_TEAM_EXECS = "manager_team_execs"
    MANAGER_CLIENTS = "manager_clients"
    EXEC_CLIENTS = "exec_clients"
    PARTNER_CLIENT_IDS = "partner_client_ids"
    MASTER_DOC_TYPES = "master_doc_types"
    MASTER_TEXT_FIELD_TYPES = "master_text_field_types"
    FORM_FIELDS = "form_fields"
    TAGS = "tags"
    EMAIL_CONFIG = "email_config"
    WHATSAPP_CONFIG = "whatsapp_config"
    DASHBOARD_SUMMARY = "dashboard_summary"
    REPORT = "report"
    CLIENT_LIST = "client_list"
