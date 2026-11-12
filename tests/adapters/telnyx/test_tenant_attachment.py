"""The tenant attachment, built from the `Annotation-tenant` field a relaying receiver adds.

The receiver adds the field, last, to the three recording callbacks it forwards here (Telnyx
TeXML, a bridge leg, and SignalWire reshaped into the TeXML form) and leaves it out for a call
it cannot place. Every case below goes through the real `/webhook/texml-recording` handler and
the real `TelnyxVconBuilder`; only the conserver post is captured. Payloads and goldens are in
`tenant_cases.py`.
"""

import json
import logging
import urllib.parse
from datetime import datetime
from pathlib import Path

import jsonschema
import pytest

from adapters.telnyx.builder import (
    TelnyxRecordingData,
    TelnyxVconBuilder,
    tenant_attachment,
)

from .tenant_cases import (
    CARRIERS,
    FORMS,
    INVALID,
    TENANT,
    form_body,
    golden_path,
    normalized,
    post_forward,
)

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


def test_a_non_string_value_adds_nothing_and_logs_its_type(caplog):
    """Only a relayed form reaches here in practice, where every value is a string."""
    caplog.set_level(logging.WARNING, logger="adapters.telnyx.builder")
    event = json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )
    event["data"]["payload"]["annotations"] = {"tenant": 1234}
    vcon = TelnyxVconBuilder(download_recordings=False).build(TelnyxRecordingData(event))

    assert vcon is not None and _tenant_attachments(vcon) == []
    assert any("(int)" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("name", ["Annotation-Tenant", "annotation-tenant", "Annotation-tenant_id"])
def test_only_the_exact_field_name_counts(tmp_path, name):
    body = urllib.parse.urlencode([*FORMS["telnyx"], (name, TENANT)]).encode()
    assert _tenant_attachments(_post(tmp_path, "telnyx", body=body)) == []


def test_a_call_control_event_with_no_annotations_has_no_tenant_attachment():
    event = json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(TelnyxRecordingData(event))
    assert vcon is not None and _tenant_attachments(vcon) == []


# ---- repeated fields: the last one, as the receiver adds its own last


def test_with_the_field_twice_the_last_one_is_the_tenant(tmp_path):
    other = "t-00000000000000b2"
    body = urllib.parse.urlencode(
        [*FORMS["telnyx"], ("Annotation-tenant", other), ("Annotation-tenant", TENANT)]
    ).encode()
    vcon = _post(tmp_path, "telnyx", body=body)

    assert [a["body"] for a in _tenant_attachments(vcon)] == [{"id": TENANT}]
    assert _tags(vcon)["annotation_tenant"] == TENANT, "the tag takes the same field"


def test_a_valid_field_followed_by_an_invalid_one_adds_nothing(tmp_path):
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
