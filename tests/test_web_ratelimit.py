from __future__ import annotations

import pytest

from face_id_verification.web import app as app_module
from face_id_verification.web.ratelimit import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_SECONDS,
    RATE_LIMIT_ENV,
    RATE_WINDOW_ENV,
    TRUSTED_PROXY_HOSTS_ENV,
    SlidingWindowRateLimiter,
    client_identity,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def limiter(clock):
    return SlidingWindowRateLimiter(limit=3, window_seconds=60.0, clock=clock)


class TestSlidingWindowRateLimiter:
    def test_requests_within_the_limit_are_allowed(self, limiter):
        assert [limiter.acquire("a").allowed for _ in range(3)] == [True, True, True]

    def test_the_next_request_is_refused(self, limiter):
        for _ in range(3):
            limiter.acquire("a")
        decision = limiter.acquire("a")
        assert decision.allowed is False
        assert decision.retry_after > 0

    def test_window_slides_rather_than_resetting_on_a_boundary(self, limiter, clock):
        for _ in range(3):
            limiter.acquire("a")
        clock.advance(30)
        assert limiter.acquire("a").allowed is False
        clock.advance(31)
        assert limiter.acquire("a").allowed is True

    def test_retry_after_counts_down(self, limiter, clock):
        for _ in range(3):
            limiter.acquire("a")
        assert limiter.acquire("a").retry_after == 60
        clock.advance(45)
        assert limiter.acquire("a").retry_after == 15

    def test_clients_have_independent_budgets(self, limiter):
        for _ in range(3):
            limiter.acquire("a")
        assert limiter.acquire("a").allowed is False
        assert limiter.acquire("b").allowed is True

    def test_state_is_forgotten_once_the_window_passes(self, limiter, clock):
        for _ in range(3):
            limiter.acquire("a")
        clock.advance(61)
        assert limiter.acquire("a").allowed is True
        limiter.acquire("b")
        assert limiter.tracked_clients() == 2
        clock.advance(61)
        limiter.acquire("c")
        assert limiter.tracked_clients() == 1, "expired clients must not accumulate"

    def test_timestamps_per_client_stay_bounded_by_the_limit(self, clock):
        limiter = SlidingWindowRateLimiter(limit=4, window_seconds=60.0, clock=clock)
        for _ in range(200):
            limiter.acquire("a")
            clock.advance(0.01)
        assert len(limiter._hits["a"]) <= 4

    def test_tracked_clients_never_exceed_the_cap(self, clock):
        limiter = SlidingWindowRateLimiter(
            limit=2, window_seconds=600.0, max_clients=8, clock=clock
        )
        for index in range(200):
            limiter.acquire(f"client-{index}")
            clock.advance(0.01)
        assert limiter.tracked_clients() <= 8

    def test_decisions_are_deterministic(self, clock):
        results = []
        for _ in range(3):
            fresh = SlidingWindowRateLimiter(limit=2, window_seconds=10.0, clock=clock)
            results.append(
                [fresh.acquire("a").allowed for _ in range(4)]
            )
        assert results[0] == results[1] == results[2] == [True, True, False, False]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"limit": 0},
            {"limit": -1},
            {"window_seconds": 0},
            {"window_seconds": -5.0},
            {"max_clients": 0},
        ],
    )
    def test_invalid_configuration_rejected(self, kwargs):
        with pytest.raises(ValueError):
            SlidingWindowRateLimiter(**kwargs)


class TestEnvironmentConfiguration:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv(RATE_LIMIT_ENV, raising=False)
        monkeypatch.delenv(RATE_WINDOW_ENV, raising=False)
        limiter = app_module._build_rate_limiter()
        assert limiter.acquire("a").allowed is True
        assert limiter._limit == DEFAULT_LIMIT
        assert limiter._window == DEFAULT_WINDOW_SECONDS

    def test_configured_limit_is_honoured(self, monkeypatch):
        monkeypatch.setenv(RATE_LIMIT_ENV, "2")
        limiter = app_module._build_rate_limiter()
        assert [limiter.acquire("a").allowed for _ in range(3)] == [True, True, False]

    @pytest.mark.parametrize(
        ("variable", "value"),
        [(RATE_LIMIT_ENV, "banana"), (RATE_LIMIT_ENV, "0"), (RATE_LIMIT_ENV, "-3"),
         (RATE_WINDOW_ENV, "banana"), (RATE_WINDOW_ENV, "0"), (RATE_WINDOW_ENV, "-1")],
    )
    def test_unusable_configuration_falls_back_to_defaults(self, monkeypatch, variable, value):
        monkeypatch.setenv(variable, value)
        limiter = app_module._build_rate_limiter()
        assert limiter._limit == DEFAULT_LIMIT
        assert limiter._window == DEFAULT_WINDOW_SECONDS

    def test_trusted_proxies_are_parsed(self, monkeypatch):
        monkeypatch.delenv(TRUSTED_PROXY_HOSTS_ENV, raising=False)
        assert app_module._trusted_proxies() == frozenset()
        monkeypatch.setenv(TRUSTED_PROXY_HOSTS_ENV, "10.0.0.1, 10.0.0.2 ,")
        assert app_module._trusted_proxies() == frozenset({"10.0.0.1", "10.0.0.2"})


class TestClientIdentity:
    def test_peer_is_the_identity_by_default(self):
        assert client_identity("203.0.113.5", "1.2.3.4", frozenset()) == "203.0.113.5"

    def test_forged_forwarded_header_is_ignored_for_a_direct_caller(self):
        identity = client_identity("203.0.113.5", "1.2.3.4", frozenset({"10.0.0.1"}))
        assert identity == "203.0.113.5"

    def test_forwarded_header_used_when_the_peer_is_a_trusted_proxy(self):
        identity = client_identity("10.0.0.1", "1.2.3.4, 10.0.0.1", frozenset({"10.0.0.1"}))
        assert identity == "1.2.3.4"

    def test_missing_peer_falls_back_to_a_constant_key(self):
        assert client_identity(None, None, frozenset()) == "unknown"

    def test_credential_headers_cannot_change_identity(self):
        identity = client_identity("127.0.0.1", "Bearer nope", frozenset())
        assert identity == "127.0.0.1"
