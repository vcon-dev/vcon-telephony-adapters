"""The Telnyx adapter emits core-05 vCons (syntax 0.5.0).

Output is validated against Appendix C of draft-ietf-vcon-vcon-core-05.
"""

import json
from pathlib import Path

import jsonschema
import pytest

from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder
from adapters.telnyx.call_session import CallSessionStore
from core.lawful_basis import LawfulBasisConfig

SCHEMA = json.loads(
    (Path(__file__).parents[2] / "schema" / "vcon-core-05-appendix-c-json-schema.json").read_text()
)
FIXTURES = Path(__file__).parent / "fixtures" / "live_call"
SESSION_ID = "db3dddae-ac92-11f1-b906-4e3d2db56c39"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def lifecycle_store(*extra):
    store = CallSessionStore()
    for name in ("call_initiated.json", "call_answered.json", "call_hangup.json"):
        store.record(load(name))
    for event in extra:
        store.record(event)
    return store


def refer(payload_extra, at="2026-09-09T21:10:30.000000Z"):
    return {
        "data": {
            "event_type": "call.refer.started",
            "occurred_at": at,
            "payload": {"call_session_id": SESSION_ID, **payload_extra},
        }
    }


def recording_event():
    return {
        "data": {
            "event_type": "call.recording.saved",
            "occurred_at": "2026-09-09T21:10:38Z",
            "payload": {
                "call_session_id": SESSION_ID,
                "recording_id": "rec-1",
                "recording_urls": {"wav": "https://s3.amazonaws.com/x?X-Amz-Signature=abc"},
                "recording_started_at": "2026-09-09T21:10:18Z",
                "recording_ended_at": "2026-09-09T21:10:37Z",
                "start_time": "2026-09-09T21:10:18Z",
            },
        }
    }


def build(store=None, lawful_basis=None):
    store = store or lifecycle_store()
    data = TelnyxRecordingData(recording_event(), session=store.get(SESSION_ID))
    # Inline audio: a bare `url` without `content_hash` is not schema-valid, and
    # that is the builder's existing media behavior, not what is tested here.
    builder = TelnyxVconBuilder(download_recordings=True, lawful_basis=lawful_basis)
    builder._download_recording = lambda _data: b"RIFF0000WAVE"
    vcon = builder.build(data)
    assert vcon is not None
    return vcon


def validate(vcon):
    jsonschema.validate(vcon.to_dict(), SCHEMA)
    ok, errors = vcon.is_valid()
    assert ok, errors


def test_one_session_receivers_and_contributors():
    vcon = build()
    validate(vcon)
    data = vcon.to_dict()
    assert data["vcon"] == "0.5.0"
    assert len(data["sessions"]) == 1
    session = data["sessions"][0]
    assert session["parties"] == [0, 1]
    assert session["dialog"] == [0]
    assert session["duration"] == pytest.approx(20.58, abs=0.01)
    assert session["session_id"]["local"] == SESSION_ID
    assert data["dialog"][0]["parties"] == [0, 1]
    for removed in ("originator", "party_history", "recordings", "recording_set"):
        assert removed not in data["dialog"][0]


def test_inbound_originator_is_on_the_session_not_the_dialog():
    data = build().to_dict()
    assert data["sessions"][0]["originator"] == 1


def test_join_and_drop_become_party_events():
    events = build().to_dict()["events"]
    assert [(e["type"], e["party"]) for e in events] == [
        ("join", 0),
        ("join", 1),
        ("drop", 0),
        ("drop", 1),
    ]
    assert all(e["sessions"] == 0 and "dialog" not in e for e in events)


def test_no_lifecycle_means_no_events_and_still_valid():
    event = recording_event()
    event["data"]["payload"].update({"from": "+15550001", "to": "+15550002"})
    builder = TelnyxVconBuilder(download_recordings=True)
    builder._download_recording = lambda _data: b"RIFF0000WAVE"
    vcon = builder.build(TelnyxRecordingData(event))
    validate(vcon)
    assert "events" not in vcon.to_dict() or vcon.to_dict()["events"] == []


def test_attachments_are_scoped_with_party_and_dialog_or_session():
    data = build().to_dict()
    assert data["attachments"]
    for attachment in data["attachments"]:
        assert "party" in attachment
        assert "dialog" in attachment or "session" in attachment
    trace = next(a for a in data["attachments"] if a["purpose"] == "sip-message-trace")
    assert trace["session"] == 0 and "dialog" not in trace
    assert "sip-signaling" in data["extensions"]


def test_lawful_basis_is_scoped_with_party_and_session():
    vcon = build(lawful_basis=LawfulBasisConfig(lawful_basis="consent"))
    validate(vcon)
    basis = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "lawful_basis")
    assert basis["party"] == 0
    assert basis["session"] in (0, [0])
    assert "dialog" not in basis


def test_no_lawful_basis_is_not_invented():
    purposes = [a["purpose"] for a in build().to_dict()["attachments"]]
    assert "lawful_basis" not in purposes


def test_blind_transfer_event():
    store = lifecycle_store(refer({"refer_to": "sip:+16175550199@sip.example.com"}))
    vcon = build(store)
    validate(vcon)
    data = vcon.to_dict()
    transfers = [e for e in data["events"] if e["type"].endswith("transfer")]
    assert len(transfers) == 1
    event = transfers[0]
    assert event["type"] == "blind-transfer"
    assert event["sessions"] == 0
    # inbound: the callee (our side) is the transferor, the caller the transferee
    assert event["party"] == 1 and event["transferee"] == 0
    assert data["parties"][event["transfer_target"]] == {"tel": "+16175550199"}
    assert "consultation" not in event
    target = data["sessions"][event["target_session"]]
    assert event["target_session"] != 0 and "dialog" not in target


def test_consultative_transfer_events():
    store = lifecycle_store(
        refer(
            {
                "refer_to": "+16175550199",
                "consultation_call_session_id": "consult-1",
                "consultation_started_at": "2026-09-09T21:10:25.000000Z",
            }
        )
    )
    vcon = build(store)
    validate(vcon)
    data = vcon.to_dict()
    start = next(e for e in data["events"] if e["type"] == "consultation-start")
    done = next(e for e in data["events"] if e["type"] == "consultative-transfer")
    assert start["consultation"] == done["consultation"]
    assert "target_session" not in start
    assert done["target_session"] not in (0, done["consultation"])
    assert start["time"] < done["time"]


def test_transfer_without_a_known_target_uses_an_empty_party():
    vcon = build(lifecycle_store(refer({})))
    validate(vcon)
    data = vcon.to_dict()
    event = next(e for e in data["events"] if e["type"] == "blind-transfer")
    assert data["parties"][event["transfer_target"]] == {}
