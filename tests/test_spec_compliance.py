"""Spec compliance smoke tests for vCon output.

Locks in the spec-correct shape that the codebase produces. If a refactor
re-introduces `mimetype`, `encoding="base64"`, or strips `content_hash` from
external media, these tests fail before the bad output ships.

Based on the canonical assertions in vcon-dev/vcon-adapter-template.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.base_builder import BaseRecordingData, BaseVconBuilder


class _FakeRecordingData(BaseRecordingData):
    def __init__(self, *, url: str | None = None, duration: float | None = 30.0):
        self._url = url
        self._duration = duration

    @property
    def recording_id(self) -> str: return "rec-001"
    @property
    def from_number(self) -> str: return "+15551234567"
    @property
    def to_number(self) -> str: return "+15557654321"
    @property
    def direction(self) -> str: return "inbound"
    @property
    def recording_url(self) -> str | None: return self._url
    @property
    def duration_seconds(self) -> float | None: return self._duration
    @property
    def start_time(self) -> datetime: return datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    @property
    def platform_tags(self) -> dict: return {"call_sid": "CA123"}


class _FakeBuilder(BaseVconBuilder):
    ADAPTER_SOURCE = "test_platform"

    def __init__(self, *, audio: bytes | None = None, **kw):
        super().__init__(**kw)
        self._audio = audio

    def _download_recording(self, recording_data):
        return self._audio


def test_vcon_syntax_is_0_4_0():
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    assert v.vcon_dict["vcon"] == "0.4.0"


def test_no_group_or_redacted_placeholders():
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    assert "group" not in v.vcon_dict
    assert "redacted" not in v.vcon_dict


def test_dialog_uses_mediatype_not_mimetype():
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    dialog = v.vcon_dict["dialog"][0]
    assert "mediatype" in dialog, f"Dialog keys: {list(dialog.keys())}"
    assert "mimetype" not in dialog, "Spec field is `mediatype`, never `mimetype`"


def test_dialog_strips_empty_metadata_meta_placeholders():
    """vcon-lib Dialog.to_dict emits empty metadata/meta; the builder strips them."""
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    dialog = v.vcon_dict["dialog"][0]
    assert "metadata" not in dialog, f"Empty metadata placeholder leaked: {dialog!r}"
    assert "meta" not in dialog, f"Empty meta placeholder leaked: {dialog!r}"


def test_inline_audio_uses_base64url_not_base64():
    audio = b"fake wav bytes \xfe\xff\x00"
    v = _FakeBuilder(audio=audio, download_recordings=True).build(
        _FakeRecordingData(url="https://example.com/rec")
    )
    dialog = v.vcon_dict["dialog"][0]
    assert dialog["encoding"] == "base64url", f"got encoding={dialog['encoding']!r}"
    # body must be RFC 4648 URL-safe alphabet, no padding
    assert dialog["body"] == base64.urlsafe_b64encode(audio).rstrip(b"=").decode("ascii")
    assert "=" not in dialog["body"], "base64url body should have no padding"


def test_inline_audio_has_content_hash():
    audio = b"fake wav bytes"
    v = _FakeBuilder(audio=audio, download_recordings=True).build(
        _FakeRecordingData(url="https://example.com/rec")
    )
    dialog = v.vcon_dict["dialog"][0]
    assert "content_hash" in dialog
    assert dialog["content_hash"].startswith("sha512-")
    # Spec: base64url-encoded digest, no padding, no +/
    digest = dialog["content_hash"].removeprefix("sha512-")
    assert "=" not in digest and "+" not in digest and "/" not in digest


def test_tags_attachment_has_purpose_and_party_and_dialog():
    """vcon-lib >=0.9.3 fix: tags attachment includes party/dialog indices."""
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    tags_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "tags"]
    assert tags_atts, "Expected at least one tags attachment"
    for att in tags_atts:
        assert "type" not in att, "Spec attachment field is `purpose`, never `type`"
        assert att.get("party") == 0
        assert att.get("dialog") == 0


@pytest.mark.parametrize("legacy", ['"appended"', '"must_support"'])
def test_no_legacy_field_names_in_serialized_vcon(legacy):
    v = _FakeBuilder(download_recordings=False).build(_FakeRecordingData())
    serialized = json.dumps(v.vcon_dict, default=str)
    assert legacy not in serialized
