"""Application-local time abstraction.

The service must be independently runnable and must not depend on the control
plane that governs it.  These few duplicated lines are a deliberate trade for
a one-way dependency from the governing control plane to the product.
"""

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def iso(self) -> str: ...


class SystemClock:
    def iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
