"""The demo rig's two load-bearing behaviours.

`scripts/byok_live_test.py` is a test rig rather than shipped code, but these two
properties are the ones a demo depends on and the ones most likely to be undone
by a well-meaning edit:

1. The **caller** ends the call. The rig must never hang up on a person
   mid-sentence, and `max_length` is the only thing bounding the cost.
2. A lawful basis is wired through, and is **never invented** when unset.
"""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from vcon import Vcon

from core.lawful_basis import LawfulBasisConfig

RIG = Path(__file__).resolve().parent.parent / "scripts" / "byok_live_test.py"


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setenv("TELNYX_API_KEY", "KEYtest")
    spec = importlib.util.spec_from_file_location("byok_live_test", RIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def drive(rig, events):
    sent = []
    rig.command = lambda ccid, action, payload=None: sent.append((action, payload)) or {}
    client = TestClient(rig.build_app())
    for evt, payload in events:
        client.post("/hook", json={"data": {"event_type": evt, "payload": payload}})
    return sent


def test_the_caller_hangs_up_not_the_rig(rig):
    sent = drive(
        rig,
        [
            ("call.initiated", {"call_control_id": "c1", "direction": "incoming"}),
            ("call.answered", {"call_control_id": "c1"}),
            ("call.speak.ended", {"call_control_id": "c1"}),
        ],
    )
    actions = [a for a, _ in sent]
    assert actions == ["answer", "record_start", "speak"]
    assert "hangup" not in actions, "the rig cut off the caller"


def test_recording_is_bounded_and_dual_channel(rig):
    sent = drive(rig, [("call.answered", {"call_control_id": "c1"})])
    payload = dict(sent)["record_start"]
    assert payload["channels"] == "dual"
    assert payload["max_length"] == rig.MAX_SECONDS
    assert payload["max_length"] > 0, "an unbounded recording has no cost ceiling"


def test_an_invalid_basis_is_refused():
    with pytest.raises(ValueError):
        LawfulBasisConfig(lawful_basis="whatever-i-like")


def test_a_configured_basis_lands_and_an_unset_one_does_not():
    configured = Vcon.build_new()
    assert LawfulBasisConfig(lawful_basis="legitimate_interests").apply(configured)
    assert configured.find_attachment_by_purpose("lawful_basis") is not None

    unset = Vcon.build_new()
    LawfulBasisConfig(lawful_basis=None).apply(unset)
    assert unset.find_attachment_by_purpose("lawful_basis") is None, "a basis was invented"


def test_the_rig_passes_its_basis_to_the_builder(rig):
    assert "lawful_basis=LAWFUL" in RIG.read_text(), "builder no longer receives the basis"
