"""Log lines must carry neither full phone numbers nor signed-URL query strings."""

import logging
from unittest.mock import MagicMock, patch

import requests

from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder
from core.log_safe import mask_number, redact_text, redact_url

FROM_NUMBER = "+15555550142"
TO_NUMBER = "+15555550177"
SIGNED_URL = (
    "https://recordings.example.test/bucket/rec-1.wav"
    "?X-Amz-Signature=deadbeef0123&X-Amz-Credential=AKIAFAKE"
)
SECRETS = ("deadbeef0123", "AKIAFAKE", "X-Amz-Signature", "?X-Amz")


def _recording() -> TelnyxRecordingData:
    return TelnyxRecordingData(
        {
            "data": {
                "event_type": "call.recording.saved",
                "payload": {
                    "recording_id": "rec-1",
                    "from": FROM_NUMBER,
                    "to": TO_NUMBER,
                    "direction": "incoming",
                    "duration_millis": 30000,
                    "recording_urls": {"wav": SIGNED_URL},
                    "start_time": "2024-01-15T10:29:30Z",
                },
            }
        }
    )


def _own_log(caplog) -> str:
    """Log text from this repo's loggers; the third-party `vcon` library logs under `vcon.*`."""
    return "\n".join(
        r.getMessage() for r in caplog.records if r.name.split(".")[0] in {"adapters", "core"}
    )


def _assert_clean(text: str) -> None:
    for number in (FROM_NUMBER, TO_NUMBER, FROM_NUMBER[1:], TO_NUMBER[1:]):
        assert number not in text
    for secret in SECRETS:
        assert secret not in text


class TestHelpers:
    def test_mask_number_keeps_last_four(self):
        assert mask_number(FROM_NUMBER) == "***0142"
        assert mask_number("+1 (555) 555-0177") == "***0177"

    def test_mask_number_edge_cases(self):
        assert mask_number(None) == "-"
        assert mask_number("") == "-"
        assert mask_number("12") == "***"

    def test_redact_url_drops_query_fragment_and_userinfo(self):
        assert redact_url(SIGNED_URL) == "https://recordings.example.test/bucket/rec-1.wav"
        assert redact_url("https://user:pw@host.test/a#frag") == "https://host.test/a"

    def test_redact_text_cleans_urls_in_exception_text(self):
        err = requests.ConnectionError(
            f"HTTPSConnectionPool: Max retries with url: {SIGNED_URL} (x)"
        )
        cleaned = redact_text(err)
        assert "deadbeef0123" not in cleaned
        assert "https://recordings.example.test/bucket/rec-1.wav" in cleaned


class TestBuildLog:
    def test_created_vcon_line_masks_numbers_and_keeps_prefix(self, caplog):
        builder = TelnyxVconBuilder(download_recordings=False)
        with caplog.at_level(logging.DEBUG):
            vcon = builder.build(_recording())
        assert vcon is not None
        assert f"Created vCon {vcon.uuid}" in _own_log(caplog)
        assert "***0142" in _own_log(caplog)
        assert "***0177" in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    def test_base_builder_created_line_masks_numbers(self, caplog):
        from core.base_builder import BaseVconBuilder

        class Stub(BaseVconBuilder):
            ADAPTER_SOURCE = "stub"

            def _download_recording(self, recording_data):
                return None

        data = _recording()
        builder = Stub(download_recordings=False)
        with caplog.at_level(logging.DEBUG):
            vcon = builder.build(data)
        assert vcon is not None
        assert f"Created vCon {vcon.uuid}" in _own_log(caplog)
        assert "(from: ***0142, to: ***0177)" in _own_log(caplog)
        _assert_clean(_own_log(caplog))


class TestTelnyxDownloadLog:
    @patch("adapters.telnyx.builder.requests.get")
    def test_debug_line_has_no_query_string(self, mock_get, caplog):
        response = MagicMock()
        response.content = b"audio"
        mock_get.return_value = response
        builder = TelnyxVconBuilder(api_key="test-api-key")
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(_recording()) == b"audio"
        assert "https://recordings.example.test/bucket/rec-1.wav" in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    @patch("adapters.telnyx.builder.requests.get")
    def test_failed_download_does_not_log_signed_url(self, mock_get, caplog):
        mock_get.side_effect = requests.ConnectionError(
            f"Max retries exceeded with url: {SIGNED_URL}"
        )
        builder = TelnyxVconBuilder(api_key="test-api-key")
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(_recording()) is None
        assert "Failed to download Telnyx recording rec-1" in _own_log(caplog)
        _assert_clean(_own_log(caplog))


def test_twilio_failed_download_does_not_log_query(caplog):
    module = "adapters.twilio.builder"
    from adapters.twilio.builder import TwilioVconBuilder

    builder = TwilioVconBuilder(twilio_auth=("ACfake", "fake"))
    data = MagicMock()
    data.recording_url = SIGNED_URL.split("?")[0] + "?sig=deadbeef0123"
    with patch(f"{module}.requests.get", side_effect=requests.ConnectionError(SIGNED_URL)):
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(data) is None
    assert "Error downloading recording" in _own_log(caplog)
    assert "deadbeef0123" not in _own_log(caplog)
    assert "AKIAFAKE" not in _own_log(caplog)
