"""The screening outcome in a `voice-screen` analysis entry, from `Annotation-screen_result`.

A relaying receiver adds `Annotation-screen_result` (one compact JSON object) beside the tenant
fields on the recording forward. It reaches the vCon only as one `analysis` entry of type
`voice-screen`, and only from a request the route trusted as relayed, with
`TELNYX_SCREEN_RESULT_FROM_RELAY` on, beside a tenant that counts, and valid. It is never a
tag. Every case goes through the real `/webhook/texml-recording` handler and the real
`TelnyxVconBuilder`, from loopback unless it says otherwise; only the conserver post is captured.
"""

import json
import logging
import urllib.parse
from pathlib import Path

import pytest

from adapters.telnyx.builder import (
    SCREEN_RESULT_LIMIT,
    TelnyxRecordingData,
    TelnyxVconBuilder,
    relayed_screen_result,
    screen_result_body,
)

from .tenant_cases import (
    CARRIERS,
    FORMS,
    INVALID,
    REMOTE,
    TENANT,
    golden_path,
    normalized,
    post_forward,
)

TOKEN = "synthetic-callback-token"
FIELD = "Annotation-screen_result"
ON = {"TELNYX_SCREEN_RESULT_FROM_RELAY": "1"}

ACTED = {
    "status": "acted",
    "mode": "listen",
    "threshold": 0.8,
    "windows": 2,
    "frames": 5,
    "synthetic_frames": 2,
    "max_confidence": 0.97,
    "decided_at": "2026-11-20T15:04:05.123Z",
    "action": "bot",
    "action_result": "accepted",
    "acted_at": "2026-11-20T15:04:05.400Z",
    "telnyx_status": 200,
}
CLEAN = {
    "status": "clean",
    "mode": "prompt_first",
    "threshold": 0.9,
    "windows": 1,
    "frames": 40,
    "synthetic_frames": 0,
    "max_confidence": None,
    "decided_at": None,
    "action": None,
    "action_result": None,
    "acted_at": None,
    "telnyx_status": None,
}
ENTRY = {
    "type": "voice-screen",
    "dialog": 0,
    "vendor": "modulate",
    "product": "velma-2-synthetic-voice-detection-streaming",
    "schema": "voice-screen-result-v1",
    "encoding": "json",
}


def _compact(doc):
    return json.dumps(doc, separators=(",", ":"))


def _body(carrier, tenant=TENANT, *results, extra=()):
    """The forward as the receiver sends it: the tenant, then the screening field(s)."""
    pairs = list(FORMS[carrier])
    if tenant is not None:
        pairs.append(("Annotation-tenant", tenant))
    pairs += [(FIELD, r if isinstance(r, str) else _compact(r)) for r in results]
    pairs += list(extra)
    return urllib.parse.urlencode(pairs).encode()


def _post(tmp_path, carrier="telnyx", tenant=TENANT, *results, **kwargs):
    return post_forward(_body(carrier, tenant, *results), tmp_path, **{**ON, **kwargs})


def _analysis(vcon):
    return vcon.to_dict()["analysis"]


def _tags(vcon):
    tags = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    return dict(t.split(":", 1) for t in tags["body"])


def _no_trace(vcon, *needles):
    """The outcome is nowhere in the vCon: not as an analysis entry, a tag, or any text."""
    assert _analysis(vcon) == []
    assert not [k for k in _tags(vcon) if "screen" in k.lower()]
    wire = vcon.to_json()
    for needle in needles or ("voice-screen", "screen_result", "synthetic_frames", "modulate"):
        assert needle not in wire


def _event():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )


# ---- trusted relay, setting on: the entry is written


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_trusted_relay_s_outcome_becomes_one_voice_screen_entry(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT, ACTED)

    assert _analysis(vcon) == [{**ENTRY, "body": ACTED}]
    assert list(_analysis(vcon)[0]["body"]) == list(ACTED)


@pytest.mark.parametrize("peer", [("127.0.0.1", 40000), ("::1", 40000)])
def test_a_loopback_peer_with_no_token_counts(tmp_path, peer):
    vcon = _post(tmp_path, "telnyx", TENANT, CLEAN, peer=peer)
    assert _analysis(vcon) == [{**ENTRY, "body": CLEAN}]


def test_a_matched_token_counts_from_any_peer(tmp_path):
    vcon = _post(
        tmp_path,
        "telnyx",
        TENANT,
        ACTED,
        peer=REMOTE,
        query=f"token={TOKEN}",
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
    )
    assert _analysis(vcon) == [{**ENTRY, "body": ACTED}]


@pytest.mark.parametrize(
    "doc",
    [
        {"status": "missing"},
        {
            "status": "missing",
            "action": "stop",
            "action_result": "refused",
            "acted_at": None,
            "telnyx_status": None,
        },
    ],
)
def test_a_missing_result_is_written_as_it_was_sent(tmp_path, doc):
    vcon = _post(tmp_path, "telnyx", TENANT, doc)
    assert _analysis(vcon) == [{**ENTRY, "body": doc}]


def test_the_entry_leaves_the_rest_of_the_vcon_as_it_was(tmp_path):
    (tmp_path / "plain").mkdir()
    plain = _post(tmp_path / "plain", "telnyx", TENANT)
    with_result = _post(tmp_path, "telnyx", TENANT, ACTED)

    doc = json.loads(normalized(with_result))
    assert doc["analysis"] == [{**ENTRY, "body": ACTED}]
    doc["analysis"] = []
    assert json.dumps(doc) == normalized(plain)


def test_the_entry_rides_beside_the_addons_and_the_tenant(tmp_path):
    body = urllib.parse.urlencode(
        [
            *FORMS["telnyx"],
            ("Annotation-tenant", TENANT),
            ("Annotation-addons", "voice-screen"),
            (FIELD, _compact(ACTED)),
        ]
    ).encode()
    vcon = post_forward(body, tmp_path, TELNYX_ADDONS_FROM_RELAY="1", **ON)

    [tenant] = [a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tenant"]
    assert tenant["body"] == {"id": TENANT, "addons": ["voice-screen"]}
    assert _analysis(vcon) == [{**ENTRY, "body": ACTED}]


def test_the_same_value_twice_counts_once(tmp_path):
    vcon = _post(tmp_path, "telnyx", TENANT, ACTED, ACTED)
    assert _analysis(vcon) == [{**ENTRY, "body": ACTED}]


# ---- the setting is off (the default): nothing is written


@pytest.mark.parametrize("value", [None, "", "false", "0", "no"])
def test_with_the_setting_off_nothing_is_written(tmp_path, caplog, value):
    caplog.set_level(logging.INFO, logger="adapters.telnyx.webhook")
    vcon = _post(tmp_path, "telnyx", TENANT, ACTED, TELNYX_SCREEN_RESULT_FROM_RELAY=value)

    _no_trace(vcon)
    [line] = [r.getMessage() for r in caplog.records if FIELD in r.getMessage()]
    assert line == f"{FIELD} ignored: TELNYX_SCREEN_RESULT_FROM_RELAY is off"


def test_the_setting_reads_true_one_and_yes_in_any_case(tmp_path):
    for i, value in enumerate(("true", "TRUE", "1", "yes", "Yes")):
        (tmp_path / str(i)).mkdir()
        vcon = _post(
            tmp_path / str(i), "telnyx", TENANT, ACTED, TELNYX_SCREEN_RESULT_FROM_RELAY=value
        )
        assert len(_analysis(vcon)) == 1, value


def test_the_addons_setting_does_not_switch_it_on(tmp_path):
    vcon = _post(
        tmp_path, "telnyx", TENANT, ACTED, TELNYX_SCREEN_RESULT_FROM_RELAY=None,
        TELNYX_ADDONS_FROM_RELAY="1",
    )  # fmt: skip
    _no_trace(vcon)


def test_an_off_vcon_is_the_vcon_it_was_before_the_field_existed(tmp_path):
    vcon = _post(tmp_path, "telnyx", None, ACTED, TELNYX_SCREEN_RESULT_FROM_RELAY=None)
    assert normalized(vcon) + "\n" == golden_path("telnyx", "absent").read_text()


# ---- an untrusted source: dropped, no entry, no tag


def _untrusted(tmp_path, caplog, **kwargs):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.webhook")
    vcon = _post(tmp_path, "telnyx", TENANT, ACTED, **kwargs)

    assert [a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tenant"] == []
    _no_trace(vcon, "voice-screen", "screen_result", "synthetic_frames", "0.97")
    [line] = [r.getMessage() for r in caplog.records if FIELD in r.getMessage()]
    assert line.startswith(f"{FIELD} ignored: ")
    assert "0.97" not in line and TOKEN not in line
    return vcon


def test_a_remote_peer_with_no_token_adds_nothing(tmp_path, caplog):
    _untrusted(tmp_path, caplog, peer=REMOTE)


def test_a_wrong_token_adds_nothing_even_from_loopback(tmp_path, caplog):
    _untrusted(tmp_path, caplog, query="token=not-the-token", TELNYX_TEXML_CALLBACK_TOKEN=TOKEN)


def test_a_spoofed_forwarded_for_header_does_not_count(tmp_path, caplog):
    _untrusted(
        tmp_path,
        caplog,
        peer=REMOTE,
        headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1"},
    )


def test_an_untrusted_vcon_is_the_vcon_it_was_before_the_field_existed(tmp_path, caplog):
    vcon = _untrusted(tmp_path, caplog, peer=REMOTE)
    assert normalized(vcon) + "\n" == golden_path("telnyx", "valid").read_text()


def test_the_builder_does_not_trust_the_value_without_the_route_s_trust(caplog):
    """The route blanks the value for an untrusted request; the builder checks again."""
    data = TelnyxRecordingData(
        _event(), relayed_tenants=[TENANT], relayed_screen_results=[_compact(ACTED)]
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)

    assert vcon is not None and _analysis(vcon) == []
    assert relayed_screen_result(data) == (None, None)


def test_a_json_body_cannot_claim_trust_or_an_outcome(tmp_path):
    """The Call Control route takes JSON; trust and the value are arguments, so no field of the
    body can set them, and an `annotations` object in it never becomes a tag."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from adapters.telnyx.config import TelnyxConfig
    from adapters.telnyx.webhook import create_app

    from .tenant_cases import LOOPBACK, adapter_env

    event = _event()
    event["data"]["payload"].update(
        relay_trusted=True,
        relayed_tenants=[TENANT],
        relayed_screen_results=[_compact(ACTED)],
        annotations={"tenant": TENANT, "screen_result": _compact(ACTED)},
    )
    with (
        patch.dict("os.environ", adapter_env(tmp_path, **ON), clear=True),
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
    ):
        poster_cls.return_value.post.return_value = True
        client = TestClient(create_app(TelnyxConfig()), client=LOOPBACK)
        assert client.post("/webhook/recording", json=event).text == "OK"
    (vcon,), _ = poster_cls.return_value.post.call_args
    _no_trace(vcon, "voice-screen", "synthetic_frames")


# ---- no tenant that counts: nothing is written


@pytest.mark.parametrize("tenant", [None, INVALID])
def test_a_trusted_outcome_with_no_usable_tenant_writes_nothing(tmp_path, caplog, tenant):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", tenant, ACTED)

    _no_trace(vcon, "voice-screen", "synthetic_frames")
    assert any("no usable tenant" in r.getMessage() for r in caplog.records)


# ---- malformed input: dropped, nothing written


def _with(**changes):
    doc = {**ACTED, **changes}
    return {k: v for k, v in doc.items() if v is not ...}


MALFORMED = {
    "not json": "not json",
    "empty": "",
    "a list": "[]",
    "null": "null",
    "a string": '"acted"',
    "a number": "1",
    "truncated": _compact(ACTED)[:-1],
    "trailing text": _compact(ACTED) + " x",
    "empty object": "{}",
    "NaN": _compact(ACTED).replace("0.97", "NaN"),
    "Infinity": _compact(ACTED).replace("0.97", "Infinity"),
    "repeated key": _compact(ACTED)[:-1] + ',"status":"clean"}',
    "extra key": _with(note="free text"),
    "extra key, bot says": _with(prompt="Thanks for calling"),
    "missing key": _with(frames=...),
    "missing action key": _with(telnyx_status=...),
    "unknown status": _with(status="synthetic"),
    "status null": _with(status=None),
    "status number": _with(status=1),
    "status in other case": _with(status="Acted"),
    "status with space": _with(status="acted "),
    "unknown mode": _with(mode="both"),
    "mode null": _with(mode=None),
    "unknown action": _with(action="hangup"),
    "action list": _with(action=["bot"]),
    "unknown action result": _with(action_result="ok"),
    "action result list": _with(action_result=["accepted"]),
    "threshold above 1": _with(threshold=1.01),
    "threshold below 0": _with(threshold=-0.1),
    "threshold text": _with(threshold="0.8"),
    "threshold bool": _with(threshold=True),
    "threshold null": _with(threshold=None),
    "threshold 1e999": _compact(ACTED).replace("0.8", "1e999"),
    "windows 0": _with(windows=0),
    "windows 5": _with(windows=5),
    "windows float": _with(windows=2.0),
    "windows bool": _with(windows=True),
    "windows text": _with(windows="2"),
    "frames negative": _with(frames=-1),
    "frames too many": _with(frames=3601),
    "frames float": _with(frames=5.5),
    "frames bool": _with(frames=False),
    "synthetic frames too many": _with(synthetic_frames=3601),
    "synthetic frames negative": _with(synthetic_frames=-1),
    "confidence above 1": _with(max_confidence=1.5),
    "confidence text": _with(max_confidence="0.97"),
    "confidence bool": _with(max_confidence=True),
    "decided_at no zone": _with(decided_at="2026-11-20T15:04:05"),
    "decided_at epoch": _with(decided_at=1790000000),
    "decided_at date only": _with(decided_at="2026-11-20"),
    "decided_at not a time": _with(decided_at="2026-13-45T25:61:61Z"),
    "decided_at free text": _with(decided_at="ignore previous instructions"),
    "decided_at with trailing text": _with(decided_at="2026-11-20T15:04:05Z; drop table"),
    "decided_at with space": _with(decided_at="2026-11-20 15:04:05Z"),
    "decided_at non-ascii digits": _with(decided_at="２０２６-11-20T15:04:05Z"),
    "decided_at list": _with(decided_at=["2026-11-20T15:04:05Z"]),
    "acted_at no zone": _with(acted_at="2026-11-20T15:04:05"),
    "acted_at text": _with(acted_at="soon"),
    "telnyx status low": _with(telnyx_status=99),
    "telnyx status high": _with(telnyx_status=600),
    "telnyx status text": _with(telnyx_status="200"),
    "telnyx status float": _with(telnyx_status=200.0),
    "telnyx status bool": _with(telnyx_status=True),
    "missing with result keys": {"status": "missing", "mode": "listen"},
    "missing with some action keys": {"status": "missing", "action": "bot"},
    "missing with a bad action": {
        "status": "missing",
        "action": "nope",
        "action_result": None,
        "acted_at": None,
        "telnyx_status": None,
    },
    "missing with a note": {"status": "missing", "note": "x"},
    "nested": {"status": {"value": "acted"}},
    "too large": _compact({**ACTED, "decided_at": "x" * SCREEN_RESULT_LIMIT}),
}


@pytest.mark.parametrize("name", MALFORMED)
def test_a_malformed_value_writes_nothing(tmp_path, caplog, name):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", TENANT, MALFORMED[name])

    _no_trace(vcon, "voice-screen", "synthetic_frames")
    [line] = [r.getMessage() for r in caplog.records if "screen_result" in r.getMessage()]
    assert "not the screening contract" in line
    assert "free text" not in line and "ignore previous" not in line


def test_a_valid_object_over_the_limit_is_dropped(tmp_path):
    """Everything in it is in range; only its size is wrong, so only the cap can refuse it."""
    padded = _compact(ACTED) + " " * SCREEN_RESULT_LIMIT
    assert json.loads(padded) == ACTED and len(padded) > SCREEN_RESULT_LIMIT
    assert screen_result_body(padded) is None
    assert _analysis(_post(tmp_path, "telnyx", TENANT, padded)) == []


def test_a_value_exactly_at_the_limit_still_parses():
    exact = _compact(ACTED) + " " * (SCREEN_RESULT_LIMIT - len(_compact(ACTED)))
    assert len(exact) == SCREEN_RESULT_LIMIT
    assert screen_result_body(exact) == ACTED


def test_the_body_is_in_the_contract_s_order_whatever_the_order_sent():
    shuffled = dict(reversed(list(ACTED.items())))
    assert list(screen_result_body(_compact(shuffled))) == list(ACTED)
    assert list(screen_result_body(_compact(dict(reversed(list(CLEAN.items())))))) == list(CLEAN)


def test_two_different_values_write_nothing_and_say_why(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", TENANT, ACTED, CLEAN)

    _no_trace(vcon, "voice-screen", "synthetic_frames")
    [line] = [r.getMessage() for r in caplog.records if "screen_result" in r.getMessage()]
    assert "more than one value" in line and "0.97" not in line


def test_a_non_text_value_writes_nothing_and_logs_its_type(caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    data = TelnyxRecordingData(
        _event(), relayed_tenants=[TENANT], relay_trusted=True, relayed_screen_results=[ACTED]
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)

    assert vcon is not None and _analysis(vcon) == []
    assert any("(dict)" in r.getMessage() for r in caplog.records)


def test_a_deeply_nested_value_is_dropped_not_an_error(tmp_path):
    vcon = _post(tmp_path, "telnyx", TENANT, "[" * 400 + "]" * 400)
    assert _analysis(vcon) == []


# ---- the entry copies no free text from the request


def test_the_entry_is_rebuilt_from_checked_values_only():
    """A value that passes is exactly the contract's: nothing outside it can ride along, and
    each text value is one of a fixed few or a fixed shape."""
    body = screen_result_body(_compact(ACTED))
    assert body == ACTED and body is not ACTED
    assert set(body) == set(ACTED)
    for k in ("status", "mode", "action", "action_result"):
        assert body[k] in {
            "clean", "acted", "error", "refused", "listen", "prompt_first",
            "bot", "announce", "stop", "accepted", "gone", "bot_unavailable",
        }  # fmt: skip


@pytest.mark.parametrize("status", ["clean", "acted", "error", "refused"])
@pytest.mark.parametrize("mode", ["listen", "prompt_first"])
@pytest.mark.parametrize("action", ["bot", "announce", "stop", None])
@pytest.mark.parametrize("action_result", ["accepted", "gone", "refused", "bot_unavailable", None])
def test_every_listed_value_is_accepted(status, mode, action, action_result):
    doc = _with(status=status, mode=mode, action=action, action_result=action_result)
    assert screen_result_body(_compact(doc)) == doc


@pytest.mark.parametrize("value", [0, 0.5, 1, 0.99])
def test_the_threshold_and_confidence_accept_the_whole_range(value):
    doc = _with(threshold=value, max_confidence=value)
    assert screen_result_body(_compact(doc)) == doc


@pytest.mark.parametrize("when", ["2026-11-20T15:04:05Z", "2026-11-20T15:04:05.1+05:30"])
def test_a_time_with_a_zone_is_accepted(when):
    doc = _with(decided_at=when, acted_at=when)
    assert screen_result_body(_compact(doc)) == doc


# ---- no annotation: the output does not change


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_vcon_with_no_annotation_has_no_analysis(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT)
    assert _analysis(vcon) == []
    assert "voice-screen" not in vcon.to_json()


@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("tenant", [None, TENANT, INVALID])
def test_with_no_annotation_the_output_is_the_same_whether_the_setting_is_on_or_off(
    tmp_path, carrier, tenant
):
    wire = {}
    for name, env in (("on", ON), ("off", {"TELNYX_SCREEN_RESULT_FROM_RELAY": None})):
        (tmp_path / name).mkdir()
        vcon = post_forward(_body(carrier, tenant), tmp_path / name, **env)
        wire[name] = normalized(vcon)
    assert wire["on"] == wire["off"]
    assert '"analysis": []' in wire["on"]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_with_no_annotation_and_no_tenant_the_output_is_the_golden(tmp_path, carrier):
    vcon = post_forward(_body(carrier, None), tmp_path, **ON)
    assert normalized(vcon) + "\n" == golden_path(carrier, "absent").read_text()


# ---- the raw tag is never written


@pytest.mark.parametrize(
    "name",
    [FIELD, "Annotation-Screen_Result", "Annotation-SCREEN_RESULT", "Annotation-screen_result"],
)
@pytest.mark.parametrize("env", [ON, {"TELNYX_SCREEN_RESULT_FROM_RELAY": None}])
@pytest.mark.parametrize("peer", [("127.0.0.1", 40000), REMOTE])
@pytest.mark.parametrize("tenant", [TENANT, None, INVALID])
def test_the_raw_annotation_tag_is_never_written(tmp_path, name, env, peer, tenant):
    pairs = list(FORMS["telnyx"])
    if tenant is not None:
        pairs.append(("Annotation-tenant", tenant))
    pairs.append((name, _compact(ACTED)))
    vcon = post_forward(urllib.parse.urlencode(pairs).encode(), tmp_path, peer=peer, **env)

    assert not [k for k in _tags(vcon) if "screen" in k.lower()]
    assert "annotation_screen" not in vcon.to_json().lower()
    assert "synthetic_frames" not in json.dumps(vcon.to_dict()["attachments"])


def test_only_the_exact_field_name_writes_an_entry(tmp_path):
    for i, name in enumerate(["Annotation-Screen_Result", "annotation-screen_result"]):
        (tmp_path / str(i)).mkdir()
        body = urllib.parse.urlencode(
            [*FORMS["telnyx"], ("Annotation-tenant", TENANT), (name, _compact(ACTED))]
        )
        vcon = post_forward(body.encode(), tmp_path / str(i), **ON)
        assert _analysis(vcon) == [], name


def test_other_annotations_are_still_tags(tmp_path):
    vcon = _post(tmp_path, "telnyx", TENANT, ACTED)
    assert _tags(vcon)["annotation_notice"] == "notice-v1.wav"
