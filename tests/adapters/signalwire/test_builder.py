"""Tests for the SignalWire vCon builder."""

import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from adapters.signalwire.builder import SignalWireRecordingData, SignalWireVconBuilder
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import NonePublisher

WG_SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "vcon_json_schema.json"
WG_SCHEMA = json.loads(WG_SCHEMA_PATH.read_text())


def _mock_recording_response():
    response = type("Response", (), {})()
    response.content = b"fake-wav-bytes"
    response.raise_for_status = lambda: None
    return response


def make_data(num_recordings: int = 2):
    call_meta = {"sid": "CA123", "to_formatted": "(555) 123-4567", "from_formatted": "5559876543"}
    recordings = [
        {
            "sid": f"RE{i}",
            "account_sid": "AC1",
            "call_sid": "CA123",
            "channels": 1,
            "date_created": "Mon, 15 Jan 2024 10:29:30 +0000",
            "duration": "30",
            "recording_url": f"https://signalwire.example.com/recordings/RE{i}",
        }
        for i in range(num_recordings)
    ]
    transcripts = {f"RE{i}": [{"text": f"transcript {i}"}] for i in range(num_recordings)}
    return SignalWireRecordingData(
        call_meta=call_meta, recordings=recordings, transcriptions_by_recording_sid=transcripts
    )


class TestSignalWireVconBuilder:
    def test_one_dialog_per_recording(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        recordings = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording"]
        assert len(recordings) == 3

    def test_parties_formatted_e164(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data())
        parties = vcon.vcon_dict["parties"]
        assert parties[0]["tel"] == "+15551234567"
        assert parties[1]["tel"] == "+15559876543"

    def test_transcript_analysis_per_dialog(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=2))
        transcripts = [a for a in vcon.vcon_dict.get("analysis", []) if a["type"] == "transcript"]
        assert len(transcripts) == 2

    def test_recording_metadata_attachment_per_dialog(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=2))
        metas = [
            a
            for a in vcon.vcon_dict.get("attachments", [])
            if a.get("purpose") == "recording_metadata"
        ]
        assert len(metas) == 2

    def test_call_sid_tag(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data())
        tags_attachment = next(a for a in vcon.vcon_dict["attachments"] if a["purpose"] == "tags")
        assert "call_sid:CA123" in tags_attachment["body"]

    def test_no_lawful_basis_by_default(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert lawful == []

    def test_lawful_basis_applied_when_configured(self):
        builder = SignalWireVconBuilder(
            download_recordings=False, lawful_basis=LawfulBasisConfig(lawful_basis="consent")
        )
        vcon = builder.build(make_data())
        lawful = [
            a for a in vcon.vcon_dict.get("attachments", []) if a.get("purpose") == "lawful_basis"
        ]
        assert len(lawful) == 1

    def test_dialogs_use_mediatype_not_mimetype(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=1))
        recording = next(d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording")
        assert recording.get("mediatype") == "audio/wav"
        assert "mimetype" not in recording

    def test_duration_string_coerced_to_number(self):
        """SignalWire's Recordings.json returns duration as a numeric string
        (e.g. "30"); the dialog's duration field must be numeric, not a str,
        or it fails WG schema validation (found via schema check, CON-703)."""
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=1))
        recording = next(d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording")
        assert recording["duration"] == 30.0
        assert isinstance(recording["duration"], float)

    def test_unparseable_duration_omitted_without_raising(self):
        data = make_data(num_recordings=1)
        data.recordings[0]["duration"] = "not-a-number"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        recording = next(d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording")
        assert recording.get("duration") is None


class TestRecordingSet:
    """CON-1105: segmented calls (>1 recording) get a recording-set dialog
    per draft-ietf-vcon-vcon-core-04 Sec. 4.3.1.2/4.3.6/4.3.7."""

    def test_single_recording_gets_no_recording_set(self):
        """Single-segment calls are unchanged: no recording-set dialog, and
        the lone recording dialog carries no recording_set back-reference."""
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=1))
        types = [d.get("type") for d in vcon.vcon_dict["dialog"]]
        assert types == ["recording"]
        assert "recording_set" not in vcon.vcon_dict["dialog"][0]

    def test_multi_recording_adds_one_recording_set_dialog(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        sets = [d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"]
        assert len(sets) == 1

    def test_recording_set_is_appended_after_segments(self):
        """The set dialog is last, so the earlier per-segment dialog indices
        (already used by attachments/analysis keyed by dialog_idx) don't
        shift."""
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        dialogs = vcon.vcon_dict["dialog"]
        assert [d["type"] for d in dialogs] == [
            "recording",
            "recording",
            "recording",
            "recording-set",
        ]

    def test_recording_set_recordings_indexes_the_segments(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        assert recording_set["recordings"] == [0, 1, 2]

    def test_segment_dialogs_reference_back_to_the_set(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        dialogs = vcon.vcon_dict["dialog"]
        set_idx = next(i for i, d in enumerate(dialogs) if d["type"] == "recording-set")
        for i in range(3):
            assert dialogs[i]["recording_set"] == set_idx

    def test_recording_set_has_no_dialog_content_fields(self):
        """Sec. 4.3.1.2: a recording-set dialog has no Dialog Content, so
        mediatype/body/url/filename/encoding/content_hash MUST NOT appear."""
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=2))
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        for forbidden in ("mediatype", "body", "url", "filename", "encoding", "content_hash"):
            assert forbidden not in recording_set

    def test_recording_set_parties_covers_the_call(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=2))
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        assert recording_set["parties"] == [0, 1]

    def test_recording_set_start_is_earliest_segment_start(self):
        data = make_data(num_recordings=2)
        data.recordings[0]["date_created"] = "Mon, 15 Jan 2024 10:29:30 +0000"
        data.recordings[1]["date_created"] = "Mon, 15 Jan 2024 10:31:00 +0000"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        assert recording_set["start"] == "2024-01-15T10:29:30+00:00"

    def test_recording_set_duration_spans_call(self):
        """start/duration together SHOULD identify the complete interval
        spanned by the member recordings (Sec. 4.3.3)."""
        data = make_data(num_recordings=2)
        data.recordings[0]["date_created"] = "Mon, 15 Jan 2024 10:29:30 +0000"
        data.recordings[0]["duration"] = "30"
        data.recordings[1]["date_created"] = "Mon, 15 Jan 2024 10:31:00 +0000"
        data.recordings[1]["duration"] = "20"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        # second segment ends at 10:31:20, first segment starts at 10:29:30
        assert recording_set["duration"] == pytest.approx(110.0)

    def test_recording_set_duration_omitted_when_a_segment_is_unparseable(self):
        """Don't fabricate a call duration when the inputs don't support one."""
        data = make_data(num_recordings=2)
        data.recordings[1]["duration"] = "not-a-number"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        recording_set = next(
            d for d in vcon.vcon_dict["dialog"] if d.get("type") == "recording-set"
        )
        assert "duration" not in recording_set

    def test_multi_recording_vcon_validates_against_wg_schema(self):
        # A publisher is configured (rather than download_recordings=False)
        # so dialogs get a content_hash alongside their url: the schema
        # requires content_hash whenever url is present, independent of
        # recording-set. `requests.get` is mocked to avoid a real network
        # call to the fixture's fake recording URL.
        builder = SignalWireVconBuilder(
            publisher=NonePublisher(base_url="https://cdn.example.com/audio")
        )
        with patch(
            "adapters.signalwire.builder.requests.get", return_value=_mock_recording_response()
        ):
            vcon = builder.build(make_data(num_recordings=2))
        jsonschema.validate(instance=vcon.vcon_dict, schema=WG_SCHEMA)

    def test_single_recording_vcon_still_validates_against_wg_schema(self):
        builder = SignalWireVconBuilder(
            publisher=NonePublisher(base_url="https://cdn.example.com/audio")
        )
        with patch(
            "adapters.signalwire.builder.requests.get", return_value=_mock_recording_response()
        ):
            vcon = builder.build(make_data(num_recordings=1))
        jsonschema.validate(instance=vcon.vcon_dict, schema=WG_SCHEMA)
