from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

BLOCKER_URL_PATTERNS = (
    "/checkpoint/",
    "/challenge/",
    "/authwall",
    "/captcha",
    "security-verification",
)


class PortalCircuitBreaker:
    """Enforces circadian rhythm windows and instant kill-switch on portal security challenges."""

    def __init__(self, timezone: str = "Asia/Kolkata") -> None:
        self.tz = ZoneInfo(timezone)
        self.tripped_portals: dict[str, str] = {}

    def is_within_circadian_window(self, now: datetime | None = None) -> bool:
        current = now or datetime.now(self.tz)
        hour_decimal = current.hour + current.minute / 60.0
        # Active window: 08:30 AM to 09:30 PM IST
        return 8.5 <= hour_decimal <= 21.5

    def inspect_url_for_blockers(self, portal: str, current_url: str) -> bool:
        lower_url = current_url.lower()
        for pattern in BLOCKER_URL_PATTERNS:
            if pattern in lower_url:
                self.tripped_portals[portal] = f"Blocker URL pattern detected: {pattern} in {current_url}"
                return True
        return False

    def inspect_http_status(self, portal: str, status_code: int) -> bool:
        if status_code in (403, 429):
            self.tripped_portals[portal] = f"HTTP {status_code} anomaly detected"
            return True
        return False

    def can_execute(self, portal: str, now: datetime | None = None) -> tuple[bool, str]:
        if portal in self.tripped_portals:
            return False, self.tripped_portals[portal]
        if not self.is_within_circadian_window(now):
            return False, "Outside IST circadian execution window (08:30 - 21:30 IST)"
        return True, "OK"
