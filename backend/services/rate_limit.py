"""
rate_limit.py — Medication Orchestra

Per-user budgets for the expensive (and abusable) endpoints.

The previous backend had no limit of any kind: an authenticated account could
loop the scan endpoint, and each call cost two Vertex AI Vision invocations on
a service capped at five instances. That is a denial-of-wallet, not a denial of
service. Limits here are deliberately generous for a real household (a caregiver
photographs a handful of strips a week) and tight enough to bound the bill.

Counting is per UTC day and per minute, stored in memory per instance. That is
approximate across instances by design — it exists to bound cost, not to be an
exact quota — and `Retry-After` is returned so a client can back off correctly.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from fastapi import HTTPException

logger = logging.getLogger(__name__)


@dataclass
class Limits:
    per_minute: int
    per_day: int


#: endpoint key -> limits
LIMITS: dict[str, Limits] = {
    "scan": Limits(per_minute=6, per_day=120),
    "confirm": Limits(per_minute=12, per_day=300),
    "interactions": Limits(per_minute=12, per_day=300),
    "schedule": Limits(per_minute=6, per_day=120),
    "sos": Limits(per_minute=2, per_day=20),
    "sos_outbound": Limits(per_minute=4, per_day=60),
}

_WINDOW_SECONDS = 60
_DAY_SECONDS = 86400


@dataclass
class _Counter:
    minute_start: float = 0.0
    minute_count: int = 0
    day_start: float = 0.0
    day_count: int = 0

    def reset_if_needed(self, now: float) -> None:
        if now - self.minute_start >= _WINDOW_SECONDS:
            self.minute_start = now
            self.minute_count = 0
        if now - self.day_start >= _DAY_SECONDS:
            self.day_start = now
            self.day_count = 0


class RateLimiter:
    def __init__(self) -> None:
        self._counters: dict[tuple[str, str], _Counter] = {}
        self._lock = threading.Lock()

    def check(self, key: str, user_id: str) -> None:
        limits = LIMITS.get(key)
        if limits is None:
            return
        now = time.time()
        with self._lock:
            counter = self._counters.setdefault((key, user_id), _Counter())
            counter.reset_if_needed(now)
            if counter.minute_count >= limits.per_minute:
                retry = int(_WINDOW_SECONDS - (now - counter.minute_start)) + 1
                logger.warning("rate limit: %s per-minute exceeded by %s", key, user_id)
                raise HTTPException(
                    429,
                    detail=(
                        "Too many requests in a short time. Please wait "
                        f"{max(retry, 1)} seconds and try again."
                    ),
                    headers={"Retry-After": str(max(retry, 1))},
                )
            if counter.day_count >= limits.per_day:
                logger.warning("rate limit: %s daily budget exhausted by %s", key, user_id)
                raise HTTPException(
                    429,
                    detail=(
                        "You have reached today's limit for this action. It resets at "
                        "midnight UTC. If you need more, contact support."
                    ),
                )
            counter.minute_count += 1
            counter.day_count += 1

    def reset(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._counters.clear()
            else:
                for key in list(self._counters):
                    if key[1] == user_id:
                        del self._counters[key]


limiter = RateLimiter()
