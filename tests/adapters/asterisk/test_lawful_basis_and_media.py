"""CON-1083: lawful basis and external media, wired the same way as Telnyx.

`core/lawful_basis.py` and `core/media_publisher.py` were already exercised
through the Telnyx adapter (CON-814/CON-844); this closes the gap for
Asterisk, whose webhook factory previously built `AsteriskVconBuilder`
without either `lawful_basis` or `publisher`, so every vCon it produced
carried no basis and always embedded audio inline.
"""

import base64
import hashlib
import logging

import pytest

from adapters.asterisk.builder import AsteriskRecordingData, AsteriskVconBuilder
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import FilesystemPublisher

AUDIO = b"RIFF" + b"\x00" * 4 + b"WAVEfmt not real audio, just deterministic bytes"


def _event(**overrides):
    base = {
        "recording_name": "rec-1",
        "caller_id_num": "+15551234567",
        "connected_line_num": "+15559876543",
        "direction": "inbound",
        "target_uri": "file:/var/spool/asterisk/recording/rec-1.wav",
    }
    base.update(overrides)
    return base


@pytest.fixture
def build(monkeypatch):
    """Build a vCon with the real builder, audio download stubbed out."""
    monkeypatch.setattr(AsteriskVconBuilder, "_download_recording", lambda self, rd: AUDIO)

    def _build(**builder_kwargs):
        builder = AsteriskVconBuilder(recording_format="wav", **builder_kwargs)
        vcon = builder.build(AsteriskRecordingData(_event()))
        assert vcon is not None, "builder returned None"
        return vcon

    return _build


def test_lawful_basis_emitted_when_configured(build):
    vcon = build(lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"]))

    found = vcon.find_lawful_basis_attachments()
    assert len(found) == 1
    attachment = found[0]

    assert attachment["purpose"] == "lawful_basis"
    assert "type" not in attachment
    assert attachment["party"] == 0
    assert attachment["dialog"] == 0
    assert attachment["encoding"] == "json"
    assert attachment["mediatype"] == "application/json"
    # draft-ietf-vcon-vcon-core-04 §2.3.2 (CDDL `body: any`): for
    # `encoding: "json"`, body is the JSON value itself, not a `json.dumps`
    # string.
    assert isinstance(attachment["body"], dict), "body must be the JSON object, not a string"

    body = attachment["body"]
    assert body["lawful_basis"] == "consent"

    assert "lawful_basis" in vcon.to_dict()["extensions"]
    valid, errors = vcon.is_valid()
    assert valid, errors


def test_lawful_basis_absent_and_warned_when_unset(build, caplog):
    with caplog.at_level(logging.WARNING, logger="core.base_builder"):
        vcon = build(lawful_basis=None)

    assert vcon.find_lawful_basis_attachments() == []
    assert "lawful_basis" not in (vcon.to_dict().get("extensions") or [])
    assert any("no lawful_basis attachment" in r.getMessage() for r in caplog.records)


def test_filesystem_media_backend_produces_url_and_hash_not_inline_body(build, tmp_path):
    publisher = FilesystemPublisher(destination=tmp_path, base_url="https://media.test/rec")
    vcon = build(publisher=publisher)
    dialog = vcon.to_dict()["dialog"][0]

    assert dialog["url"].startswith("https://media.test/rec/")
    expected_hash = "sha512-" + base64.urlsafe_b64encode(
        hashlib.sha512(AUDIO).digest()
    ).decode().rstrip("=")
    assert dialog["content_hash"] == expected_hash
    assert "body" not in dialog, "audio must not be inlined when publishing"
    assert "encoding" not in dialog

    valid, errors = vcon.is_valid()
    assert valid, errors
