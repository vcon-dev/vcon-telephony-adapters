"""The tenant attachment, built from the `Annotation-tenant` field a relaying receiver adds.

The receiver adds the field, last, to the three recording callbacks it forwards here (Telnyx
TeXML, a bridge leg, and SignalWire reshaped into the TeXML form) and leaves it out for a call
it cannot place. It posts from loopback on the same host. The field counts only from a request
trusted as relayed: the route's token configured and matched, or a loopback peer. Every case
below goes through the real `/webhook/texml-recording` handler and the real
`TelnyxVconBuilder`, from loopback unless it says otherwise; only the conserver post is
captured. Payloads and goldens are in `tenant_cases.py`.
"""

import json
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from adapters.telnyx.builder import (
    TelnyxRecordingData,
    TelnyxVconBuilder,
    tenant_attachment,
)
from adapters.telnyx.config import TelnyxConfig
from adapters.telnyx.webhook import create_app

from .tenant_cases import (
    CARRIERS,
    FORMS,
    INVALID,
    LOOPBACK,
    REMOTE,
    TENANT,
    adapter_env,
    form_body,
    golden_path,
    normalized,
    post_forward,
)

TOKEN = "synthetic-callback-token"

WG_SCHEMA = json.loads((Path(__file__).parents[2] / "schema" / "vcon_json_schema.json").read_text())

# The shape the multi-tenant design gives, with `start` filled in by the adapter.
DESIGN_KEYS = ["purpose", "start", "party", "dialog", "mediatype", "encoding", "body"]


def _post(tmp_path, carrier, tenant=None, body=None):
    return post_forward(body if body is not None else form_body(carrier, tenant), tmp_path)


def _tenant_attachments(vcon):
    return [a for a in vcon.to_dict()["attachments"] if a.get("purpose") == "tenant"]


def _without_tenant_attachment(vcon):
    vcon.vcon_dict["attachments"] = [
        a for a in vcon.vcon_dict["attachments"] if a.get("purpose") != "tenant"
    ]
    return vcon


def _tags(vcon):
    tags = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    return dict(t.split(":", 1) for t in tags["body"])


# ---- a valid tenant: one attachment in the design's shape, and the tag as before


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_valid_tenant_adds_the_attachment_in_the_design_shape(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT)

    attachments = vcon.to_dict()["attachments"]
    assert attachments[-1] == {
        "purpose": "tenant",
        "start": vcon.created_at,
        "party": 0,
        "dialog": 0,
        "mediatype": "application/json",
        "encoding": "json",
        "body": {"id": TENANT},
    }
    assert _tenant_attachments(vcon) == [attachments[-1]], "exactly one tenant attachment"
    assert list(attachments[-1]) == DESIGN_KEYS
    assert _tags(vcon)["annotation_tenant"] == TENANT, "the tag stays as it was"


@pytest.mark.parametrize("carrier", CARRIERS)
def test_the_attachment_meets_the_attachment_object_rules(tmp_path, carrier):
    """Draft -04 and the spec kit: `purpose` and never `type`, `start`/`party`/`dialog`
    present, `start` an ISO 8601 time with a zone, and an `encoding: json` body that is the
    JSON value, not a string of it."""
    vcon = _post(tmp_path, carrier, TENANT)
    [attachment] = _tenant_attachments(vcon)

    assert "type" not in attachment
    assert isinstance(attachment["body"], dict)
    assert isinstance(attachment["party"], int) and isinstance(attachment["dialog"], int)
    start = datetime.fromisoformat(attachment["start"].replace("Z", "+00:00"))
    assert start.tzinfo is not None
    assert attachment["start"] == vcon.created_at, "the same start as every other attachment"
    # Against the WG schema's Attachment definition. Not the whole vCon: a dialog that only
    # references the recording URL has no content_hash, which the schema rejects on its own.
    jsonschema.validate(
        instance=attachment,
        schema={**WG_SCHEMA["definitions"]["Attachment"], "definitions": WG_SCHEMA["definitions"]},
    )


# ---- no tenant, or a bad one: the vCon exactly as before this attachment existed


@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("case", ["absent", "invalid"])
def test_without_a_valid_tenant_the_vcon_is_byte_for_byte_as_before(tmp_path, carrier, case):
    """The goldens are this builder's output for the same forms before the change."""
    vcon = _post(tmp_path, carrier, {"absent": None, "invalid": INVALID}[case])

    assert _tenant_attachments(vcon) == []
    assert normalized(vcon) + "\n" == golden_path(carrier, case).read_text()


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_valid_tenant_changes_nothing_but_the_added_attachment(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, TENANT)

    assert len(_tenant_attachments(vcon)) == 1
    assert (
        normalized(_without_tenant_attachment(vcon)) + "\n"
        == golden_path(carrier, "valid").read_text()
    )


@pytest.mark.parametrize(
    "value",
    [
        INVALID,
        "t-00000000000000A1",  # upper-case hex
        "t-00000000000000a",  # 15 hex
        "t-00000000000000a12",  # 17 hex
        "00000000000000a1",  # no prefix
        "T-00000000000000a1",
        "t_00000000000000a1",
        "t-00000000000000g1",  # not hex
        "t-０００００００００００００ａ１",  # full-width digits
        " t-00000000000000a1",
        "t-00000000000000a1\n",
        "t-00000000000000a1 t-00000000000000b2",
        "_quarantine",
        "null",
    ],
)
def test_an_invalid_value_adds_nothing_and_logs_only_its_type(tmp_path, caplog, value):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    vcon = _post(tmp_path, "telnyx", value)
    (tmp_path / "baseline").mkdir()
    baseline = _post(tmp_path / "baseline", "telnyx")

    assert _tenant_attachments(vcon) == []
    assert [a["purpose"] for a in vcon.to_dict()["attachments"]] == [
        a["purpose"] for a in baseline.to_dict()["attachments"]
    ]
    assert _tags(vcon)["annotation_tenant"] == value, "the tag stays as it was"
    [record] = [r for r in caplog.records if "annotation_tenant" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert "(str)" in record.getMessage()
    assert value.strip() not in record.getMessage(), "the value itself is never logged"


def test_an_empty_value_adds_nothing(tmp_path):
    vcon = _post(tmp_path, "telnyx", "")
    assert _tenant_attachments(vcon) == []


def _call_control_event():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )


def test_a_non_string_value_adds_nothing_and_logs_its_type(caplog):
    """Only a relayed form reaches here in practice, where every value is a string."""
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    data = TelnyxRecordingData(_call_control_event(), relayed_tenants=[1234], relay_trusted=True)
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)

    assert vcon is not None and _tenant_attachments(vcon) == []
    assert any("(int)" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", ["Annotation-Tenant", "annotation-tenant", "Annotation-tenant_id"])
def test_only_the_exact_field_name_counts(tmp_path, name):
    body = urllib.parse.urlencode([*FORMS["telnyx"], (name, TENANT)]).encode()
    assert _tenant_attachments(_post(tmp_path, "telnyx", body=body)) == []


def test_a_call_control_event_with_no_annotations_has_no_tenant_attachment():
    vcon = TelnyxVconBuilder(download_recordings=False).build(
        TelnyxRecordingData(_call_control_event())
    )
    assert vcon is not None and _tenant_attachments(vcon) == []


def test_the_builder_attaches_nothing_without_the_route_s_trust():
    data = TelnyxRecordingData(_call_control_event(), relayed_tenants=[TENANT])
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)
    assert vcon is not None and _tenant_attachments(vcon) == []


def test_a_json_body_cannot_claim_trust_or_a_tenant(tmp_path):
    """The Call Control route takes JSON; trust is an argument, so no field can forge it."""
    event = _call_control_event()
    event["data"]["payload"].update(
        relay_trusted=True,
        relayed_tenants=[TENANT],
        tenant_values=[TENANT],
        annotations={"tenant": TENANT},
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


# ---- which requests are trusted as relayed: the route's token, or a loopback peer


@pytest.mark.parametrize("peer", [LOOPBACK, ("::1", 40000)], ids=["127.0.0.1", "::1"])
def test_a_loopback_peer_with_no_token_attaches(tmp_path, peer):
    vcon = post_forward(form_body("telnyx", TENANT), tmp_path, peer=peer)
    assert [a["body"] for a in _tenant_attachments(vcon)] == [{"id": TENANT}]


@pytest.mark.parametrize("validate", ["true", "false"])
def test_a_matched_token_attaches_from_any_peer(tmp_path, validate):
    vcon = post_forward(
        form_body("telnyx", TENANT),
        tmp_path,
        peer=REMOTE,
        query=f"token={TOKEN}",
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
        VALIDATE_TELNYX_WEBHOOK=validate,
        ALLOW_UNSIGNED_WEBHOOKS="true",
    )
    assert [a["body"] for a in _tenant_attachments(vcon)] == [{"id": TENANT}]


def _untrusted(tmp_path, caplog, reason, **kwargs):
    """Posted with a valid tenant but untrusted: no attachment, tag and log as described."""
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.webhook")
    vcon = post_forward(form_body("telnyx", TENANT), tmp_path, **kwargs)

    assert _tenant_attachments(vcon) == []
    assert (
        normalized(vcon) + "\n" == golden_path("telnyx", "valid").read_text()
    ), "the vCon is what it was before the attachment existed, tag included"
    [line] = [r.getMessage() for r in caplog.records if "Annotation-tenant" in r.getMessage()]
    assert line == f"Annotation-tenant ignored: {reason}"
    assert TENANT not in line and TOKEN not in line


NOT_LOOPBACK = "no matching callback token and the peer is not loopback"
WRONG_TOKEN = "the callback token does not match"


def test_a_remote_peer_with_no_token_does_not_attach(tmp_path, caplog):
    _untrusted(tmp_path, caplog, NOT_LOOPBACK, peer=REMOTE)


def test_a_remote_peer_with_no_token_configured_does_not_attach(tmp_path, caplog):
    """A token on the request means nothing when the route has none configured."""
    _untrusted(tmp_path, caplog, NOT_LOOPBACK, peer=REMOTE, query=f"token={TOKEN}")


def test_a_spoofed_forwarded_for_header_does_not_count(tmp_path, caplog):
    _untrusted(
        tmp_path,
        caplog,
        NOT_LOOPBACK,
        peer=REMOTE,
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Forwarded": "for=127.0.0.1",
        },
    )


def test_through_uvicorn_proxy_headers_a_spoofed_header_from_a_remote_peer_does_not_count(
    tmp_path, caplog
):
    """uvicorn.run's default: X-Forwarded-For is believed only from 127.0.0.1."""
    _untrusted(
        tmp_path,
        caplog,
        NOT_LOOPBACK,
        peer=REMOTE,
        headers={"X-Forwarded-For": "127.0.0.1"},
        wrap=ProxyHeadersMiddleware,
    )


def test_through_uvicorn_proxy_headers_a_local_proxy_forwarding_a_remote_client_does_not_count(
    tmp_path, caplog
):
    _untrusted(
        tmp_path,
        caplog,
        NOT_LOOPBACK,
        peer=LOOPBACK,
        headers={"X-Forwarded-For": REMOTE[0]},
        wrap=ProxyHeadersMiddleware,
    )


@pytest.mark.parametrize("peer", [REMOTE, LOOPBACK], ids=["remote", "loopback"])
def test_a_wrong_token_does_not_attach_even_from_loopback(tmp_path, caplog, peer):
    """With validation off the route lets the callback in, but it is not trusted as relayed."""
    _untrusted(
        tmp_path,
        caplog,
        WRONG_TOKEN,
        peer=peer,
        query="token=not-the-token",
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
    )


def test_with_validation_on_a_wrong_token_is_refused_as_before(tmp_path):
    post_forward(
        form_body("telnyx", TENANT),
        tmp_path,
        query="token=not-the-token",
        expect_status=403,
        TELNYX_TEXML_CALLBACK_TOKEN=TOKEN,
        VALIDATE_TELNYX_WEBHOOK="true",
        ALLOW_UNSIGNED_WEBHOOKS="true",
    )


def test_an_untrusted_request_with_no_tenant_field_logs_nothing(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.webhook")
    vcon = post_forward(form_body("telnyx"), tmp_path, peer=REMOTE)
    assert normalized(vcon) + "\n" == golden_path("telnyx", "absent").read_text()
    assert not [r for r in caplog.records if "Annotation-tenant" in r.getMessage()]


# ---- repeated fields: two different tenants is no tenant


def test_two_different_tenants_add_nothing_and_say_why(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    other = "t-00000000000000b2"
    body = urllib.parse.urlencode(
        [*FORMS["telnyx"], ("Annotation-tenant", other), ("Annotation-tenant", TENANT)]
    ).encode()
    vcon = _post(tmp_path, "telnyx", body=body)

    assert _tenant_attachments(vcon) == []
    assert _tags(vcon)["annotation_tenant"] == TENANT, "the tag takes the last field, as before"
    [line] = [r.getMessage() for r in caplog.records if "tenant" in r.getMessage()]
    assert "more than one tenant" in line and TENANT not in line and other not in line


def test_the_same_tenant_twice_attaches_once(tmp_path):
    body = urllib.parse.urlencode(
        [*FORMS["telnyx"], ("Annotation-tenant", TENANT), ("Annotation-tenant", TENANT)]
    ).encode()
    vcon = _post(tmp_path, "telnyx", body=body)
    assert [a["body"] for a in _tenant_attachments(vcon)] == [{"id": TENANT}]


def test_a_valid_field_and_an_invalid_one_add_nothing(tmp_path):
    body = urllib.parse.urlencode(
        [*FORMS["telnyx"], ("Annotation-tenant", TENANT), ("Annotation-tenant", INVALID)]
    ).encode()
    assert _tenant_attachments(_post(tmp_path, "telnyx", body=body)) == []


# ---- lawful basis is untouched


def test_with_no_lawful_basis_configured_the_tenant_is_still_attached_and_no_basis_is(tmp_path):
    """The basis is the adapter's configured one, as before; the tenant does not add one."""
    vcon = post_forward(form_body("telnyx", TENANT), tmp_path, LAWFUL_BASIS=None)
    assert [a["purpose"] for a in vcon.to_dict()["attachments"]] == ["tags", "tenant"]


def test_tenant_attachment_shape():
    assert tenant_attachment(TENANT, "2026-11-06T18:00:00Z") == {
        "purpose": "tenant",
        "start": "2026-11-06T18:00:00Z",
        "party": 0,
        "dialog": 0,
        "mediatype": "application/json",
        "encoding": "json",
        "body": {"id": TENANT},
    }
