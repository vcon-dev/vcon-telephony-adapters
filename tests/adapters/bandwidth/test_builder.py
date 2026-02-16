"""Tests for Bandwidth vCon builder."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from adapters.bandwidth.builder import BandwidthRecordingData, BandwidthVconBuilder


class TestBandwidthRecordingData:
    """Tests for BandwidthRecordingData class."""

    @pytest.fixture
    def sample_webhook_event(self):
        """Sample Bandwidth recording complete event."""
        return {
            "eventType": "recordingComplete",
            "accountId": "123456",
            "applicationId": "app-789",
            "callId": "c-call-abc123",
            "callUrl": "https://voice.bandwidth.com/api/v2/accounts/123456/calls/c-call-abc123",
            "recordingId": "r-rec-def456",
            "mediaUrl": "https://voice.bandwidth.com/api/v2/accounts/123456/calls/c-call-abc123/recordings/r-rec-def456/media",
            "direction": "inbound",
            "from": "+15551234567",
            "to": "+15559876543",
            "startTime": "2024-01-15T10:29:30.000Z",
            "endTime": "2024-01-15T10:30:00.000Z",
            "duration": "PT30S",
            "channels": 1,
            "fileFormat": "wav",
            "status": "complete"
        }

    def test_recording_id(self, sample_webhook_event):
        """Test recording_id extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.recording_id == "r-rec-def456"

    def test_call_id(self, sample_webhook_event):
        """Test call_id extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.call_id == "c-call-abc123"

    def test_from_number(self, sample_webhook_event):
        """Test from_number extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.from_number == "+15551234567"

    def test_to_number(self, sample_webhook_event):
        """Test to_number extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.to_number == "+15559876543"

    def test_direction(self, sample_webhook_event):
        """Test direction extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.direction == "inbound"

    def test_direction_outbound(self):
        """Test outbound direction."""
        data = BandwidthRecordingData({"direction": "Outbound"})
        assert data.direction == "outbound"

    def test_recording_url(self, sample_webhook_event):
        """Test recording_url extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert "media" in data.recording_url

    def test_file_format(self, sample_webhook_event):
        """Test file_format extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.file_format == "wav"

    def test_duration_seconds_pt30s(self, sample_webhook_event):
        """Test duration_seconds parsing PT30S format."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.duration_seconds == 30.0

    def test_duration_seconds_pt1m30s(self):
        """Test duration_seconds parsing PT1M30S format."""
        data = BandwidthRecordingData({"duration": "PT1M30S"})
        assert data.duration_seconds == 90.0

    def test_duration_seconds_pt1h30m45s(self):
        """Test duration_seconds parsing PT1H30M45S format."""
        data = BandwidthRecordingData({"duration": "PT1H30M45S"})
        assert data.duration_seconds == 5445.0

    def test_duration_seconds_none(self):
        """Test duration_seconds when not provided."""
        data = BandwidthRecordingData({})
        assert data.duration_seconds is None

    def test_start_time(self, sample_webhook_event):
        """Test start_time extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.start_time.year == 2024
        assert data.start_time.month == 1
        assert data.start_time.day == 15
        assert data.start_time.hour == 10
        assert data.start_time.minute == 29

    def test_end_time(self, sample_webhook_event):
        """Test end_time extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        assert data.end_time is not None
        assert data.end_time.minute == 30

    def test_platform_tags(self, sample_webhook_event):
        """Test platform_tags extraction."""
        data = BandwidthRecordingData(sample_webhook_event)
        tags = data.platform_tags

        assert tags["bandwidth_recording_id"] == "r-rec-def456"
        assert tags["bandwidth_call_id"] == "c-call-abc123"
        assert tags["bandwidth_account_id"] == "123456"
        assert tags["bandwidth_application_id"] == "app-789"
        assert tags["recording_channels"] == "1"


class TestBandwidthVconBuilder:
    """Tests for BandwidthVconBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create builder instance."""
        return BandwidthVconBuilder(
            download_recordings=True,
            recording_format="wav",
            api_auth=("user", "pass"),
        )

    @pytest.fixture
    def sample_recording_data(self):
        """Create sample recording data."""
        return BandwidthRecordingData({
            "eventType": "recordingComplete",
            "recordingId": "r-rec-123",
            "callId": "c-call-456",
            "from": "+15551234567",
            "to": "+15559876543",
            "direction": "inbound",
            "duration": "PT30S",
            "mediaUrl": "https://voice.bandwidth.com/recordings/r-rec-123/media",
            "startTime": "2024-01-15T10:29:30.000Z",
        })

    def test_adapter_source(self, builder):
        """Test adapter source tag."""
        assert builder.ADAPTER_SOURCE == "bandwidth_adapter"

    @patch("adapters.bandwidth.builder.requests.get")
    def test_download_recording(self, mock_get, builder, sample_recording_data):
        """Test downloading recording."""
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        # Verify auth is passed
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["auth"] is not None

    @patch("adapters.bandwidth.builder.requests.get")
    def test_download_recording_no_auth(self, mock_get, sample_recording_data):
        """Test downloading without auth."""
        builder = BandwidthVconBuilder(api_auth=None)
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["auth"] is None

    @patch("adapters.bandwidth.builder.requests.get")
    def test_download_recording_no_url(self, mock_get, builder):
        """Test download fails gracefully with no URL."""
        recording_data = BandwidthRecordingData({
            "recordingId": "r-rec-123",
            "from": "+15551234567",
            "to": "+15559876543",
        })

        result = builder._download_recording(recording_data)

        assert result is None

    def test_build_vcon_structure(self, sample_recording_data):
        """Test building vCon creates proper structure."""
        builder = BandwidthVconBuilder(download_recordings=False)

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.parties) == 2
        assert len(vcon.dialog) == 1

    def test_build_vcon_no_download(self, sample_recording_data):
        """Test building vCon without downloading."""
        builder = BandwidthVconBuilder(download_recordings=False)

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.dialog) == 1
