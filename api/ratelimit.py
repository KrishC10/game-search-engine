"""In-memory sliding-window rate limiter, per client IP.

Enough for a single API process. Behind several workers or a load balancer,
move the counts to a shared store such as Redis.
"""
import time
from collections import defaultdict, deque
from threading import Lock
from typing import Callable, Deque, Dict


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self.calls: Dict[str, Deque[float]] = defaultdict(deque)
        self.lock = Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self.lock:
            calls = self.calls[key]
            while calls and calls[0] <= now - self.window:
                calls.popleft()
            if len(calls) >= self.limit:
                return False
            calls.append(now)
            return True
