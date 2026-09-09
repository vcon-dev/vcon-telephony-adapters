"""Accumulate Call Control lifecycle events so a recording knows whose call it was.

`call.recording.saved` carries no `from`, `to`, or `direction` (verified against
a live call, CON-844). Those facts arrive earlier, on `call.initiated`,
`call.answered` and `call.hangup`, keyed by the same `call_session_id`.

Two sources, deliberately layered:

* **This store** — everything the lifecycle events know: party identity,
  direction, ring/answer/end timing, hangup cause, SIP headers, negotiated
  codec, carrier quality stats. In memory, so a restart loses it.
* **The recordings API** (`TelnyxProvisioner.get_recording`) — `from`, `to` and
  duration, authoritative and stateless.

Party identity is the part a vCon cannot do without, so it has the stateless
fallback. Everything else is enrichment, and losing it on a restart is an
acceptable trade against running a database for it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# How long a session is kept after its last event. A recording webhook lands
# a second or two after hangup; an hour is generous cover for a retry storm
# without letting the store grow without bound.
DEFAULT_TTL_SECONDS = 3600

# Only these are folded into a session. `call.recording.saved` must NOT be:
# its `payload.start_time` is the *recording* start, not the call start, so
# folding it silently overwrites the call's own start time with a later one.
LIFECYCLE_EVENTS = frozenset(
    {
        "call.initiated",
        "call.answered",
        "call.bridged",
        "call.hangup",
        "call.machine.detection.ended",
        "call.dtmf.received",
    }
)


@dataclass
class CallSession:
    """What the lifecycle events told us about one call."""

    call_session_id: str
    from_number: str = ""
    to_number: str = ""
    direction: str = ""
    caller_id_name: str = ""
    from_sip_uri: str = ""
    to_sip_uri: str = ""
    start_time: str = ""
    answered_at: str = ""
    end_time: str = ""
    hangup_cause: str = ""
    hangup_source: str = ""
    sip_hangup_cause: str = ""
    codec: str = ""
    sampling_rate: Any = None
    custom_headers: list[dict[str, Any]] = field(default_factory=list)
    call_quality_stats: dict[str, Any] = field(default_factory=dict)
    dtmf_digits: list[str] = field(default_factory=list)
    call_control_id: str = ""
    connection_id: str = ""
    last_seen: float = field(default_factory=time.monotonic)

    def apply(self, event_type: str, payload: dict[str, Any], occurred_at: str = "") -> None:
        """Fold one lifecycle event in.

        Later events win only where they carry a value, so a field set on
        `call.initiated` is not blanked by a `call.hangup` that omits it.
        """
        self.last_seen = time.monotonic()

        def take(attr: str, key: str) -> None:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                setattr(self, attr, value)

        take("from_number", "from")
        take("to_number", "to")
        take("direction", "direction")
        take("caller_id_name", "caller_id_name")
        take("from_sip_uri", "from_sip_uri")
        take("to_sip_uri", "to_sip_uri")
        take("start_time", "start_time")
        take("call_control_id", "call_control_id")
        take("connection_id", "connection_id")
        take("custom_headers", "custom_headers")
        take("codec", "codec")
        take("sampling_rate", "sampling_rate")

        if event_type == "call.answered":
            # `payload.start_time` is the *call* start and is identical on every
            # lifecycle event, so it cannot be the answer time. The envelope's
            # `occurred_at` is when the answer actually happened, which is what
            # makes ring duration computable.
            self.answered_at = occurred_at or self.answered_at

        if event_type == "call.dtmf.received":
            digit = payload.get("digit")
            if digit:
                self.dtmf_digits.append(str(digit))

        if event_type == "call.hangup":
            take("end_time", "end_time")
            take("hangup_cause", "hangup_cause")
            take("hangup_source", "hangup_source")
            take("sip_hangup_cause", "sip_hangup_cause")
            take("call_quality_stats", "call_quality_stats")

    @property
    def ring_seconds(self) -> float | None:
        """Time between call start and answer, if both are known."""
        if not (self.start_time and self.answered_at):
            return None
        try:
            from datetime import datetime

            t0 = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(self.answered_at.replace("Z", "+00:00"))
            return round((t1 - t0).total_seconds(), 3)
        except (ValueError, AttributeError):
            return None

    @property
    def has_parties(self) -> bool:
        return bool(self.from_number and self.to_number)


class CallSessionStore:
    """Lifecycle events keyed by `call_session_id`, swept by age.

    Thread-safe because a webhook receiver serves events concurrently, and
    two legs of the same call can land at once.
    """

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, CallSession] = {}
        self._lock = threading.Lock()

    def record(self, event: dict[str, Any]) -> CallSession | None:
        """Fold a whole webhook event into its session, creating it if new.

        Takes the full event rather than the payload because the answer time
        lives on the envelope (`occurred_at`), not inside the payload.
        """
        data = event.get("data", event)
        event_type = data.get("event_type", "")
        payload = data.get("payload", {})

        if event_type not in LIFECYCLE_EVENTS:
            return None

        session_id = payload.get("call_session_id")
        if not session_id:
            logger.debug("%s carried no call_session_id; not tracked", event_type)
            return None

        with self._lock:
            self._sweep_locked()
            session = self._sessions.get(session_id)
            if session is None:
                session = CallSession(call_session_id=session_id)
                self._sessions[session_id] = session
            session.apply(event_type, payload, data.get("occurred_at", ""))
            return session

    def get(self, session_id: str) -> CallSession | None:
        if not session_id:
            return None
        with self._lock:
            self._sweep_locked()
            return self._sessions.get(session_id)

    def discard(self, session_id: str) -> None:
        """Drop a session once its vCon has been emitted."""
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            self._sweep_locked()
            return len(self._sessions)

    def _sweep_locked(self) -> None:
        """Evict expired sessions. Lazy, so there is no background thread."""
        cutoff = time.monotonic() - self.ttl_seconds
        expired = [k for k, s in self._sessions.items() if s.last_seen < cutoff]
        for key in expired:
            del self._sessions[key]
        if expired:
            logger.debug("Swept %d expired call session(s)", len(expired))


def sip_signaling_attachment(session: CallSession) -> dict[str, Any]:
    """A `sip-message-trace` body for the sip-signaling extension.

    Per draft-howe-vcon-sip-signaling, which the SIPREC SRS already emits, so
    a Telnyx-sourced vCon and a SIPREC-sourced one describe signalling the same
    way. Call disposition belongs here rather than in ad-hoc tags.
    """
    body: dict[str, Any] = {
        "version": "1.0",
        "call_id": session.call_session_id,
        "remote_uri": session.from_sip_uri or None,
        "local_uri": session.to_sip_uri or None,
        "media_streams": [],
    }

    disposition = {
        k: v
        for k, v in (
            ("direction", session.direction),
            ("hangup_cause", session.hangup_cause),
            ("hangup_source", session.hangup_source),
            ("sip_hangup_cause", session.sip_hangup_cause),
            ("caller_id_name", session.caller_id_name),
            ("codec", session.codec),
            ("sampling_rate", session.sampling_rate),
        )
        if v
    }
    if disposition:
        body["disposition"] = disposition

    timing = {
        k: v
        for k, v in (
            ("start_time", session.start_time),
            ("answered_at", session.answered_at),
            ("end_time", session.end_time),
        )
        if v
    }
    if session.ring_seconds is not None:
        timing["ring_seconds"] = session.ring_seconds
    if timing:
        body["timing"] = timing

    if session.dtmf_digits:
        body["dtmf"] = "".join(session.dtmf_digits)

    if session.custom_headers:
        body["sip_headers"] = session.custom_headers

    return body


SIP_SIGNALING_EXTENSION = "sip-signaling"


def declare_extension(vcon_dict: dict[str, Any], name: str) -> None:
    """Add `name` to the vCon's top-level `extensions[]` if not already there."""
    extensions = vcon_dict.setdefault("extensions", [])
    if name not in extensions:
        extensions.append(name)
