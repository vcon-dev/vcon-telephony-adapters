"""Comprehensive tests for vCon builder module."""

import base64
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from twilio_adapter.builder import (
    TwilioRecordingData,
    VconBuilder,
    MIME_TYPES,
)


# =============================================================================
# TwilioRecordingData Tests
# =============================================================================

class TestTwilioRecordingDataBasicParsing:
    """Tests for basic TwilioRecordingData field parsing."""

    def test_core_identifiers(self, basic_webhook_data):
        """Core identifiers are parsed correctly."""
        data = TwilioRecordingData(basic_webhook_data)

        assert data.recording_sid == "REtest1234567890abcdef12345678"
        assert data.account_sid == "ACtest1234567890abcdef12345678"
        assert data.call_sid == "CAtest1234567890abcdef12345678"

    def test_recording_details(self, basic_webhook_data):
        """Recording details are parsed correctly."""
        data = TwilioRecordingData(basic_webhook_data)

        assert data.recording_url == "https://api.twilio.com/2010-04-01/Accounts/ACtest123/Recordings/REtest123"
        assert data.recording_status == "completed"
        assert data.recording_duration == "120"
        assert data.recording_channels == "1"
        assert data.recording_source == "RecordVerb"

    def test_participant_numbers(self, basic_webhook_data):
        """Participant phone numbers are parsed correctly."""
        data = TwilioRecordingData(basic_webhook_data)

        assert data.from_number == "+15551234567"
        assert data.to_number == "+15559876543"

    def test_direction_and_status(self, basic_webhook_data):
        """Direction and call status are parsed correctly."""
        data = TwilioRecordingData(basic_webhook_data)

        assert data.direction == "inbound"
        assert data.call_status == "completed"

    def test_api_version(self, basic_webhook_data):
        """API version is parsed correctly."""
        data = TwilioRecordingData(basic_webhook_data)
        assert data.api_version == "2010-04-01"


class TestTwilioRecordingDataEmptyValues:
    """Tests for handling empty/missing webhook data."""

    def test_empty_dict(self):
        """Empty dict results in empty string defaults."""
        data = TwilioRecordingData({})

        assert data.recording_sid == ""
        assert data.account_sid == ""
        assert data.call_sid == ""
        assert data.recording_url == ""
        assert data.recording_status == ""
        assert data.from_number == ""
        assert data.to_number == ""
        assert data.direction == ""

    def test_missing_optional_fields(self):
        """Missing optional fields use defaults."""
        data = TwilioRecordingData({"RecordingSid": "RE123"})

        assert data.recording_sid == "RE123"
        assert data.recording_channels == "1"  # Default
        assert data.forwarded_from is None
        assert data.caller_city is None

    def test_raw_data_preserved(self, basic_webhook_data):
        """Raw webhook data is preserved."""
        data = TwilioRecordingData(basic_webhook_data)
        assert data._raw_data == basic_webhook_data


class TestTwilioRecordingDataCallerCalled:
    """Tests for Caller/Called field handling."""

    def test_caller_called_fallback_to_from_to(self):
        """Caller/Called fall back to From/To when not provided."""
        data = TwilioRecordingData({
            "From": "+15551111111",
            "To": "+15552222222",
        })

        assert data.caller == "+15551111111"
        assert data.called == "+15552222222"

    def test_explicit_caller_called(self):
        """Explicit Caller/Called override From/To."""
        data = TwilioRecordingData({
            "From": "+15551111111",
            "To": "+15552222222",
            "Caller": "+15553333333",
            "Called": "+15554444444",
        })

        assert data.caller == "+15553333333"
        assert data.called == "+15554444444"
        # From/To still available
        assert data.from_number == "+15551111111"
        assert data.to_number == "+15552222222"

    def test_partial_caller_called(self):
        """Partial Caller/Called with From/To fallback."""
        data = TwilioRecordingData({
            "From": "+15551111111",
            "To": "+15552222222",
            "Caller": "+15553333333",
            # Called not provided, should use To
        })

        assert data.caller == "+15553333333"
        assert data.called == "+15552222222"


class TestTwilioRecordingDataGeographic:
    """Tests for geographic metadata parsing."""

    def test_caller_geographic_data(self, full_webhook_data):
        """Caller geographic data is parsed."""
        data = TwilioRecordingData(full_webhook_data)

        assert data.caller_city == "San Francisco"
        assert data.caller_state == "CA"
        assert data.caller_zip == "94102"
        assert data.caller_country == "US"

    def test_called_geographic_data(self, full_webhook_data):
        """Called geographic data is parsed."""
        data = TwilioRecordingData(full_webhook_data)

        assert data.called_city == "New York"
        assert data.called_state == "NY"
        assert data.called_zip == "10001"
        assert data.called_country == "US"

    def test_missing_geographic_data(self, basic_webhook_data):
        """Missing geographic data returns None."""
        data = TwilioRecordingData(basic_webhook_data)

        assert data.caller_city is None
        assert data.caller_state is None
        assert data.caller_zip is None
        assert data.called_city is None


class TestTwilioRecordingDataDuration:
    """Tests for duration_seconds property."""

    def test_integer_duration(self):
        """Integer duration string is converted."""
        data = TwilioRecordingData({"RecordingDuration": "120"})
        assert data.duration_seconds == 120.0

    def test_float_duration(self):
        """Float duration string is converted."""
        data = TwilioRecordingData({"RecordingDuration": "45.5"})
        assert data.duration_seconds == 45.5

    def test_zero_duration(self):
        """Zero duration is valid."""
        data = TwilioRecordingData({"RecordingDuration": "0"})
        assert data.duration_seconds == 0.0

    def test_missing_duration(self):
        """Missing duration returns None."""
        data = TwilioRecordingData({})
        assert data.duration_seconds is None

    def test_none_duration(self):
        """None duration returns None."""
        data = TwilioRecordingData({"RecordingDuration": None})
        assert data.duration_seconds is None

    def test_invalid_duration_string(self):
        """Invalid duration string returns None."""
        data = TwilioRecordingData({"RecordingDuration": "invalid"})
        assert data.duration_seconds is None

    def test_empty_duration_string(self):
        """Empty duration string returns None."""
        data = TwilioRecordingData({"RecordingDuration": ""})
        assert data.duration_seconds is None

    def test_large_duration(self):
        """Large duration values work."""
        data = TwilioRecordingData({"RecordingDuration": "3600"})
        assert data.duration_seconds == 3600.0


class TestTwilioRecordingDataStartTime:
    """Tests for start_time property."""

    def test_valid_rfc2822_timestamp(self):
        """Valid RFC 2822 timestamp is parsed."""
        data = TwilioRecordingData({
            "RecordingStartTime": "Tue, 21 Jan 2025 10:30:00 +0000"
        })
        start = data.start_time

        assert start.year == 2025
        assert start.month == 1
        assert start.day == 21
        assert start.hour == 10
        assert start.minute == 30

    def test_missing_start_time_uses_now(self):
        """Missing start time returns current UTC time."""
        before = datetime.now(timezone.utc)
        data = TwilioRecordingData({})
        start_time = data.start_time
        after = datetime.now(timezone.utc)

        # Allow 1 second tolerance for test execution time
        assert before.replace(microsecond=0) <= start_time.replace(microsecond=0) <= after.replace(microsecond=0)

    def test_invalid_start_time_uses_now(self):
        """Invalid start time falls back to current time."""
        before = datetime.now(timezone.utc)
        data = TwilioRecordingData({"RecordingStartTime": "not-a-date"})
        start_time = data.start_time
        after = datetime.now(timezone.utc)

        # Allow 1 second tolerance for test execution time
        assert before.replace(microsecond=0) <= start_time.replace(microsecond=0) <= after.replace(microsecond=0)

    def test_start_time_with_timezone(self):
        """Start time with timezone offset is parsed correctly."""
        data = TwilioRecordingData({
            "RecordingStartTime": "Tue, 21 Jan 2025 10:30:00 -0500"
        })
        start = data.start_time

        # Should be in the original timezone
        assert start.hour == 10
        assert start.utcoffset().total_seconds() == -5 * 3600


# =============================================================================
# VconBuilder Tests
# =============================================================================

class TestVconBuilderInit:
    """Tests for VconBuilder initialization."""

    def test_default_init(self):
        """Default initialization."""
        builder = VconBuilder()

        assert builder.download_recordings is True
        assert builder.recording_format == "wav"
        assert builder.twilio_auth is None

    def test_custom_init(self):
        """Custom initialization."""
        builder = VconBuilder(
            download_recordings=False,
            recording_format="mp3",
            twilio_auth=("AC123", "token")
        )

        assert builder.download_recordings is False
        assert builder.recording_format == "mp3"
        assert builder.twilio_auth == ("AC123", "token")


class TestVconBuilderBuildBasic:
    """Tests for basic vCon building."""

    def test_build_creates_vcon(self, builder_no_download, basic_recording_data):
        """Build creates a vCon object."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon is not None
        assert vcon.uuid is not None

    def test_build_sets_uuid(self, builder_no_download, basic_recording_data):
        """Build generates unique UUID."""
        vcon1 = builder_no_download.build(basic_recording_data)
        vcon2 = builder_no_download.build(basic_recording_data)

        assert vcon1.uuid != vcon2.uuid

    def test_build_adds_parties(self, builder_no_download, basic_recording_data):
        """Build adds caller and callee as parties."""
        vcon = builder_no_download.build(basic_recording_data)

        assert len(vcon.parties) == 2
        assert vcon.parties[0]["tel"] == "+15551234567"
        assert vcon.parties[1]["tel"] == "+15559876543"

    def test_build_adds_dialog(self, builder_no_download, basic_recording_data):
        """Build adds a dialog entry."""
        vcon = builder_no_download.build(basic_recording_data)

        assert len(vcon.dialog) == 1
        assert vcon.dialog[0]["type"] == "recording"


class TestVconBuilderDialogContent:
    """Tests for dialog content in built vCons."""

    def test_dialog_type_is_recording(self, builder_no_download, basic_recording_data):
        """Dialog type is 'recording'."""
        vcon = builder_no_download.build(basic_recording_data)
        assert vcon.dialog[0]["type"] == "recording"

    def test_dialog_parties(self, builder_no_download, basic_recording_data):
        """Dialog includes both parties."""
        vcon = builder_no_download.build(basic_recording_data)
        assert vcon.dialog[0]["parties"] == [0, 1]

    def test_dialog_duration(self, builder_no_download, basic_recording_data):
        """Dialog includes duration when available."""
        vcon = builder_no_download.build(basic_recording_data)
        assert vcon.dialog[0]["duration"] == 120.0

    def test_dialog_duration_missing(self, builder_no_download):
        """Dialog handles missing duration."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        # Duration should be None or not in dialog dict
        assert vcon.dialog[0].get("duration") is None

    def test_dialog_mimetype_wav(self, builder_no_download, basic_recording_data):
        """Dialog MIME type for WAV format."""
        vcon = builder_no_download.build(basic_recording_data)
        assert vcon.dialog[0]["mimetype"] == "audio/wav"

    def test_dialog_mimetype_mp3(self, builder_mp3, basic_recording_data):
        """Dialog MIME type for MP3 format."""
        vcon = builder_mp3.build(basic_recording_data)
        assert vcon.dialog[0]["mimetype"] == "audio/mpeg"


class TestVconBuilderOriginator:
    """Tests for originator determination."""

    def test_originator_inbound_call(self, builder_no_download):
        """Inbound call sets originator to party 1 (external caller)."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
            "Direction": "inbound",
        })
        vcon = builder_no_download.build(data)

        assert vcon.dialog[0]["originator"] == 1

    def test_originator_outbound_call(self, builder_no_download):
        """Outbound call sets originator to party 0 (us)."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
            "Direction": "outbound",
        })
        vcon = builder_no_download.build(data)

        assert vcon.dialog[0]["originator"] == 0

    def test_originator_outbound_api(self, builder_no_download):
        """outbound-api direction sets originator to party 1 (not exact match)."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
            "Direction": "outbound-api",
        })
        vcon = builder_no_download.build(data)

        # outbound-api is not exactly "outbound", so falls to party 1
        assert vcon.dialog[0]["originator"] == 1

    def test_originator_missing_direction(self, builder_no_download):
        """Missing direction defaults to party 1."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        assert vcon.dialog[0]["originator"] == 1


class TestVconBuilderUrlReference:
    """Tests for URL reference in dialog."""

    def test_url_reference_when_not_downloading(self, builder_no_download):
        """URL reference set when not downloading."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        assert vcon.dialog[0]["url"] == "https://api.twilio.com/recording.wav"

    def test_url_reference_mp3_format(self, builder_mp3):
        """URL reference uses configured format."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_mp3.build(data)

        assert vcon.dialog[0]["url"] == "https://api.twilio.com/recording.mp3"

    def test_no_url_when_missing(self, builder_no_download):
        """No URL set when recording URL missing."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        # URL should not be set or be None
        assert vcon.dialog[0].get("url") is None


class TestVconBuilderDownload:
    """Tests for recording download functionality."""

    def test_download_success_embeds_audio(self, builder_with_auth, sample_audio_bytes):
        """Successful download embeds base64 audio."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = sample_audio_bytes
            mock_get.return_value = mock_response

            vcon = builder_with_auth.build(data)

            dialog = vcon.dialog[0]
            assert dialog["body"] == base64.b64encode(sample_audio_bytes).decode('utf-8')
            assert dialog["encoding"] == "base64"
            assert dialog["filename"] == "RE123.wav"

    def test_download_failure_falls_back_to_url(self, builder_with_auth):
        """Download failure falls back to URL reference."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            vcon = builder_with_auth.build(data)

            assert vcon.dialog[0]["url"] == "https://api.twilio.com/recording.wav"

    def test_download_exception_falls_back_to_url(self, builder_with_auth):
        """Download exception falls back to URL reference."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")

            vcon = builder_with_auth.build(data)

            assert vcon.dialog[0]["url"] == "https://api.twilio.com/recording.wav"

    def test_download_uses_auth(self, builder_with_auth):
        """Download uses configured Twilio auth."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"audio"
            mock_get.return_value = mock_response

            builder_with_auth.build(data)

            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs['auth'] == ("AC123", "auth_token")

    def test_download_url_format(self, builder_with_auth):
        """Download URL includes format extension."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"audio"
            mock_get.return_value = mock_response

            builder_with_auth.build(data)

            mock_get.assert_called_with(
                "https://api.twilio.com/recording.wav",
                auth=("AC123", "auth_token"),
                timeout=60
            )

    def test_download_timeout(self, builder_with_auth):
        """Download has 60 second timeout."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingUrl": "https://api.twilio.com/recording",
            "From": "+15551234567",
            "To": "+15559876543",
        })

        with patch('twilio_adapter.builder.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = b"audio"
            mock_get.return_value = mock_response

            builder_with_auth.build(data)

            call_kwargs = mock_get.call_args[1]
            assert call_kwargs['timeout'] == 60


class TestVconBuilderTags:
    """Tests for vCon metadata tags."""

    def test_source_tag(self, builder_no_download, basic_recording_data):
        """Source tag is set to twilio_adapter."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("source") == "twilio_adapter"

    def test_recording_sid_tag(self, builder_no_download, basic_recording_data):
        """Recording SID tag is set."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("recording_sid") == "REtest1234567890abcdef12345678"

    def test_call_sid_tag(self, builder_no_download, basic_recording_data):
        """Call SID tag is set."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("call_sid") == "CAtest1234567890abcdef12345678"

    def test_account_sid_tag(self, builder_no_download, basic_recording_data):
        """Account SID tag is set."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("account_sid") == "ACtest1234567890abcdef12345678"

    def test_direction_tag(self, builder_no_download, basic_recording_data):
        """Direction tag is set when available."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("direction") == "inbound"

    def test_duration_tag(self, builder_no_download, basic_recording_data):
        """Duration tag is set when available."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("duration_seconds") == "120.00"

    def test_geographic_tags(self, builder_no_download, full_recording_data):
        """Geographic tags are set when available."""
        vcon = builder_no_download.build(full_recording_data)

        assert vcon.get_tag("caller_city") == "San Francisco"
        assert vcon.get_tag("caller_state") == "CA"
        assert vcon.get_tag("caller_country") == "US"
        assert vcon.get_tag("called_city") == "New York"
        assert vcon.get_tag("called_state") == "NY"
        assert vcon.get_tag("called_country") == "US"

    def test_recording_source_tag(self, builder_no_download, basic_recording_data):
        """Recording source tag is set when available."""
        vcon = builder_no_download.build(basic_recording_data)

        assert vcon.get_tag("recording_source") == "RecordVerb"

    def test_missing_optional_tags(self, builder_no_download, minimal_webhook_data):
        """Missing optional tags are not included."""
        data = TwilioRecordingData(minimal_webhook_data)
        vcon = builder_no_download.build(data)

        assert vcon.get_tag("direction") is None
        assert vcon.get_tag("caller_city") is None
        assert vcon.get_tag("recording_source") is None


class TestMimeTypes:
    """Tests for MIME type mapping."""

    def test_wav_mime_type(self):
        """WAV MIME type is correct."""
        assert MIME_TYPES["wav"] == "audio/wav"

    def test_mp3_mime_type(self):
        """MP3 MIME type is correct."""
        assert MIME_TYPES["mp3"] == "audio/mpeg"

    def test_unsupported_format_fallback(self, builder_no_download):
        """Unsupported format falls back to audio/wav."""
        builder = VconBuilder(download_recordings=False, recording_format="unsupported")
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder.build(data)

        # Should fall back to wav
        assert vcon.dialog[0]["mimetype"] == "audio/wav"


class TestVconBuilderEdgeCases:
    """Tests for edge cases in VconBuilder."""

    def test_empty_phone_numbers(self, builder_no_download):
        """Handles empty phone numbers."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "",
            "To": "",
        })
        vcon = builder_no_download.build(data)

        assert vcon is not None
        assert vcon.parties[0]["tel"] == ""
        assert vcon.parties[1]["tel"] == ""

    def test_special_characters_in_recording_sid(self, builder_no_download):
        """Handles special characters in recording SID."""
        data = TwilioRecordingData({
            "RecordingSid": "RE_special-123.test",
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        assert vcon.get_tag("recording_sid") == "RE_special-123.test"

    def test_international_phone_numbers(self, builder_no_download):
        """Handles international phone numbers."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "From": "+442071234567",  # UK number
            "To": "+81312345678",     # Japan number
        })
        vcon = builder_no_download.build(data)

        assert vcon.parties[0]["tel"] == "+442071234567"
        assert vcon.parties[1]["tel"] == "+81312345678"

    def test_very_long_duration(self, builder_no_download):
        """Handles very long duration recordings."""
        data = TwilioRecordingData({
            "RecordingSid": "RE123",
            "RecordingDuration": "86400",  # 24 hours
            "From": "+15551234567",
            "To": "+15559876543",
        })
        vcon = builder_no_download.build(data)

        assert vcon.dialog[0]["duration"] == 86400.0
        assert vcon.get_tag("duration_seconds") == "86400.00"
