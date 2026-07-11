"""Tiny in-process TTL cache -- a dict and a clock, nothing more.

Data changes at most daily (the collection pipeline runs on a cron), so a
10-minute TTL is generous headroom, not a freshness risk. This is
deliberately not Redis or anything shared: the API is a single process, and
if it's ever scaled to multiple workers each just gets its own cache (a
strictly acceptable tradeoff at this data-change frequency).
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

DEFAULT_TTL_SECONDS = 600  # 10 minutes

_store: dict[str, tuple[float, Any]] = {}


def cached(ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Callable:
    """Decorator: cache a function's return value per distinct (args,
    kwargs) call signature for ttl_seconds. Works on both plain functions
    (queries.py fetchers) and FastAPI route handlers (their query-parsed
    arguments become **kwargs, which is exactly what we want the cache key
    to vary on)."""

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__module__}.{fn.__qualname__}:{args!r}:{sorted(kwargs.items())!r}"
            now = time.monotonic()
            hit = _store.get(key)
            if hit is not None:
                cached_at, value = hit
                if now - cached_at < ttl_seconds:
                    return value
            value = fn(*args, **kwargs)
            _store[key] = (now, value)
            return value

        return wrapper

    return decorator


def cache_stats() -> dict:
    """For /system/health-style introspection, not exposed as its own endpoint."""
    return {"entries": len(_store)}


def clear_cache() -> None:
    _store.clear()
