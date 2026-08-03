from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def monotonic_now() -> float:
    return time.monotonic()


def elapsed_s(start_monotonic_s: float, end_monotonic_s: float | None = None) -> float:
    end_value = monotonic_now() if end_monotonic_s is None else end_monotonic_s
    return end_value - start_monotonic_s


@dataclass(frozen=True)
class MonotonicDeadline:
    start_s: float
    timeout_s: float

    @property
    def due_s(self) -> float:
        return self.start_s + self.timeout_s

    def remaining_s(self, now_s: float | None = None) -> float:
        now_value = monotonic_now() if now_s is None else now_s
        return self.due_s - now_value

    def is_expired(self, now_s: float | None = None) -> bool:
        return self.remaining_s(now_s=now_s) <= 0.0


def make_deadline(timeout_s: float, now_s: float | None = None) -> MonotonicDeadline:
    start = monotonic_now() if now_s is None else now_s
    return MonotonicDeadline(start_s=start, timeout_s=timeout_s)

