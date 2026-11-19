"""Tests for the SignalWire vCon builder."""

import json
from pathlib import Path
from unittest.mock import patch

import jsonschema
import pytest

from adapters.signalwire.builder import SignalWireRecordingData, SignalWireVconBuilder
from core.lawful_basis import LawfulBasisConfig
from core.media_publisher import NonePublisher

WG_SCHEMA_PATH = Path(__file__).parents[2] / "schema" / "vcon-core-05-appendix-c-json-schema.json"
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


class TestCallSession:
    """Core-05: one Session Object per call groups its segment recordings.

    Replaces the core-04 recording-set dialog (CON-1105), which core-05 removed.
    """

    def test_single_recording_is_one_session_with_one_dialog(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=1))
        assert [d.get("type") for d in vcon.vcon_dict["dialog"]] == ["recording"]
        assert "recording_set" not in vcon.vcon_dict["dialog"][0]
        assert [s["dialog"] for s in vcon.vcon_dict["sessions"]] == [[0]]

    def test_multi_recording_is_one_session_listing_every_segment(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        assert [d["type"] for d in vcon.vcon_dict["dialog"]] == ["recording"] * 3
        [session] = vcon.vcon_dict["sessions"]
        assert session["dialog"] == [0, 1, 2]

    def test_no_recording_set_dialog_or_back_reference(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=3))
        for dialog in vcon.vcon_dict["dialog"]:
            assert dialog["type"] != "recording-set"
            assert "recording_set" not in dialog
            assert "recordings" not in dialog

    def test_session_parties_cover_the_call(self):
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(make_data(num_recordings=2))
        assert vcon.vcon_dict["sessions"][0]["parties"] == [0, 1]

    def test_session_start_is_earliest_segment_start(self):
        data = make_data(num_recordings=2)
        data.recordings[0]["date_created"] = "Mon, 15 Jan 2024 10:29:30 +0000"
        data.recordings[1]["date_created"] = "Mon, 15 Jan 2024 10:31:00 +0000"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        assert vcon.vcon_dict["sessions"][0]["start"] == "2024-01-15T10:29:30+00:00"

    def test_session_duration_spans_call(self):
        """start/duration together identify the interval spanned by the segments."""
        data = make_data(num_recordings=2)
        data.recordings[0]["date_created"] = "Mon, 15 Jan 2024 10:29:30 +0000"
        data.recordings[0]["duration"] = "30"
        data.recordings[1]["date_created"] = "Mon, 15 Jan 2024 10:31:00 +0000"
        data.recordings[1]["duration"] = "20"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        # second segment ends at 10:31:20, first segment starts at 10:29:30
        assert vcon.vcon_dict["sessions"][0]["duration"] == pytest.approx(110.0)

    def test_session_duration_omitted_when_a_segment_is_unparseable(self):
        """Don't fabricate a call duration when the inputs don't support one."""
        data = make_data(num_recordings=2)
        data.recordings[1]["duration"] = "not-a-number"
        builder = SignalWireVconBuilder(download_recordings=False)
        vcon = builder.build(data)
        assert "duration" not in vcon.vcon_dict["sessions"][0]

    def test_attachments_are_scoped(self):
        """Per-recording metadata keeps its dialog; the rest names the call's Session."""
        builder = SignalWireVconBuilder(
            download_recordings=False,
            lawful_basis=LawfulBasisConfig(lawful_basis="consent", purposes=["recording"]),
        )
        vcon = builder.build(make_data(num_recordings=2))
        for attachment in vcon.vcon_dict["attachments"]:
            if attachment["purpose"] == "recording_metadata":
                assert attachment["dialog"] in (0, 1)
            else:
                assert attachment["session"] in (0, [0]), attachment["purpose"]
        assert vcon.is_valid() == (True, [])

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
