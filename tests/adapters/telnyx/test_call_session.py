"""Lifecycle correlation: making a recording know whose call it was.

`call.recording.saved` carries no `from`, `to` or `direction` (CON-844, verified
against a live call). Those arrive on the lifecycle events, keyed by the same
`call_session_id`.

The fixtures here are real payloads captured from a live Telnyx call on
2026-09-09, trimmed to the fields under test.
"""

import json
import time
from pathlib import Path

import pytest

from adapters.telnyx.call_session import (
    LIFECYCLE_EVENTS,
    CallSessionStore,
    sip_signaling_attachment,
)

SESSION_ID = "db3dddae-ac92-11f1-b906-4e3d2db56c39"
FROM = "+15083649972"
TO = "+14012041080"

CALL_START = "2026-09-09T21:10:16.845276Z"
ANSWERED = "2026-09-09T21:10:18.025270Z"
ENDED = "2026-09-09T21:10:37.425279Z"


def event(event_type, occurred_at, **payload):
    base = {"call_session_id": SESSION_ID, "call_control_id": "v3:abc"}
    base.update(payload)
    return {"data": {"event_type": event_type, "occurred_at": occurred_at, "payload": base}}


def initiated():
    return event(
        "call.initiated",
        CALL_START,
        **{
            "from": FROM,
            "to": TO,
            "direction": "incoming",
            "start_time": CALL_START,
            "caller_id_name": FROM,
            "from_sip_uri": f"{FROM}@64.133.0.149",
            "to_sip_uri": f"{TO}@192.76.120.9",
            "custom_headers": [{"name": "P-Early-Media", "value": "supported"}],
        },
    )


def answered():
    return event(
        "call.answered",
        ANSWERED,
        **{
            "from": FROM,
            "to": TO,
            "start_time": CALL_START,
            "codec": "PCMU",
            "sampling_rate": 8000,
        },
    )


def hangup():
    return event(
        "call.hangup",
        ENDED,
        **{
            "from": FROM,
            "to": TO,
            "start_time": CALL_START,
            "end_time": ENDED,
            "hangup_cause": "normal_clearing",
            "hangup_source": "callee",
            "sip_hangup_cause": "unspecified",
            "call_quality_stats": {"inbound": {"mos": "4.50"}},
        },
    )


def recording_saved():
    """Note the trap: `start_time` here is the *recording* start, not the call's."""
    return event(
        "call.recording.saved",
        "2026-09-09T21:10:38.225422Z",
        recording_id="rec-1",
        start_time="2026-09-09T21:10:18.738773Z",
    )


# -- accumulation ----------------------------------------------------------


def test_parties_resolved_from_lifecycle_events():
    store = CallSessionStore()
    store.record(initiated())
    session = store.get(SESSION_ID)

    assert session.from_number == FROM
    assert session.to_number == TO
    assert session.direction == "incoming"
    assert session.has_parties


def test_later_events_do_not_blank_earlier_fields():
    """`call.hangup` omits `direction`; it must not erase it."""
    store = CallSessionStore()
    store.record(initiated())
    store.record(hangup())

    assert store.get(SESSION_ID).direction == "incoming"


def test_recording_event_is_not_folded_into_the_session():
    """Regression, and a subtle one.

    `payload.start_time` means the *call* start on every lifecycle event but
    the *recording* start on `call.recording.saved`. Folding the recording in
    overwrote the call start with a later timestamp, producing a session whose
    call apparently started after it was answered.
    """
    store = CallSessionStore()
    store.record(initiated())
    store.record(recording_saved())

    assert store.get(SESSION_ID).start_time == CALL_START
    assert len(store) == 1


def test_recording_saved_is_not_a_lifecycle_event():
    assert "call.recording.saved" not in LIFECYCLE_EVENTS


def test_answer_time_comes_from_the_envelope_not_the_payload():
    """`payload.start_time` is identical on every event, so it cannot be the
    answer time. `occurred_at` is when the answer actually happened."""
    store = CallSessionStore()
    store.record(initiated())
    store.record(answered())
    session = store.get(SESSION_ID)

    assert session.answered_at == ANSWERED
    assert session.answered_at != session.start_time


def test_ring_duration_is_derived():
    store = CallSessionStore()
    store.record(initiated())
    store.record(answered())

    assert store.get(SESSION_ID).ring_seconds == pytest.approx(1.18)


def test_timestamps_are_in_a_sane_order():
    store = CallSessionStore()
    for e in (initiated(), answered(), hangup()):
        store.record(e)
    s = store.get(SESSION_ID)

    assert s.start_time < s.answered_at < s.end_time


def test_disposition_captured_from_hangup():
    store = CallSessionStore()
    store.record(initiated())
    store.record(hangup())
    s = store.get(SESSION_ID)

    assert s.hangup_cause == "normal_clearing"
    assert s.hangup_source == "callee"
    assert s.call_quality_stats["inbound"]["mos"] == "4.50"


def test_dtmf_digits_accumulate_in_order():
    store = CallSessionStore()
    store.record(initiated())
    for digit in "417":
        store.record(event("call.dtmf.received", CALL_START, digit=digit))

    assert store.get(SESSION_ID).dtmf_digits == ["4", "1", "7"]


def test_event_without_a_session_id_is_ignored():
    store = CallSessionStore()
    assert store.record({"data": {"event_type": "call.initiated", "payload": {}}}) is None
    assert len(store) == 0


# -- both arrival orders (the card's explicit requirement) -----------------


def test_hangup_before_recording():
    """The order actually observed live: hangup, then recording ~1s later."""
    store = CallSessionStore()
    for e in (initiated(), answered(), hangup()):
        store.record(e)

    session = store.get(SESSION_ID)
    assert session.has_parties
    assert session.hangup_cause == "normal_clearing"


def test_recording_before_hangup():
    """The other order. Parties resolve; disposition is simply not known yet.

    Deliberately no waiting: the vCon is emitted with what is known rather
    than delaying every call for an event that may never come.
    """
    store = CallSessionStore()
    store.record(initiated())
    store.record(answered())
    store.record(recording_saved())  # ignored, but arrives first

    session = store.get(SESSION_ID)
    assert session.has_parties, "parties must resolve regardless of ordering"
    assert session.hangup_cause == "", "disposition is not yet known"

    store.record(hangup())
    assert store.get(SESSION_ID).hangup_cause == "normal_clearing"


# -- housekeeping ----------------------------------------------------------


def test_sessions_expire():
    store = CallSessionStore(ttl_seconds=0)
    store.record(initiated())
    time.sleep(0.01)
    assert len(store) == 0


def test_session_discarded_after_use():
    store = CallSessionStore()
    store.record(initiated())
    store.discard(SESSION_ID)
    assert store.get(SESSION_ID) is None


# -- the sip-signaling attachment ------------------------------------------


def test_sip_signaling_body_shape():
    store = CallSessionStore()
    for e in (initiated(), answered(), hangup()):
        store.record(e)
    body = sip_signaling_attachment(store.get(SESSION_ID))

    assert body["call_id"] == SESSION_ID
    assert body["remote_uri"] == f"{FROM}@64.133.0.149"
    assert body["disposition"]["hangup_cause"] == "normal_clearing"
    assert body["disposition"]["codec"] == "PCMU"
    assert body["timing"]["ring_seconds"] == pytest.approx(1.18)
    assert body["sip_headers"][0]["name"] == "P-Early-Media"
    json.dumps(body)  # must be serialisable; it ships as an encoded attachment


def test_sip_signaling_omits_what_it_does_not_know():
    """A half-known call should not emit empty disposition keys."""
    store = CallSessionStore()
    store.record(initiated())
    body = sip_signaling_attachment(store.get(SESSION_ID))

    assert "hangup_cause" not in body.get("disposition", {})
    assert "ring_seconds" not in body.get("timing", {})


# -- end to end, against the real captured events --------------------------

EVENTS_DIR = Path(__file__).parent / "fixtures" / "live_call"


@pytest.mark.skipif(not EVENTS_DIR.exists(), reason="live capture not vendored")
def test_replay_of_a_real_call_names_its_parties():
    store = CallSessionStore()
    for path in sorted(EVENTS_DIR.glob("*.json")):
        store.record(json.loads(path.read_text()))

    session = store.get(SESSION_ID)
    assert session is not None and session.has_parties
    assert session.start_time < session.answered_at < session.end_time
