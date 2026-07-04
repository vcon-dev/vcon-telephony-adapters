"""Session aggregation for Twilio messaging and conversations (optional)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.tracker import StateTracker

logger = logging.getLogger(__name__)


class SessionAggregator:
    """Track open messaging sessions for future amend/append workflows.

    MVP: one vCon per message. This class records session keys so a future
    phase can merge dialogs into a single vCon within the configured window.
    """

    def __init__(self, tracker: StateTracker, window_hours: int = 24):
        self.tracker = tracker
        self.window = timedelta(hours=window_hours)

    def session_key(self, *, from_addr: str, to_addr: str, channel: str) -> str:
        return f"msg:{channel}:{from_addr}:{to_addr}"

    def conversation_key(self, conversation_sid: str) -> str:
        return f"conv:{conversation_sid}"

    def touch_session(self, key: str, vcon_uuid: str) -> None:
        self.tracker.mark_processed(
            key,
            vcon_uuid,
            status="session_open",
            last_activity=datetime.now(timezone.utc).isoformat(),
        )

    def get_open_session_vcon(self, key: str) -> str | None:
        if not self.tracker.is_processed(key):
            return None
        meta = self.tracker.get_metadata(key) or {}
        ts = meta.get("last_activity") or meta.get("timestamp")
        if not ts:
            return self.tracker.get_vcon_uuid(key)
        try:
            last = datetime.fromisoformat(ts)
            if datetime.now(timezone.utc) - last <= self.window:
                return self.tracker.get_vcon_uuid(key)
        except ValueError:
            pass
        return None
