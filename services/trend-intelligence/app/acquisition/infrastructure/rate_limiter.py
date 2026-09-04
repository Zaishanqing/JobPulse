from __future__ import annotations

import time
from threading import Lock


class RateLimiter:
    def __init__(self, requests_per_second: float, burst: int = 5) -> None:
        self.rate = requests_per_second
        self.burst = burst
        self.tokens = float(burst)
        self.max_tokens = float(burst)
        self.last_refill = time.monotonic()
        self.lock = Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens < 1.0:
                wait = (1.0 - self.tokens) / self.rate
                time.sleep(wait)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0
