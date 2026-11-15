from api.ratelimit import RateLimiter


def test_rate_limiter_uses_a_sliding_window():
    now = [0.0]
    limiter = RateLimiter(limit=2, window_seconds=60, clock=lambda: now[0])
    assert limiter.allow("ip") and limiter.allow("ip")
    assert not limiter.allow("ip")
    assert limiter.allow("other-ip")
    now[0] = 61
    assert limiter.allow("ip")
