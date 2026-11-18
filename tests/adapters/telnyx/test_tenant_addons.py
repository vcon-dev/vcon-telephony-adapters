"""The add-on choice in the tenant attachment, from the `Annotation-addons` field.

A relaying receiver adds `Annotation-addons` (comma separated ids) beside `Annotation-tenant`
and the basis fields. It reaches the vCon only inside the tenant attachment, as
`body.addons`, and only from a request the route trusted as relayed. Every case goes through
the real `/webhook/texml-recording` handler and the real `TelnyxVconBuilder`, from loopback
unless it says otherwise; only the conserver post is captured.
"""

import json
import logging
import urllib.parse
from pathlib import Path

import pytest

from adapters.telnyx.builder import (
    TelnyxRecordingData,
    TelnyxVconBuilder,
    relayed_addons,
    tenant_attachment,
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
ADDONS = "Annotation-addons"


def _body(carrier, tenant=TENANT, *addons, extra=()):
    """The forward as the receiver sends it: the tenant, then the add-on field(s)."""
    pairs = list(FORMS[carrier])
    if tenant is not None:
        pairs.append(("Annotation-tenant", tenant))
    pairs += [(ADDONS, value) for value in addons]
    pairs += list(extra)
    return urllib.parse.urlencode(pairs).encode()


def _post(tmp_path, carrier, tenant=TENANT, *addons, **kwargs):
    return post_forward(_body(carrier, tenant, *addons), tmp_path, **kwargs)


def _tenant_attachments(vcon):
    return [a for a in vcon.to_dict()["attachments"] if a.get("purpose") == "tenant"]


def _tags(vcon):
    tags = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    return dict(t.split(":", 1) for t in tags["body"])


def _bodies(vcon):
    return [a["body"] for a in _tenant_attachments(vcon)]


def _call_control_event():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )


# ---- from a trusted relay: the list lands in the tenant attachment


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_trusted_relay_s_addons_land_in_the_tenant_attachment(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT, "conv-triage")

    assert _bodies(vcon) == [{"id": TENANT, "addons": ["conv-triage"]}]
    [attachment] = _tenant_attachments(vcon)
    assert list(attachment["body"]) == ["id", "addons"]
    assert attachment["encoding"] == "json" and attachment["purpose"] == "tenant"


@pytest.mark.parametrize("peer", [("127.0.0.1", 40000), ("::1", 40000)])
def test_a_loopback_peer_with_no_token_counts(tmp_path, peer):
    vcon = _post(tmp_path, "telnyx", TENANT, "conv-triage,voice-screen", peer=peer)
    assert _bodies(vcon) == [{"id": TENANT, "addons": ["conv-triage", "voice-screen"]}]


def test_a_matched_token_counts_from_any_peer(tmp_path):
    vcon = _post(
        tmp_path,
        "telnyx",
        TENANT,
        "voice-screen",
        peer=REMOTE,
        query=f"token={TOKEN}",
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
    )
    assert _bodies(vcon) == [{"id": TENANT, "addons": ["voice-screen"]}]


def test_the_list_is_never_also_a_tag(tmp_path):
    vcon = _post(tmp_path, "telnyx", TENANT, "conv-triage")
    assert not [k for k in _tags(vcon) if "addons" in k.lower()]


@pytest.mark.parametrize("carrier", CARRIERS)
def test_the_addons_change_only_the_attachment_body(tmp_path, carrier):
    """The existing tenant fields are unchanged: with `addons` on the form, the whole vCon is
    the vCon without it, except `body.addons`."""
    (tmp_path / "plain").mkdir()
    plain = _post(tmp_path / "plain", carrier, TENANT)
    with_addons = _post(tmp_path, carrier, TENANT, "conv-triage")

    doc = json.loads(normalized(with_addons))
    [attachment] = [a for a in doc["attachments"] if a["purpose"] == "tenant"]
    assert attachment["body"].pop("addons") == ["conv-triage"]
    assert json.dumps(doc) == normalized(plain)
    assert _bodies(plain) == [{"id": TENANT}]


# ---- an untrusted source: dropped, no attachment, no tag


def _untrusted(tmp_path, caplog, tenant=TENANT, **kwargs):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.webhook")
    vcon = _post(tmp_path, "telnyx", tenant, "conv-triage,voice-screen", **kwargs)

    assert _tenant_attachments(vcon) == []
    assert not [k for k in _tags(vcon) if "addons" in k.lower()], "never kept as a tag"
    assert "conv-triage" not in json.dumps(vcon.to_dict())
    [line] = [r.getMessage() for r in caplog.records if ADDONS in r.getMessage()]
    assert line.startswith(f"{ADDONS} ignored: ")
    assert "conv-triage" not in line and TOKEN not in line
    return vcon


def test_a_remote_peer_with_no_token_adds_nothing(tmp_path, caplog):
    _untrusted(tmp_path, caplog, peer=REMOTE)


def test_a_wrong_token_adds_nothing_even_from_loopback(tmp_path, caplog):
    _untrusted(
        tmp_path,
        caplog,
        query="token=not-the-token",
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
    )


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


def test_the_builder_does_not_trust_the_list_without_the_route_s_trust():
    """The route blanks the list for an untrusted request; the builder checks again."""
    data = TelnyxRecordingData(
        _call_control_event(), relayed_tenants=[TENANT], relayed_addons=["conv-triage"]
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)
    assert vcon is not None and _tenant_attachments(vcon) == []
    assert relayed_addons(data) == (None, None)


def test_a_trusted_list_with_no_tenant_attaches_nothing_and_is_no_tag(tmp_path):
    vcon = _post(tmp_path, "telnyx", None, "conv-triage")
    assert _tenant_attachments(vcon) == []
    assert not [k for k in _tags(vcon) if "addons" in k.lower()]
    assert "conv-triage" not in json.dumps(vcon.to_dict())


def test_a_trusted_list_beside_an_invalid_tenant_attaches_nothing(tmp_path):
    vcon = _post(tmp_path, "telnyx", INVALID, "conv-triage")
    assert _tenant_attachments(vcon) == []
    assert "conv-triage" not in json.dumps(vcon.to_dict())


def test_a_json_body_cannot_claim_trust_or_a_list(tmp_path):
    """The Call Control route takes JSON; trust and the list are arguments, so no field of the
    body can set them."""
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from adapters.telnyx.config import TelnyxConfig
    from adapters.telnyx.webhook import create_app

    from .tenant_cases import LOOPBACK, adapter_env

    event = _call_control_event()
    event["data"]["payload"].update(
        relay_trusted=True,
        relayed_tenants=[TENANT],
        relayed_addons=["conv-triage"],
        annotations={"tenant": TENANT, "addons": "conv-triage"},
    )
    with (
        patch.dict("os.environ", adapter_env(tmp_path), clear=True),
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
    ):
        poster_cls.return_value.post.return_value = True
        client = TestClient(create_app(TelnyxConfig()), client=LOOPBACK)
        assert client.post("/webhook/recording", json=event).text == "OK"
    (vcon,), _ = poster_cls.return_value.post.call_args
    assert _tenant_attachments(vcon) == []
    assert not [k for k in _tags(vcon) if "addons" in k.lower()]


# ---- no annotation: no `addons` at all


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_vcon_with_no_annotation_gets_no_addons(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT)

    assert _bodies(vcon) == [{"id": TENANT}]
    assert "addons" not in json.dumps(vcon.to_dict())


def test_the_attachment_helper_adds_addons_only_when_given():
    assert tenant_attachment(TENANT, "t")["body"] == {"id": TENANT}
    assert tenant_attachment(TENANT, "t", [])["body"] == {"id": TENANT, "addons": []}


# ---- how the value is read


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("conv-triage", ["conv-triage"]),
        ("voice-screen,conv-triage", ["conv-triage", "voice-screen"]),
        ("conv-triage,conv-triage", ["conv-triage"]),
        ("conv-triage,something-new", ["conv-triage"]),
        ("something-new", []),
        ("", []),
    ],
)
def test_a_value_is_sorted_unique_and_filtered_to_known_ids(tmp_path, caplog, value, expected):
    caplog.set_level(logging.WARNING)
    vcon = _post(tmp_path, "telnyx", TENANT, value)

    assert _bodies(vcon) == [{"id": TENANT, "addons": expected}]
    assert not [r for r in caplog.records if "annotation_addons" in r.getMessage()]


@pytest.mark.parametrize(
    "value",
    [
        "conv-triage,",
        ",conv-triage",
        "conv-triage,,voice-screen",
        " conv-triage",
        "conv-triage ",
        "conv-triage, voice-screen",
        "Conv-Triage",
        "conv_triage",
        "conv-triage;voice-screen",
        "conv-triage\n",
        '["conv-triage"]',
        "conv-triage;drop",
        "-",
        "ｃonv-triage",
    ],
)
def test_a_value_that_does_not_parse_gives_an_empty_list_and_one_line(tmp_path, caplog, value):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", TENANT, value)

    assert _bodies(vcon) == [{"id": TENANT, "addons": []}]
    [line] = [r.getMessage() for r in caplog.records if "annotation_addons" in r.getMessage()]
    assert "does not parse" in line
    assert len(value) < 4 or value.strip() not in line, "the value itself is never logged"


def test_two_different_lists_give_an_empty_list_and_say_why(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", TENANT, "conv-triage", "voice-screen")

    assert _bodies(vcon) == [{"id": TENANT, "addons": []}]
    [line] = [r.getMessage() for r in caplog.records if "annotation_addons" in r.getMessage()]
    assert "more than one list" in line and "conv-triage" not in line


def test_the_same_list_twice_counts_once(tmp_path):
    vcon = _post(tmp_path, "telnyx", TENANT, "conv-triage", "conv-triage")
    assert _bodies(vcon) == [{"id": TENANT, "addons": ["conv-triage"]}]


def test_a_non_text_value_gives_an_empty_list_and_logs_its_type(caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    data = TelnyxRecordingData(
        _call_control_event(),
        relayed_tenants=[TENANT],
        relay_trusted=True,
        relayed_addons=[1234],
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)

    assert _bodies(vcon) == [{"id": TENANT, "addons": []}]
    assert any("(int)" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", ["Annotation-Addons", "annotation-addons", "Annotation-addons_x"])
def test_only_the_exact_field_name_turns_an_addon_on(tmp_path, name):
    body = urllib.parse.urlencode(
        [*FORMS["telnyx"], ("Annotation-tenant", TENANT), (name, "conv-triage")]
    )
    vcon = post_forward(body.encode(), tmp_path)

    assert _bodies(vcon) == [{"id": TENANT}]


def test_the_tenant_s_basis_is_untouched_by_the_list(tmp_path):
    """A tenant with no basis fields still has no lawful_basis attachment, list or not."""
    vcon = _post(tmp_path, "telnyx", TENANT, "conv-triage")
    assert not [a for a in vcon.to_dict()["attachments"] if a["purpose"] == "lawful_basis"]
