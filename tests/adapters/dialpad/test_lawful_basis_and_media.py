"""Lawful basis and external media for the Dialpad builder.

Fixtures are constructed from Dialpad's documented payload shape, not
captured traffic.
"""

import base64
import hashlib
import logging
from unittest.mock import MagicMock

from adapters.dialpad.builder import DialpadVconBuilder
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import FilesystemPublisher
from tests.adapters.dialpad.conftest import load_fixture

AUDIO = b"ID3 not real mp3, just deterministic bytes"


def make_client():
    client = MagicMock()
    client.get_call.return_value = None
    client.get_transcript.return_value = None
    client.get_ai_recap.return_value = None
    client.download_recording.return_value = AUDIO
    return client


def build(**kwargs):
    vcon = DialpadVconBuilder(make_client(), **kwargs).build(load_fixture("recording_event.json"))
    assert vcon is not None
    return vcon


def test_lawful_basis_emitted_when_configured():
    vcon = build(lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"]))
    found = vcon.find_lawful_basis_attachments()
    assert len(found) == 1
    assert found[0]["purpose"] == "lawful_basis"
    assert "type" not in found[0]
    assert found[0]["party"] == 0
    assert found[0]["dialog"] == 0
    assert found[0]["mediatype"] == "application/json"


def test_lawful_basis_dialog_is_a_recording_even_with_recording_set():
    event = load_fixture("recording_event.json")
    event["recording_details"] = load_fixture("call.json")["recording_details"][:2]
    vcon = DialpadVconBuilder(
        make_client(), lawful_basis=LawfulBasisConfig(lawful_basis="consent")
    ).build(event)
    index = vcon.find_lawful_basis_attachments()[0]["dialog"]
    assert vcon.vcon_dict["dialog"][index]["type"] == "recording"


def test_no_lawful_basis_when_unconfigured_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        vcon = build()
    assert vcon.find_lawful_basis_attachments() == []
    assert "no lawful_basis attachment" in caplog.text


def test_audio_embedded_as_base64url_by_default():
    dialog = build().vcon_dict["dialog"][0]
    assert dialog["encoding"] == "base64url"
    padded = dialog["body"] + "=" * (-len(dialog["body"]) % 4)
    assert base64.urlsafe_b64decode(padded) == AUDIO
    assert dialog["content_hash"].startswith("sha512-")
    assert "url" not in dialog


def test_audio_published_externally(tmp_path):
    publisher = FilesystemPublisher(destination=str(tmp_path), base_url="https://media.example.com")
    dialog = build(publisher=publisher).vcon_dict["dialog"][0]

    assert "body" not in dialog
    assert dialog["url"].startswith("https://media.example.com/")
    assert dialog["filename"] == "rec-1.mp3"
    digest = base64.urlsafe_b64encode(hashlib.sha512(AUDIO).digest()).decode().rstrip("=")
    assert dialog["content_hash"] == f"sha512-{digest}"
    assert (tmp_path / "rec-1.mp3").read_bytes() == AUDIO


def test_publish_failure_raises_retryable():
    import pytest

    from adapters.dialpad.builder import DialpadBuildError
    from core.media_publisher import PublishingError

    publisher = MagicMock()
    publisher.publish.side_effect = PublishingError("nope")
    with pytest.raises(DialpadBuildError):
        DialpadVconBuilder(make_client(), publisher=publisher).build(
            load_fixture("recording_event.json")
        )
