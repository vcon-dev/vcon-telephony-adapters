"""The call's own tenant's lawful basis, relayed by the receiver, in place of the global one.

Beside `Annotation-tenant` the receiver sends the tenant's basis record as
`Annotation-basis_<key>` fields. When the request is trusted as relayed and names one tenant
id, the adapter builds the lawful_basis attachment from those fields alone and never from its
own `LAWFUL_BASIS`: the tenant's basis and justification, the other seven fields in `metadata`,
the adapter's `LAWFUL_BASIS_PURPOSES`, no expiration, and a `signed_document` proof only for a
partner or staff entry with its document and check. Anything invalid or partial, or no basis
fields at all, means no lawful_basis attachment. Without a tenant that counts, the vCon is
exactly what it was before, global basis included.

Every case goes through the real `/webhook/texml-recording` route and the real builder, from
loopback unless it says otherwise, with the global basis in `GLOBAL_ENV` configured so a leak
of any of its values would show. Forms and goldens are in `basis_cases.py`.
"""

import json
import logging
import urllib.parse
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest
from fastapi.testclient import TestClient
from vcon.extensions.lawful_basis import LawfulBasisValidator

from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder
from adapters.telnyx.config import TelnyxConfig
from adapters.telnyx.webhook import create_app
from core.lawful_basis import LawfulBasisConfig, RelayedBasisError, RelayedLawfulBasis

from .basis_cases import (
    BASIS_PREFIX,
    CHECKED,
    CUSTOMER,
    GLOBAL_ENV,
    NO_TENANT_CASES,
    basis_body,
    golden_path,
    post_no_tenant_case,
)
from .tenant_cases import (
    CARRIERS,
    FORMS,
    LOOPBACK,
    TENANT,
    adapter_env,
    normalized,
    post_forward,
)

TOKEN = "synthetic-callback-token"
BASIS_TAG = "annotation_basis_"
PROOF_KEYS = ("evidence", "checked_by")  # also in the proof's proof_data
WG_SCHEMA = json.loads((Path(__file__).parents[2] / "schema" / "vcon_json_schema.json").read_text())
GLOBAL_VALUES = ("legitimate_interests", "Synthetic global justification.", "2099")
REFUSED = "the tenant's relayed lawful basis is refused"
NO_BASIS = "the relay sent no lawful basis for the call's tenant"


def _post(tmp_path, carrier, basis, tenant=TENANT, **kwargs):
    env = {**GLOBAL_ENV, **{k: kwargs.pop(k) for k in list(kwargs) if k.isupper()}}
    return post_forward(basis_body(carrier, basis, tenant), tmp_path, **kwargs, **env)


def _lawful(vcon):
    return [a for a in vcon.to_dict()["attachments"] if a.get("purpose") == "lawful_basis"]


def _purposes(vcon):
    return [a["purpose"] for a in vcon.to_dict()["attachments"]]


def _builder_warnings(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name in ("adapters.telnyx.builder", "core.base_builder")
        and r.levelno >= logging.WARNING
    ]


def _grants(vcon_or_attachment):
    """The purpose grants with their build-time `granted_at` checked and dropped."""
    attachment = vcon_or_attachment
    grants = []
    for grant in attachment["body"]["purpose_grants"]:
        grant = dict(grant)
        assert grant.pop("granted_at").endswith("+00:00")
        grants.append(grant)
    return grants


def _expected(basis, start):
    """The attachment a valid relayed basis must give, `granted_at` aside."""
    body = {"lawful_basis": basis["lawful_basis"], "expiration": None}
    body["purpose_grants"] = [
        {"purpose": "recording", "granted": True},
        {"purpose": "transcription", "granted": True},
    ]
    if all(k in basis for k in ("evidence", "checked_by", "checked_at")):
        body["proof_mechanisms"] = [
            {
                "proof_type": "signed_document",
                "timestamp": basis["checked_at"].replace("Z", "+00:00"),
                "proof_data": {"document": basis["evidence"], "checked_by": basis["checked_by"]},
            }
        ]
    body["metadata"] = {k: v for k, v in basis.items() if k != "lawful_basis"}
    return {
        "purpose": "lawful_basis",
        "encoding": "json",
        "body": body,
        "party": 0,
        "dialog": 0,
        "mediatype": "application/json",
        "start": start,
    }


def _strip_grant_times(attachment):
    attachment = json.loads(json.dumps(attachment))
    _grants(attachment)
    for grant in attachment["body"]["purpose_grants"]:
        del grant["granted_at"]
    return attachment


# ---- a trusted tenant with a basis in order: that basis, never the global one


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_customer_entered_basis_is_the_tenant_s_own_with_metadata_and_no_proof(tmp_path, carrier):
    vcon = _post(tmp_path, carrier, CUSTOMER)

    [attachment] = _lawful(vcon)
    assert _strip_grant_times(attachment) == _expected(CUSTOMER, vcon.created_at)
    assert "proof_mechanisms" not in attachment["body"]
    assert list(attachment["body"]["metadata"]) == [
        "justification",
        "consent_record",
        "chosen_by",
        "chosen_at",
        "entered_by",
    ]
    assert not any(value in json.dumps(attachment) for value in GLOBAL_VALUES)
    assert vcon.to_dict()["extensions"] == ["lawful_basis"]
    assert _purposes(vcon)[-1] == "tenant"


@pytest.mark.parametrize("entered_by", ["partner", "staff"])
@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_partner_or_staff_entry_with_its_document_and_check_gets_the_signed_document_proof(
    tmp_path, carrier, entered_by
):
    basis = dict(CHECKED, entered_by=entered_by)
    vcon = _post(tmp_path, carrier, basis)

    [attachment] = _lawful(vcon)
    assert _strip_grant_times(attachment) == _expected(basis, vcon.created_at)
    assert attachment["body"]["proof_mechanisms"] == [
        {
            "proof_type": "signed_document",
            "timestamp": "2026-11-02T10:00:00+00:00",
            "proof_data": {
                "document": "doc-00000000000000d1",
                "checked_by": "synthetic staff admin",
            },
        }
    ]
    assert not any(value in json.dumps(attachment) for value in GLOBAL_VALUES)


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_tenant_s_vcon_differs_from_the_no_tenant_golden_only_in_basis_and_tenant(
    tmp_path, carrier
):
    """The same form posted untrusted (the golden, M16's output) and trusted: the trusted one
    drops the annotation_basis_* tags, swaps the global basis for the tenant's and adds the
    tenant attachment, nothing else."""
    vcon = _post(tmp_path, carrier, CHECKED)
    doc = json.loads(normalized(vcon))
    golden = json.loads(golden_path(carrier, "untrusted").read_text())

    tags = golden["attachments"][0]
    assert tags["purpose"] == "tags" and len([t for t in tags["body"] if BASIS_TAG in t]) == 9
    tags["body"] = [t for t in tags["body"] if not t.startswith(BASIS_TAG)]
    i = [a["purpose"] for a in golden["attachments"]].index("lawful_basis")
    assert doc["attachments"][i]["body"]["lawful_basis"] == "consent"
    golden["attachments"][i] = doc["attachments"][i]
    golden["attachments"].append(doc["attachments"][-1])
    assert doc["attachments"][-1]["body"] == {"id": TENANT}
    assert doc == golden


def test_the_attachment_is_valid_for_the_library_and_the_wg_schema(tmp_path):
    for name, basis in (("customer", CUSTOMER), ("checked", CHECKED)):
        (tmp_path / name).mkdir()
        [attachment] = _lawful(_post(tmp_path / name, "telnyx", basis))
        result = LawfulBasisValidator().validate_attachment(attachment)
        assert result.is_valid, (name, result.errors)
        assert isinstance(attachment["body"], dict) and "type" not in attachment
        jsonschema.validate(
            instance=attachment,
            schema={
                **WG_SCHEMA["definitions"]["Attachment"],
                "definitions": WG_SCHEMA["definitions"],
            },
        )


def test_the_same_value_twice_counts_once(tmp_path):
    pairs = [(k, v) for k, v in CUSTOMER.items()] + [("lawful_basis", "contract")]
    [attachment] = _lawful(_post(tmp_path, "telnyx", pairs))
    assert attachment["body"]["lawful_basis"] == "contract"


# ---- the proof: only an uploaded document that staff checked, whoever entered the basis


def test_a_customer_s_own_entry_with_a_document_and_no_check_gets_no_proof(tmp_path):
    basis = dict(CUSTOMER, evidence="doc-00000000000000d1")
    [attachment] = _lawful(_post(tmp_path, "telnyx", basis))
    assert "proof_mechanisms" not in attachment["body"]
    assert attachment["body"]["metadata"]["evidence"] == "doc-00000000000000d1"


def test_a_customer_s_own_entry_with_a_check_and_no_document_gets_no_proof(tmp_path):
    basis = dict(CUSTOMER, checked_by="synthetic staff admin", checked_at="2026-11-02T10:00:00Z")
    [attachment] = _lawful(_post(tmp_path, "telnyx", basis))
    assert "proof_mechanisms" not in attachment["body"]
    assert attachment["body"]["metadata"]["checked_by"] == "synthetic staff admin"


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_customer_s_own_entry_with_a_checked_document_gets_the_signed_document_proof(
    tmp_path, carrier
):
    basis = dict(
        CUSTOMER,
        evidence="doc-00000000000000d1",
        checked_by="synthetic staff admin",
        checked_at="2026-11-02T10:00:00Z",
    )
    vcon = _post(tmp_path, carrier, basis)
    [attachment] = _lawful(vcon)
    assert _strip_grant_times(attachment) == _expected(basis, vcon.created_at)
    assert attachment["body"]["proof_mechanisms"] == [
        {
            "proof_type": "signed_document",
            "timestamp": "2026-11-02T10:00:00+00:00",
            "proof_data": {
                "document": "doc-00000000000000d1",
                "checked_by": "synthetic staff admin",
            },
        }
    ]
    assert attachment["body"]["metadata"]["entered_by"] == "customer"


@pytest.mark.parametrize("missing", ["evidence", "checked_by", "checked_at"])
def test_a_partner_entry_with_a_document_and_no_check_gets_no_attachment(tmp_path, caplog, missing):
    """A partner or staff entry is in order only with the document and its check (the
    receiver withholds it otherwise), so a partial one is refused whole, never half-proved."""
    caplog.set_level(logging.WARNING)
    basis = {k: v for k, v in CHECKED.items() if k != missing}
    vcon = _post(tmp_path, "telnyx", basis)
    assert _lawful(vcon) == [] and _purposes(vcon) == ["tags", "tenant"]
    assert _builder_warnings(caplog) == [
        f"vCon {vcon.uuid}: {REFUSED} (the basis is partial), so no lawful_basis attachment"
    ]


# ---- the annotation_basis_* tags: dropped with a counted tenant, kept without one


def _tags(vcon):
    tags = next(a for a in vcon.to_dict()["attachments"] if a["purpose"] == "tags")
    return tags["body"]


@pytest.mark.parametrize(
    "basis",
    [
        CUSTOMER,
        CHECKED,
        dict(CUSTOMER, consent_record="paper"),
        dict(CUSTOMER, purposes="recording,analysis"),
        {k: v for k, v in CHECKED.items() if k != "checked_at"},
    ],
    ids=[
        "customer entered",
        "checked",
        "refused: consent record",
        "refused: unknown key",
        "partial",
    ],
)
@pytest.mark.parametrize("carrier", CARRIERS)
def test_with_a_counted_tenant_the_basis_tags_are_dropped(tmp_path, carrier, basis):
    """Built or refused, the basis is not also kept as tags; every other tag is."""
    vcon = _post(tmp_path, carrier, basis)
    tags = _tags(vcon)

    assert not [t for t in tags if t.startswith(BASIS_TAG)]
    assert f"annotation_tenant:{TENANT}" in tags
    for name, value in FORMS[carrier]:
        if name.startswith("Annotation-"):
            assert f"annotation_{name[len('Annotation-'):]}:{value}" in tags
    assert basis["chosen_by"] not in json.dumps(tags)


def test_the_justification_and_names_are_in_the_vcon_once(tmp_path):
    vcon = _post(tmp_path, "telnyx", CHECKED)
    wire = vcon.to_json()
    for key in ("justification", "chosen_by", "evidence", "checked_by", "chosen_at"):
        assert wire.count(json.dumps(CHECKED[key])[1:-1]) == (2 if key in PROOF_KEYS else 1), key


@pytest.mark.parametrize("case", sorted(NO_TENANT_CASES))
def test_without_a_counted_tenant_the_basis_tags_stay(tmp_path, case):
    tags = _tags(post_no_tenant_case("telnyx", case, tmp_path))
    assert [t.split(":", 1)[0] for t in tags if t.startswith(BASIS_TAG)] == [
        BASIS_TAG + k for k in CHECKED
    ]


# ---- refused: invalid or partial, so no attachment and one line naming only the reason

INVALID = {
    "a basis outside the six": (dict(CUSTOMER, lawful_basis="implied"), "six bases"),
    "a basis in another case": (dict(CUSTOMER, lawful_basis="Consent"), "six bases"),
    "a basis with a space": (dict(CUSTOMER, lawful_basis="consent "), "six bases"),
    "a consent record outside the modes": (dict(CUSTOMER, consent_record="paper"), "off or jlinc"),
    "an entry by someone else": (
        dict(CUSTOMER, entered_by="reseller"),
        "not customer, partner or staff",
    ),
    "no lawful_basis": ({k: v for k, v in CUSTOMER.items() if k != "lawful_basis"}, "partial"),
    "no justification": ({k: v for k, v in CUSTOMER.items() if k != "justification"}, "partial"),
    "no consent_record": (
        {k: v for k, v in CUSTOMER.items() if k != "consent_record"},
        "partial",
    ),
    "no chosen_by": ({k: v for k, v in CUSTOMER.items() if k != "chosen_by"}, "partial"),
    "no entered_by": ({k: v for k, v in CUSTOMER.items() if k != "entered_by"}, "partial"),
    "a checker with no time": (dict(CUSTOMER, checked_by="synthetic staff admin"), "both"),
    "a time with no checker": (dict(CUSTOMER, checked_at="2026-11-02T10:00:00Z"), "both"),
    "a check time with no zone": (dict(CHECKED, checked_at="2026-11-02T10:00:00"), "zone"),
    "a check time that is no time": (dict(CHECKED, checked_at="the day before"), "zone"),
    "a blank optional value": (dict(CUSTOMER, chosen_at=""), "blank"),
    "a justification of spaces": (dict(CUSTOMER, justification=" \n "), "blank"),
    "purposes from the relay": (dict(CUSTOMER, purposes="recording,analysis"), "never sends"),
    "an expiration from the relay": (dict(CUSTOMER, expiration="2099-01-01T00:00:00Z"), "never"),
    "the source field": (dict(CUSTOMER, source="portal"), "never sends"),
    "the bare prefix": (dict(CUSTOMER, **{"": "x"}), "never sends"),
    "two different bases": (
        [(k, v) for k, v in CUSTOMER.items()] + [("lawful_basis", "consent")],
        "more than one value",
    ),
    "two different justifications": (
        [("justification", "first")] + [(k, v) for k, v in CUSTOMER.items()],
        "more than one value",
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_an_invalid_or_partial_basis_gets_no_attachment_and_one_line(tmp_path, caplog, case):
    caplog.set_level(logging.WARNING)
    basis, reason = INVALID[case]
    vcon = _post(tmp_path, "telnyx", basis)

    assert _lawful(vcon) == [], "no attachment, and never the global one"
    assert "extensions" not in vcon.to_dict()
    assert _purposes(vcon) == ["tags", "tenant"], "the tenant is still attached"
    [line] = _builder_warnings(caplog)
    assert line.startswith(f"vCon {vcon.uuid}: {REFUSED} (") and reason in line
    values = basis.values() if isinstance(basis, dict) else [v for _, v in basis]
    for value in values:
        if len(value.strip()) > 3:
            assert value not in line, "a basis value is never logged"
    assert TENANT not in line


def test_with_no_purposes_configured_a_tenant_s_basis_is_refused(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    vcon = _post(tmp_path, "telnyx", CUSTOMER, LAWFUL_BASIS=None, LAWFUL_BASIS_PURPOSES="")
    assert _lawful(vcon) == []
    assert "LAWFUL_BASIS_PURPOSES is empty" in _builder_warnings(caplog)[0]


@pytest.mark.parametrize(
    "value", [1, None, b"consent", ["consent"]], ids=["int", "None", "bytes", "list"]
)
def test_a_value_that_is_not_text_is_refused(value):
    relayed = {k: [v] for k, v in CUSTOMER.items()}
    relayed["lawful_basis"] = [value]
    with pytest.raises(RelayedBasisError, match="not text"):
        RelayedLawfulBasis.from_fields(relayed, ["recording"])


def test_a_key_with_no_value_is_refused():
    relayed = {k: [v] for k, v in CUSTOMER.items()}
    relayed["chosen_at"] = []
    with pytest.raises(RelayedBasisError, match="blank"):
        RelayedLawfulBasis.from_fields(relayed, ["recording"])


def test_a_multipart_file_for_a_basis_field_is_refused(tmp_path, caplog):
    """The receiver sends a urlencoded form; a file part is never a basis value."""
    caplog.set_level(logging.WARNING)
    data = dict(FORMS["telnyx"])
    data.update({BASIS_PREFIX + k: v for k, v in CUSTOMER.items() if k != "justification"})
    data["Annotation-tenant"] = TENANT
    with (
        patch.dict("os.environ", adapter_env(tmp_path, **GLOBAL_ENV), clear=True),
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
    ):
        poster_cls.return_value.post.return_value = True
        client = TestClient(create_app(TelnyxConfig()), client=LOOPBACK)
        response = client.post(
            "/webhook/texml-recording",
            data=data,
            files={BASIS_PREFIX + "justification": ("j.txt", b"from a file", "text/plain")},
        )
        assert response.text == "OK"
    (vcon,), _ = poster_cls.return_value.post.call_args
    assert _lawful(vcon) == []
    assert "not text" in _builder_warnings(caplog)[0]


# ---- a trusted tenant with no basis fields: no attachment, even with LAWFUL_BASIS set


@pytest.mark.parametrize("carrier", CARRIERS)
def test_a_tenant_with_no_basis_fields_gets_no_attachment_even_with_lawful_basis_set(
    tmp_path, caplog, carrier
):
    caplog.set_level(logging.WARNING)
    vcon = _post(tmp_path, carrier, None)

    assert _lawful(vcon) == [] and "extensions" not in vcon.to_dict()
    assert _purposes(vcon)[-1] == "tenant"
    assert _builder_warnings(caplog) == [
        f"vCon {vcon.uuid}: {NO_BASIS}, so no lawful_basis attachment; the global basis is "
        "never used for a tenant"
    ]


@pytest.mark.parametrize("name", ["Annotation-Basis_lawful_basis", "annotation-basis_lawful_basis"])
def test_only_the_exact_prefix_counts(tmp_path, name):
    pairs = [*FORMS["telnyx"], *[(name.replace("lawful_basis", k), v) for k, v in CUSTOMER.items()]]
    body = urllib.parse.urlencode([*pairs, ("Annotation-tenant", TENANT)]).encode()
    vcon = post_forward(body, tmp_path, **GLOBAL_ENV)
    assert _lawful(vcon) == [], "a tenant whose basis fields are misspelt has none"


# ---- no tenant that counts: the basis fields are ignored and the vCon is as before


@pytest.mark.parametrize("case", sorted(NO_TENANT_CASES))
@pytest.mark.parametrize("carrier", CARRIERS)
def test_without_a_counted_tenant_the_vcon_is_byte_for_byte_as_before(tmp_path, carrier, case):
    """The goldens are M16's output for the same forms, global basis and all."""
    vcon = post_no_tenant_case(carrier, case, tmp_path)
    assert normalized(vcon) + "\n" == golden_path(carrier, case).read_text()
    [attachment] = _lawful(vcon)
    assert attachment["body"]["lawful_basis"] == "legitimate_interests"


@pytest.mark.parametrize("peer", ["remote", "loopback with a wrong token"])
def test_basis_fields_from_an_untrusted_request_are_ignored_and_said_so(tmp_path, caplog, peer):
    caplog.set_level(logging.WARNING)
    kwargs = (
        {"peer": ("203.0.113.9", 40000)}
        if peer == "remote"
        else {"query": "token=not-the-token", "TELNYX_TEXML_CALLBACK_TOKEN": TOKEN}
    )
    vcon = _post(tmp_path, "telnyx", CHECKED, **kwargs)

    assert normalized(vcon) + "\n" == golden_path("telnyx", "untrusted").read_text()
    lines = [r.getMessage() for r in caplog.records if r.name == "adapters.telnyx.webhook"]
    reason = (
        "no matching callback token and the peer is not loopback"
        if peer == "remote"
        else "the callback token does not match"
    )
    assert f"{BASIS_PREFIX}* ignored: {reason}" in lines
    assert not [line for line in _builder_warnings(caplog) if "basis" in line]


def test_basis_fields_with_no_tenant_field_from_the_relay_are_ignored_and_said_so(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    vcon = post_no_tenant_case("telnyx", "absent", tmp_path)
    assert [a["body"]["lawful_basis"] for a in _lawful(vcon)] == ["legitimate_interests"]
    assert _builder_warnings(caplog) == [
        f"vCon {vcon.uuid}: basis fields came with no usable tenant, so they are ignored"
    ]


def _call_control_event():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "call_recording_saved.json").read_text()
    )


def test_the_builder_uses_no_relayed_basis_without_the_route_s_trust():
    relayed = {k: [v] for k, v in CUSTOMER.items()}
    data = TelnyxRecordingData(
        _call_control_event(), relayed_tenants=[TENANT], relayed_basis=relayed
    )
    builder = TelnyxVconBuilder(
        download_recordings=False, lawful_basis=LawfulBasisConfig("legitimate_interests")
    )
    vcon = builder.build(data)
    assert [a["body"]["lawful_basis"] for a in _lawful(vcon)] == ["legitimate_interests"]


def test_the_builder_with_no_lawful_basis_config_refuses_a_tenant_s_basis(caplog):
    """Purposes come only from the deployment's configuration; with none there are none."""
    caplog.set_level(logging.WARNING)
    relayed = {k: [v] for k, v in CUSTOMER.items()}
    data = TelnyxRecordingData(
        _call_control_event(), relayed_tenants=[TENANT], relay_trusted=True, relayed_basis=relayed
    )
    vcon = TelnyxVconBuilder(download_recordings=False).build(data)
    assert _lawful(vcon) == []
    assert "LAWFUL_BASIS_PURPOSES is empty" in _builder_warnings(caplog)[0]


def test_a_json_body_cannot_carry_a_basis(tmp_path):
    """The Call Control route takes JSON; the basis is an argument only the form route sets."""
    event = _call_control_event()
    event["data"]["payload"].update(
        relay_trusted=True,
        relayed_tenants=[TENANT],
        relayed_basis={k: [v] for k, v in CUSTOMER.items()},
        annotations={"tenant": TENANT, **{f"basis_{k}": v for k, v in CUSTOMER.items()}},
    )
    with (
        patch.dict("os.environ", adapter_env(tmp_path, **GLOBAL_ENV), clear=True),
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
    ):
        poster_cls.return_value.post.return_value = True
        client = TestClient(create_app(TelnyxConfig()), client=LOOPBACK)
        assert client.post("/webhook/recording", json=event).text == "OK"
    (vcon,), _ = poster_cls.return_value.post.call_args
    assert [a["body"]["lawful_basis"] for a in _lawful(vcon)] == ["legitimate_interests"]


# ---- free text round-trips from a real urlencoded form through the real route

JUSTIFICATIONS = {
    "newlines": "First line.\nSecond line.\r\nThird, after a CRLF.\tAnd a tab.\n",
    "form syntax": "a&b=c;d e+f #g ?h /i \\j 'k' \"l\" <m>",
    "percent signs": "100% sure; %0A is not a newline, nor %26, %%, %zz or %E9",
    "plus and space": "a + b = c  and  +%2B+",
    "non-ASCII": "naïve café, Zürich, 東京, Ω, the emoji 🎙️, and é combined",
    "leading and trailing space": "  kept as held  ",
}


@pytest.mark.parametrize("case", sorted(JUSTIFICATIONS))
@pytest.mark.parametrize("carrier", CARRIERS)
def test_free_text_round_trips_from_a_real_form(tmp_path, carrier, case):
    basis = dict(
        CHECKED,
        justification=JUSTIFICATIONS[case],
        chosen_by="Synthetic & Söhne = 100%; admin+1",
        checked_by="synthetic staff\nadmin & co",
    )
    body = basis_body(carrier, basis)
    body.decode("ascii")  # urlencoded, as the receiver sends it
    vcon = _post(tmp_path, carrier, basis)

    [attachment] = _lawful(vcon)
    metadata = attachment["body"]["metadata"]
    assert metadata["justification"] == JUSTIFICATIONS[case]
    assert metadata["chosen_by"] == basis["chosen_by"]
    assert metadata["checked_by"] == basis["checked_by"]
    assert attachment["body"]["proof_mechanisms"][0]["proof_data"]["checked_by"] == (
        basis["checked_by"]
    )
    # And the wire JSON carries it back unchanged.
    wire = json.loads(vcon.to_json())
    [posted] = [a for a in wire["attachments"] if a["purpose"] == "lawful_basis"]
    assert posted["body"]["metadata"]["justification"] == JUSTIFICATIONS[case]
