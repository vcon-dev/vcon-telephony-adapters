"""SignalWire adapter spec compliance tests."""
from __future__ import annotations

import json

from adapters.signalwire.builder import (
    SignalWireRecordingData,
    SignalWireVconBuilder,
    _format_to_e164,
)


def _sample_data(with_transcript: bool = True) -> SignalWireRecordingData:
    call_meta = {
        "sid": "CA12345",
        "to_formatted": "(555) 123-4567",
        "from_formatted": "(555) 765-4321",
        "duration": "60",
        "status": "completed",
    }
    recording = {
        "sid": "RE12345",
        "account_sid": "AC12345",
        "call_sid": "CA12345",
        "duration": 60,
        "channels": 2,
        "date_created": "Tue, 19 May 2026 15:24:24 -0000",
        "recording_url": "https://example.signalwire.com/rec/RE12345.wav",
    }
    transcripts = {"RE12345": []}
    if with_transcript:
        transcripts["RE12345"] = [{"sid": "TR12345", "text": "Hello, world."}]
    return SignalWireRecordingData(
        call_meta=call_meta,
        recordings=[recording],
        transcriptions_by_recording_sid=transcripts,
    )


def test_format_to_e164():
    assert _format_to_e164("(555) 123-4567") == "+15551234567"
    assert _format_to_e164("+1 (555) 123-4567") == "+15551234567"
    assert _format_to_e164(None) is None
    assert _format_to_e164("") is None


def test_vcon_syntax_is_0_4_0():
    v = SignalWireVconBuilder().build(_sample_data())
    assert v.vcon_dict["vcon"] == "0.4.0"


def test_no_group_or_redacted_placeholders():
    v = SignalWireVconBuilder().build(_sample_data())
    assert "group" not in v.vcon_dict
    assert "redacted" not in v.vcon_dict


def test_two_parties_e164():
    v = SignalWireVconBuilder().build(_sample_data())
    parties = v.vcon_dict["parties"]
    assert len(parties) == 2
    assert parties[0]["tel"] == "+15551234567"
    assert parties[1]["tel"] == "+15557654321"


def test_dialog_uses_mediatype_not_mimetype():
    v = SignalWireVconBuilder().build(_sample_data())
    dialog = v.vcon_dict["dialog"][0]
    assert dialog["mediatype"] == "audio/wav"
    assert "mimetype" not in dialog


def test_dialog_strips_empty_metadata_meta_placeholders():
    v = SignalWireVconBuilder().build(_sample_data())
    dialog = v.vcon_dict["dialog"][0]
    assert "metadata" not in dialog
    assert "meta" not in dialog


def test_recording_metadata_attachment_is_spec_correct():
    v = SignalWireVconBuilder().build(_sample_data(with_transcript=False))
    recording_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "recording_metadata"]
    assert len(recording_atts) == 1
    att = recording_atts[0]
    assert "type" not in att
    assert att["party"] == 0
    assert att["dialog"] == 0
    assert att["encoding"] == "json"
    body = json.loads(att["body"])
    assert body["sid"] == "RE12345"
    assert body["source"] == "SignalWire"


def test_transcript_goes_in_analysis_not_attachments():
    v = SignalWireVconBuilder().build(_sample_data(with_transcript=True))
    transcripts_in_attachments = [
        a for a in v.vcon_dict["attachments"] if a.get("purpose") == "transcript"
    ]
    assert transcripts_in_attachments == []
    transcripts_in_analysis = [
        a for a in v.vcon_dict["analysis"] if a.get("type") == "transcript"
    ]
    assert len(transcripts_in_analysis) == 1
    a = transcripts_in_analysis[0]
    assert a["vendor"] == "signalwire"
    assert "schema" in a
    assert "schema_version" not in a
    assert a["encoding"] == "json"
    body = json.loads(a["body"])
    assert body["text"] == "Hello, world."


def test_tags_attachment_has_purpose_and_party_and_dialog():
    v = SignalWireVconBuilder().build(_sample_data())
    tags_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "tags"]
    assert tags_atts, "Expected tags attachment"
    for att in tags_atts:
        assert "type" not in att
        assert att["party"] == 0
        assert att["dialog"] == 0


def test_no_legacy_field_names():
    v = SignalWireVconBuilder().build(_sample_data())
    serialized = json.dumps(v.vcon_dict)
    assert '"appended"' not in serialized
    assert '"must_support"' not in serialized
