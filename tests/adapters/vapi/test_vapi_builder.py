"""VAPI adapter spec compliance tests."""
from __future__ import annotations

import json

from adapters.vapi.builder import VapiVconBuilder


def _sample_message(*, with_recording=True, with_transcript=True, with_analysis=True) -> dict:
    msg = {
        "type": "end-of-call-report",
        "startedAt": "2026-05-19T12:00:00Z",
        "endedAt": "2026-05-19T12:00:30Z",
        "call": {"id": "call_abc123"},
        "cost": 0.42,
        "durationSeconds": 30,
        "endedReason": "customer-hung-up",
        "artifact": {
            "messages": [
                {"role": "system", "message": "Greet user", "time": 1747657200000},
                {"role": "user", "message": "Hi there", "time": 1747657201000},
                {"role": "bot", "message": "Hello, how can I help?", "time": 1747657202000},
                {"role": "user", "message": "Bye", "time": 1747657228000},
            ]
        },
    }
    if with_recording:
        msg["recordingUrl"] = "https://example.com/vapi/audio.mp3"
    if with_transcript:
        msg["transcript"] = "user: Hi there\nbot: Hello, how can I help?\nuser: Bye"
    if with_analysis:
        msg["analysis"] = {
            "summary": "Brief greeting, customer hung up quickly.",
            "successEvaluation": True,
        }
    return msg


def test_returns_none_for_non_end_of_call_messages():
    v = VapiVconBuilder().build({"type": "status-update"})
    assert v is None


def test_vcon_syntax_is_0_4_0():
    v = VapiVconBuilder().build(_sample_message())
    assert v.vcon_dict["vcon"] == "0.4.0"


def test_two_parties_customer_and_agent():
    v = VapiVconBuilder().build(_sample_message())
    parties = v.vcon_dict["parties"]
    assert len(parties) == 2
    assert parties[0]["name"] == "Customer"
    assert parties[1]["name"] == "AI Agent"


def test_only_user_and_bot_messages_become_dialogs():
    """System messages should be skipped (only user/bot map to dialogs)."""
    v = VapiVconBuilder().build(_sample_message())
    dialogs = v.vcon_dict["dialog"]
    assert len(dialogs) == 3  # 2 user + 1 bot from the sample; system is skipped


def test_dialogs_use_mediatype_text_plain():
    v = VapiVconBuilder().build(_sample_message())
    for dialog in v.vcon_dict["dialog"]:
        assert dialog["mediatype"] == "text/plain"
        assert "mimetype" not in dialog


def test_dialog_originator_mapping():
    v = VapiVconBuilder().build(_sample_message())
    dialogs = v.vcon_dict["dialog"]
    # user messages → originator=0, bot → originator=1
    assert dialogs[0]["originator"] == 0  # user "Hi there"
    assert dialogs[1]["originator"] == 1  # bot
    assert dialogs[2]["originator"] == 0  # user "Bye"


def test_dialogs_strip_empty_metadata_placeholders():
    v = VapiVconBuilder().build(_sample_message())
    for dialog in v.vcon_dict["dialog"]:
        assert "metadata" not in dialog
        assert "meta" not in dialog


def test_recording_url_is_attachment_with_purpose_and_indices():
    v = VapiVconBuilder().build(_sample_message())
    recording_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "recording_url"]
    assert len(recording_atts) == 1
    att = recording_atts[0]
    assert "type" not in att
    assert att["party"] == 0
    assert att["dialog"] == 0
    assert att["body"] == "https://example.com/vapi/audio.mp3"


def test_transcript_in_analysis_not_attachments():
    v = VapiVconBuilder().build(_sample_message())
    transcript_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "transcript"]
    assert transcript_atts == []
    transcripts = [a for a in v.vcon_dict["analysis"] if a.get("type") == "transcript"]
    assert len(transcripts) == 1
    a = transcripts[0]
    assert a["vendor"] == "vapi"
    assert "schema_version" not in a


def test_summary_and_success_evaluation_as_analysis():
    v = VapiVconBuilder().build(_sample_message())
    analyses = v.vcon_dict["analysis"]
    types = {a["type"] for a in analyses}
    assert "summary" in types
    assert "success_evaluation" in types


def test_call_record_attachment_has_purpose_indices_and_json_body():
    v = VapiVconBuilder().build(_sample_message())
    call_record = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "call_record"]
    assert len(call_record) == 1
    att = call_record[0]
    assert "type" not in att
    assert att["party"] == 0
    assert att["dialog"] == 0
    assert att["encoding"] == "json"
    body = json.loads(att["body"])
    assert body["cost"] == 0.42
    assert body["durationSeconds"] == 30


def test_tags_attachment_has_party_and_dialog():
    v = VapiVconBuilder().build(_sample_message())
    tags_atts = [a for a in v.vcon_dict["attachments"] if a.get("purpose") == "tags"]
    assert tags_atts
    for att in tags_atts:
        assert att["party"] == 0
        assert att["dialog"] == 0


def test_no_legacy_field_names():
    v = VapiVconBuilder().build(_sample_message())
    serialized = json.dumps(v.vcon_dict, default=str)
    assert '"appended"' not in serialized
    assert '"must_support"' not in serialized
