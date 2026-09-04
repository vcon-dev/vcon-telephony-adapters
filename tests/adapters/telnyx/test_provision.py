"""Tests for BYOK provisioning against a fake Telnyx.

No network, no API key. A FakeSession records requests and replays canned
responses, so the request shapes and the decision logic are both checked.
"""

import json

import pytest

from adapters.telnyx.provision import (
    Connector,
    TelnyxError,
    TelnyxProvisioner,
    handle_call_event,
)

# Deliberately not shaped like a real Telnyx key (those start with "KEY") so
# secret scanners do not flag this file.
KEY = "test-key-placeholder-do-not-use"


class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = {} if body is None else body
        self.content = json.dumps(self._body).encode() if body is not None else b""
        self.text = json.dumps(self._body) if body is not None else ""

    def json(self):
        return self._body


class FakeSession:
    """Replays queued responses and records what was asked of it."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers})
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {"data": {}})


def connectors(*items):
    return FakeResponse(200, {"data": list(items)})


def make(responses):
    session = FakeSession(responses)
    return TelnyxProvisioner(KEY, session=session), session


# -- connectors ------------------------------------------------------------


def test_ensure_connector_creates_when_absent():
    p, s = make([connectors(), FakeResponse(200, {"data": {"name": "st", "host": "srs.example", "port": 5060}})])

    c = p.ensure_connector("st", "srs.example", 5060)

    assert c == Connector("st", "srs.example", 5060)
    assert s.calls[1]["method"] == "POST"
    assert s.calls[1]["url"].endswith("/siprec_connectors")
    assert s.calls[1]["json"] == {"name": "st", "host": "srs.example", "port": 5060}


def test_ensure_connector_is_idempotent_when_already_correct():
    """Re-running provisioning must not thrash the customer's account."""
    p, s = make([connectors({"name": "st", "host": "srs.example", "port": 5060})])

    c = p.ensure_connector("st", "srs.example", 5060)

    assert c == Connector("st", "srs.example", 5060)
    assert len(s.calls) == 1, "should have read only, not written"


def test_ensure_connector_repoints_when_host_changed():
    """A tenant that moves droplets must not silently keep forking to the old one."""
    p, s = make([
        connectors({"name": "st", "host": "old.example", "port": 5060}),
        FakeResponse(200, {"data": {"name": "st", "host": "new.example", "port": 5060}}),
    ])

    c = p.ensure_connector("st", "new.example", 5060)

    assert c.host == "new.example"
    assert s.calls[1]["method"] == "PATCH"


def test_delete_connector_reports_when_already_gone():
    p, _ = make([connectors()])
    assert p.delete_connector("st") is False


# -- per-call actions ------------------------------------------------------


def test_start_siprec_sends_expected_body():
    p, s = make([FakeResponse(200, {"data": {}})])

    p.start_siprec("ccid-1", "st")

    assert s.calls[0]["url"].endswith("/calls/ccid-1/actions/siprec_start")
    assert s.calls[0]["json"] == {
        "connector_name": "st",
        "siprec_track": "both_tracks",
        "sip_transport": "udp",
    }


# -- key handling ----------------------------------------------------------


def test_api_key_is_sent_as_bearer():
    p, s = make([connectors()])
    p.check_key()
    assert s.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"


def test_bad_key_gives_a_clear_error():
    p, _ = make([FakeResponse(401, {"errors": []})])
    with pytest.raises(TelnyxError, match="rejected the API key"):
        p.check_key()


def test_customer_key_never_appears_in_errors():
    """BYOK: the key is the customer's. It must not leak into logs or tracebacks."""
    p, _ = make([FakeResponse(500, {"echo": f"Bearer {KEY}"})])

    with pytest.raises(TelnyxError) as exc:
        p.check_key()

    assert KEY not in str(exc.value)
    assert "***" in str(exc.value)


def test_empty_key_is_refused_up_front():
    with pytest.raises(ValueError):
        TelnyxProvisioner("")


# -- webhook decision logic ------------------------------------------------


def answered(ccid="ccid-1"):
    return {"data": {"event_type": "call.answered", "payload": {"call_control_id": ccid}}}


def test_forks_on_answered():
    p, s = make([FakeResponse(200, {"data": {}})])
    assert handle_call_event(answered(), p, "st") == "ccid-1"
    assert s.calls[0]["url"].endswith("/actions/siprec_start")


def test_ignores_events_we_do_not_fork_on():
    p, s = make([])
    event = {"data": {"event_type": "call.hangup", "payload": {"call_control_id": "x"}}}

    assert handle_call_event(event, p, "st") is None
    assert s.calls == [], "must not call Telnyx for events we ignore"


def test_transcription_started_only_when_asked():
    p, s = make([FakeResponse(200, {"data": {}}), FakeResponse(200, {"data": {}})])

    handle_call_event(answered(), p, "st", transcribe=True)

    assert s.calls[1]["url"].endswith("/actions/transcription_start")


def test_a_telnyx_failure_does_not_propagate():
    """Losing a recording is recoverable. Raising into the webhook path is not."""
    p, _ = make([FakeResponse(500, {"error": "boom"})])

    assert handle_call_event(answered(), p, "st") is None


def test_missing_call_control_id_is_survivable():
    p, s = make([])
    event = {"data": {"event_type": "call.answered", "payload": {}}}

    assert handle_call_event(event, p, "st") is None
    assert s.calls == []


def test_accepts_flat_payload_shape():
    """Telnyx nests under `data`; be tolerant of an already-unwrapped body."""
    p, _ = make([FakeResponse(200, {"data": {}})])
    event = {"event_type": "call.answered", "payload": {"call_control_id": "flat-1"}}

    assert handle_call_event(event, p, "st") == "flat-1"
