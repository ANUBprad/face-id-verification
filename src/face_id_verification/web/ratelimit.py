"""A bounded, deterministic admission limit for verification requests.

Verification is not cheap: it runs InsightFace inference, a paid reverse-image search, and
metadata crawling, and it may sign and broadcast a transaction. Without an admission
limit, any caller that can reach the service can spend the operator's money.

The limiter here is deliberately small and self-contained. It is **per-process and
in-memory**: with several worker processes each gets its own budget, so this is not a
distributed or global limit, and it resets when the process restarts.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_LIMIT = 10
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MAX_CLIENTS = 1024

TRUSTED_PROXY_HOSTS_ENV = "MUKHDAX_WEB_TRUSTED_PROXY_HOSTS"
RATE_LIMIT_ENV = "MUKHDAX_WEB_RATE_LIMIT"
RATE_WINDOW_ENV = "MUKHDAX_WEB_RATE_WINDOW_SECONDS"


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` requests per ``window_seconds`` for each client.

    A sliding window is used rather than a fixed one so a caller cannot send a full
    budget at the end of one window and another at the start of the next. Memory is
    bounded two ways: each client keeps at most ``limit`` timestamps, and the number of
    tracked clients never exceeds ``max_clients``.
    """

    def __init__(
        self,
        limit: int = DEFAULT_LIMIT,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if max_clients < 1:
            raise ValueError("max_clients must be at least 1")

        self._limit = limit
        self._window = float(window_seconds)
        self._max_clients = max_clients
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    def acquire(self, key: str) -> RateLimitDecision:
        """Record an attempt for ``key`` and report whether it is within the limit."""
        now = self._clock()
        cutoff = now - self._window

        hits = self._hits.get(key)
        if hits is None:
            self._prune(cutoff)
            if len(self._hits) >= self._max_clients:
                self._evict_least_recent()
            hits = deque()
            self._hits[key] = hits

        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= self._limit:
            return RateLimitDecision(
                allowed=False,
                retry_after=max(1, math.ceil(self._window - (now - hits[0]))),
            )

        hits.append(now)
        return RateLimitDecision(allowed=True, retry_after=0)

    def tracked_clients(self) -> int:
        return len(self._hits)

    def _prune(self, cutoff: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
            del self._hits[key]

    def _evict_least_recent(self) -> None:
        """Keep memory bounded even when every tracked client is still inside its window.

        Ties resolve to the earliest-inserted key, so the outcome does not depend on
        iteration accidents.
        """
        oldest = min(self._hits, key=lambda k: (self._hits[k][-1], k))
        del self._hits[oldest]


def client_identity(
    peer_host: str | None, forwarded_for: str | None, trusted_proxies: frozenset[str]
) -> str:
    """Decide whose budget a request spends.

    The transport peer is the identity, because it is the one address the service cannot
    be lied into. ``X-Forwarded-For`` is only believed when the immediate peer is an
    explicitly configured proxy, so a direct caller cannot forge a fresh identity simply
    by inventing a header.
    """
    peer = (peer_host or "").strip()
    if forwarded_for and peer in trusted_proxies:
        claimed = forwarded_for.split(",")[0].strip()
        if claimed:
            return claimed
    return peer or "unknown"
