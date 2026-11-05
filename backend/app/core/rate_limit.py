from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from app.core.config import settings

try:  # pragma: no cover - optional dependency path
    import redis
except Exception:  # pragma: no cover
    redis = None


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._redis = None
        self.backend = "memory"
        if settings.redis_url and redis is not None:
            try:
                client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
                self._redis = client
                self.backend = "redis"
            except Exception:
                self._redis = None

    def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after)."""
        if not settings.rate_limit_enabled:
            return True, limit, 0
        if self._redis is not None:
            try:
                bucket = f"rl:{key}:{int(time.time()) // window_seconds}"
                pipe = self._redis.pipeline()
                pipe.incr(bucket)
                pipe.expire(bucket, window_seconds + 1)
                count, _ = pipe.execute()
                remaining = max(0, limit - int(count))
                return int(count) <= limit, remaining, window_seconds
            except Exception:
                pass
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._events[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                retry = max(1, int(window_seconds - (now - q[0]))) if q else window_seconds
                return False, 0, retry
            q.append(now)
            return True, max(0, limit - len(q) - 1), window_seconds

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


limiter = RateLimiter()
