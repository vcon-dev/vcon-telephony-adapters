"""Tests for the SignalWire vCon builder."""

from adapters.signalwire.builder import SignalWireRecordingData, SignalWireVconBuilder
from core.lawful_basis import LawfulBasisConfig


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
