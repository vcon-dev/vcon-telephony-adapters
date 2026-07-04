"""Tests for Twilio multi-mode builders."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from adapters.twilio.builder import (
    TwilioConversationsBuilder,
    TwilioFaxBuilder,
    TwilioMessagingBuilder,
    TwilioVideoBuilder,
    TwilioVoiceStatusBuilder,
    TwilioVconBuilder,
)
from adapters.twilio.builder.voice_recording import TwilioRecordingData

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "twilio"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestVoiceRecordingEnhancements:
    def test_application_and_session_id(self, basic_webhook_data):
        builder = TwilioVconBuilder(download_recordings=False)
        data = TwilioRecordingData(basic_webhook_data)
        vcon = builder.build(data)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["application"] == "twilio_voice"
        assert dialog["session_id"] == basic_webhook_data["CallSid"]
        assert vcon.get_tag("communication_mode") == "PSTN"


class TestVoiceStatusBuilder:
    def test_no_answer_incomplete_dialog(self):
        form = _load("voice_status_no_answer.json")
        builder = TwilioVoiceStatusBuilder()
        assert builder.should_process(form)
        vcon = builder.build(form)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["type"] == "incomplete"
        assert dialog["disposition"] == "no-answer"
        assert dialog["application"] == "twilio_voice"

    def test_skips_completed(self):
        builder = TwilioVoiceStatusBuilder()
        assert not builder.should_process({"CallStatus": "completed"})


class TestMessagingBuilder:
    def test_sms_text_dialog(self):
        form = _load("sms_inbound.json")
        builder = TwilioMessagingBuilder(download_media=False)
        vcon = builder.build(form)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["type"] == "text"
        assert dialog["mediatype"] == "text/plain"
        assert dialog["message_id"] == form["MessageSid"]
        assert dialog["application"] == "twilio_sms"
        assert vcon.get_tag("communication_mode") == "SMS"

    def test_whatsapp_channel(self):
        form = _load("whatsapp_inbound.json")
        builder = TwilioMessagingBuilder(download_media=False)
        vcon = builder.build(form)
        assert vcon.vcon_dict["dialog"][0]["application"] == "twilio_whatsapp"
        assert vcon.get_tag("communication_mode") == "WhatsApp"

    @patch("adapters.twilio.builder.messaging.requests.get")
    def test_mms_media_attachment(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, content=b"jpegbytes")
        form = _load("mms_inbound.json")
        builder = TwilioMessagingBuilder(download_media=True, twilio_auth=("AC", "token"))
        vcon = builder.build(form)
        attachments = vcon.vcon_dict.get("attachments", [])
        assert any(a.get("purpose") == "mms_media" for a in attachments)


class TestFaxBuilder:
    @patch("adapters.twilio.builder.fax.requests.get")
    def test_fax_recording_dialog(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, content=b"%PDF-1.4")
        form = _load("fax_received.json")
        builder = TwilioFaxBuilder(download_fax=True)
        vcon = builder.build(form)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["type"] == "recording"
        assert dialog["mediatype"] == "application/pdf"
        assert dialog["application"] == "twilio_fax"
        assert vcon.get_tag("communication_mode") == "Fax"


class TestVideoBuilder:
    @patch("adapters.twilio.builder.video.requests.get")
    def test_video_dialog(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200, content=b"mp4bytes")
        form = _load("video_composition.json")
        builder = TwilioVideoBuilder(download_video=True)
        vcon = builder.build(form)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["type"] == "video"
        assert dialog["mediatype"] == "video/mp4"
        assert vcon.get_tag("communication_mode") == "WebRTC"


class TestConversationsBuilder:
    def test_conversations_text_dialog(self):
        payload = _load("conversations_message.json")
        builder = TwilioConversationsBuilder()
        assert builder.should_process(payload)
        vcon = builder.build(payload)
        dialog = vcon.vcon_dict["dialog"][0]
        assert dialog["type"] == "text"
        assert dialog["message_id"] == payload["MessageSid"]
        assert vcon.get_tag("conversation_sid") == payload["ConversationSid"]
