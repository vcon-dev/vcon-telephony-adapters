"""Lawful basis: recorded when configured, never invented.

CON-814. `BaseVconBuilder` wrote no attachments at all, so no vCon any adapter
in this monorepo produced asserted a legal basis for its own existence.
"""

import pytest
from vcon import Vcon

from core.base_config import BaseConfig
from core.lawful_basis import VALID_LAWFUL_BASES, LawfulBasisConfig


def fresh_vcon():
    return Vcon.build_new()


def body_of(attachment: dict) -> dict:
    """An attachment's body.

    Under draft-ietf-vcon-vcon-core-04 §2.3.2 (CDDL `body: any`), `body` for
    `encoding: "json"` is the JSON value itself, not a `json.dumps` string.
    vcon-lib 0.9.6's `add_lawful_basis_attachment` already emits it that way.
    """
    assert isinstance(attachment["body"], dict), "body must be the JSON object, not a string"
    return attachment["body"]


# -- the refusal to invent -------------------------------------------------


def test_unconfigured_emits_nothing():
    """The central rule. A fabricated consent record is worse than none."""
    vcon = fresh_vcon()
    applied = LawfulBasisConfig().apply(vcon)

    assert applied is False
    assert vcon.find_lawful_basis_attachments() == []
    assert "lawful_basis" not in (vcon.to_dict().get("extensions") or [])


def test_there_is_no_default_basis():
    """Picking one would assert a legal position on a deployment's behalf."""
    assert LawfulBasisConfig().lawful_basis is None
    assert LawfulBasisConfig().enabled is False


def test_a_bogus_basis_is_refused_at_construction():
    with pytest.raises(ValueError, match="not one of"):
        LawfulBasisConfig(lawful_basis="because_we_felt_like_it")


def test_every_gdpr_article_6_basis_is_accepted():
    for basis in VALID_LAWFUL_BASES:
        assert LawfulBasisConfig(lawful_basis=basis).enabled


def test_a_basis_with_no_purposes_is_refused():
    """Granting nothing is not a lawful basis, it is a shape with no content."""
    with pytest.raises(ValueError, match="PURPOSES is empty"):
        LawfulBasisConfig(lawful_basis="consent", purposes=[])


# -- what it emits ---------------------------------------------------------


def test_emitted_attachment_is_found_by_the_library():
    vcon = fresh_vcon()
    LawfulBasisConfig(lawful_basis="consent", purposes=["recording"]).apply(vcon)

    found = vcon.find_lawful_basis_attachments()
    assert len(found) == 1
    assert body_of(found[0])["lawful_basis"] == "consent"
    assert found[0]["party"] == 0
    assert found[0]["dialog"] == 0
    assert found[0]["encoding"] == "json"
    assert found[0]["mediatype"] == "application/json"


def test_emitted_attachment_keeps_the_vcon_valid():
    """Regression on the shape.

    The legacy `type: "lawful_basis"` form (still emitted by
    vcon-siprec-adapter) fails `is_valid()`, because every attachment must
    carry `purpose`. Delegating to vcon-lib gets the shape its own validator
    accepts.
    """
    vcon = fresh_vcon()
    LawfulBasisConfig(lawful_basis="consent").apply(vcon)

    valid, errors = vcon.is_valid()
    assert valid, errors


def test_tagging_still_works_afterwards():
    """Regression, and a nasty one.

    `find_attachment_by_purpose` does `a["purpose"]`, not `a.get("purpose")`,
    so a single attachment lacking that key makes every later `add_tag` raise
    KeyError. The legacy shape triggers exactly that.
    """
    vcon = fresh_vcon()
    LawfulBasisConfig(lawful_basis="consent").apply(vcon)

    vcon.add_tag("source", "telnyx_adapter")
    assert vcon.get_tag("source") == "telnyx_adapter"


def test_extension_is_declared():
    vcon = fresh_vcon()
    LawfulBasisConfig(lawful_basis="contract").apply(vcon)
    assert "lawful_basis" in vcon.to_dict()["extensions"]


def test_all_purposes_are_granted():
    vcon = fresh_vcon()
    LawfulBasisConfig(
        lawful_basis="legitimate_interests",
        purposes=["recording", "transcription", "analysis"],
    ).apply(vcon)

    grants = body_of(vcon.find_lawful_basis_attachments()[0])["purpose_grants"]
    assert [g["purpose"] for g in grants] == ["recording", "transcription", "analysis"]
    assert all(g["granted"] and g["granted_at"] for g in grants)


def test_justification_survives_in_metadata():
    """Not a first-class field on the library's model; `metadata` is its hook."""
    vcon = fresh_vcon()
    LawfulBasisConfig(
        lawful_basis="legitimate_interests",
        justification="Carrier-side recording; controller manages consent.",
    ).apply(vcon)

    body = body_of(vcon.find_lawful_basis_attachments()[0])
    assert "controller manages consent" in body["metadata"]["justification"]


def test_expiration_is_carried_when_set():
    vcon = fresh_vcon()
    LawfulBasisConfig(lawful_basis="consent", expiration="2027-01-01T00:00:00+00:00").apply(vcon)

    assert body_of(vcon.find_lawful_basis_attachments()[0])["expiration"] is not None


# -- configuration ---------------------------------------------------------


def test_config_is_disabled_when_the_env_var_is_unset(minimal_env):
    assert BaseConfig().build_lawful_basis().enabled is False


def test_config_reads_the_environment(minimal_env, monkeypatch):
    monkeypatch.setenv("LAWFUL_BASIS", "legitimate_interests")
    monkeypatch.setenv("LAWFUL_BASIS_PURPOSES", "recording, transcription")
    monkeypatch.setenv("LAWFUL_BASIS_JUSTIFICATION", "Because the contract says so")

    config = BaseConfig().build_lawful_basis()

    assert config.enabled
    assert config.lawful_basis == "legitimate_interests"
    assert config.purposes == ["recording", "transcription"]
    assert config.justification == "Because the contract says so"


def test_a_misconfigured_basis_fails_loudly_rather_than_silently(minimal_env, monkeypatch):
    """Better to refuse to start than to emit vCons with no basis for a month."""
    monkeypatch.setenv("LAWFUL_BASIS", "vibes")

    with pytest.raises(ValueError):
        BaseConfig().build_lawful_basis()
