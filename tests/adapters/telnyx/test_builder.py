"""Tests for Telnyx vCon builder."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder


class TestTelnyxRecordingData:
    """Tests for TelnyxRecordingData class."""

    @pytest.fixture
    def sample_webhook_event(self):
        """Sample Telnyx webhook event."""
        return {
            "data": {
                "event_type": "call.recording.saved",
                "id": "event-123",
                "occurred_at": "2024-01-15T10:30:00Z",
                "payload": {
                    "call_control_id": "ctrl-abc123",
                    "call_leg_id": "leg-def456",
                    "call_session_id": "session-xyz789",
                    "connection_id": "conn-111",
                    "recording_id": "rec-222",
                    "recording_urls": {
                        "mp3": "https://api.telnyx.com/v2/recordings/rec-222.mp3",
                        "wav": "https://api.telnyx.com/v2/recordings/rec-222.wav"
                    },
                    "channels": "single",
                    "duration_millis": 30000,
                    "from": "+15551234567",
                    "to": "+15559876543",
                    "direction": "incoming",
                    "start_time": "2024-01-15T10:29:30Z"
                }
            }
        }

    @pytest.fixture
    def flat_webhook_event(self):
        """Sample flat Telnyx event (without nested data)."""
        return {
            "recording_id": "rec-flat-123",
            "from": "+15551111111",
            "to": "+15552222222",
            "direction": "outgoing",
            "duration_millis": 45000,
            "recording_urls": {
                "wav": "https://api.telnyx.com/v2/recordings/flat.wav"
            },
            "start_time": "2024-01-15T11:00:00Z"
        }

    def test_recording_id(self, sample_webhook_event):
        """Test recording_id extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.recording_id == "rec-222"

    def test_recording_id_flat(self, flat_webhook_event):
        """Test recording_id from flat event."""
        data = TelnyxRecordingData(flat_webhook_event)
        assert data.recording_id == "rec-flat-123"

    def test_call_session_id(self, sample_webhook_event):
        """Test call_session_id extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.call_session_id == "session-xyz789"

    def test_from_number(self, sample_webhook_event):
        """Test from_number extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.from_number == "+15551234567"

    def test_to_number(self, sample_webhook_event):
        """Test to_number extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.to_number == "+15559876543"

    def test_direction_incoming(self, sample_webhook_event):
        """Test direction mapping for incoming."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.direction == "inbound"

    def test_direction_outgoing(self, flat_webhook_event):
        """Test direction mapping for outgoing."""
        data = TelnyxRecordingData(flat_webhook_event)
        assert data.direction == "outbound"

    def test_recording_url_prefers_wav(self, sample_webhook_event):
        """Test recording_url prefers wav format."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.recording_url.endswith(".wav")

    def test_recording_urls(self, sample_webhook_event):
        """Test recording_urls dict."""
        data = TelnyxRecordingData(sample_webhook_event)
        urls = data.recording_urls
        assert "wav" in urls
        assert "mp3" in urls

    def test_duration_seconds(self, sample_webhook_event):
        """Test duration_seconds conversion from millis."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.duration_seconds == 30.0

    def test_duration_seconds_flat(self, flat_webhook_event):
        """Test duration_seconds from flat event."""
        data = TelnyxRecordingData(flat_webhook_event)
        assert data.duration_seconds == 45.0

    def test_start_time(self, sample_webhook_event):
        """Test start_time extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        assert data.start_time.year == 2024
        assert data.start_time.month == 1
        assert data.start_time.day == 15
        assert data.start_time.hour == 10
        assert data.start_time.minute == 29

    def test_start_time_fallback_to_occurred_at(self):
        """Test start_time falls back to occurred_at."""
        event = {
            "data": {
                "occurred_at": "2024-01-15T10:30:00Z",
                "payload": {
                    "recording_id": "rec-123",
                    "from": "+15551234567",
                    "to": "+15559876543",
                }
            }
        }
        data = TelnyxRecordingData(event)
        assert data.start_time.hour == 10
        assert data.start_time.minute == 30

    def test_platform_tags(self, sample_webhook_event):
        """Test platform_tags extraction."""
        data = TelnyxRecordingData(sample_webhook_event)
        tags = data.platform_tags

        assert tags["telnyx_recording_id"] == "rec-222"
        assert tags["telnyx_call_session_id"] == "session-xyz789"
        assert tags["telnyx_call_control_id"] == "ctrl-abc123"
        assert tags["telnyx_connection_id"] == "conn-111"
        assert tags["recording_channels"] == "single"


class TestTelnyxVconBuilder:
    """Tests for TelnyxVconBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create builder instance."""
        return TelnyxVconBuilder(
            download_recordings=True,
            recording_format="wav",
            api_key="test-api-key",
        )

    @pytest.fixture
    def sample_recording_data(self):
        """Create sample recording data."""
        return TelnyxRecordingData({
            "data": {
                "event_type": "call.recording.saved",
                "payload": {
                    "recording_id": "rec-123",
                    "from": "+15551234567",
                    "to": "+15559876543",
                    "direction": "incoming",
                    "duration_millis": 30000,
                    "recording_urls": {
                        "wav": "https://api.telnyx.com/recordings/rec-123.wav"
                    },
                    "start_time": "2024-01-15T10:29:30Z"
                }
            }
        })

    def test_adapter_source(self, builder):
        """Test adapter source tag."""
        assert builder.ADAPTER_SOURCE == "telnyx_adapter"

    @patch("adapters.telnyx.builder.requests.get")
    def test_download_recording(self, mock_get, builder, sample_recording_data):
        """Test downloading recording."""
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        # Verify Authorization header
        call_kwargs = mock_get.call_args[1]
        assert "Authorization" in call_kwargs["headers"]
        assert call_kwargs["headers"]["Authorization"] == "Bearer test-api-key"

    @patch("adapters.telnyx.builder.requests.get")
    def test_download_recording_no_api_key(self, mock_get, sample_recording_data):
        """Test downloading without API key."""
        builder = TelnyxVconBuilder(api_key=None)
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        call_kwargs = mock_get.call_args[1]
        assert "Authorization" not in call_kwargs["headers"]

    @patch("adapters.telnyx.builder.requests.get")
    def test_download_recording_no_url(self, mock_get, builder):
        """Test download fails gracefully with no URL."""
        recording_data = TelnyxRecordingData({
            "data": {
                "payload": {
                    "recording_id": "rec-123",
                    "from": "+15551234567",
                    "to": "+15559876543",
                }
            }
        })

        result = builder._download_recording(recording_data)

        assert result is None

    def test_build_vcon_structure(self, sample_recording_data):
        """Test building vCon creates proper structure."""
        builder = TelnyxVconBuilder(download_recordings=False)

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.parties) == 2
        assert len(vcon.dialog) == 1

    def test_build_vcon_no_download(self, sample_recording_data):
        """Test building vCon without downloading."""
        builder = TelnyxVconBuilder(download_recordings=False)

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.dialog) == 1
