"""Tests for Asterisk vCon builder."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from adapters.asterisk.builder import AsteriskRecordingData, AsteriskVconBuilder


class TestAsteriskRecordingData:
    """Tests for AsteriskRecordingData class."""

    @pytest.fixture
    def sample_ari_event(self):
        """Sample Asterisk ARI event data."""
        return {
            "recording_name": "rec-abc123",
            "name": "rec-abc123",
            "target_uri": "file:/var/spool/asterisk/recording/rec-abc123.wav",
            "format": "wav",
            "duration": 30,
            "channel_id": "channel-123",
            "caller_id_num": "+15551234567",
            "caller_id_name": "John Doe",
            "connected_line_num": "+15559876543",
            "direction": "inbound",
            "context": "from-external",
            "timestamp": "2024-01-15T10:29:30Z",
            "Uniqueid": "1705312170.123",
            "application": "MixMonitor",
        }

    def test_recording_id(self, sample_ari_event):
        """Test recording_id extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.recording_id == "rec-abc123"

    def test_recording_id_fallback(self):
        """Test recording_id fallback to Uniqueid."""
        data = AsteriskRecordingData({"Uniqueid": "1705312170.456"})
        assert data.recording_id == "1705312170.456"

    def test_from_number(self, sample_ari_event):
        """Test from_number extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.from_number == "+15551234567"

    def test_from_number_fallback(self):
        """Test from_number fallback to CallerIDNum."""
        data = AsteriskRecordingData({"CallerIDNum": "+15550001111"})
        assert data.from_number == "+15550001111"

    def test_to_number(self, sample_ari_event):
        """Test to_number extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.to_number == "+15559876543"

    def test_to_number_fallback(self):
        """Test to_number fallback to extension."""
        data = AsteriskRecordingData({"extension": "100"})
        assert data.to_number == "100"

    def test_direction_explicit(self, sample_ari_event):
        """Test direction when explicitly set."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.direction == "inbound"

    def test_direction_inferred_outbound(self):
        """Test direction inferred from context."""
        data = AsteriskRecordingData({"context": "from-internal"})
        assert data.direction == "outbound"

    def test_direction_inferred_inbound(self):
        """Test direction default to inbound."""
        data = AsteriskRecordingData({})
        assert data.direction == "inbound"

    def test_recording_url(self, sample_ari_event):
        """Test recording_url extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.recording_url == "file:/var/spool/asterisk/recording/rec-abc123.wav"

    def test_recording_file_path(self, sample_ari_event):
        """Test recording_file_path extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.recording_file_path == "/var/spool/asterisk/recording/rec-abc123.wav"

    def test_recording_format(self, sample_ari_event):
        """Test recording_format extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.recording_format == "wav"

    def test_duration_seconds(self, sample_ari_event):
        """Test duration_seconds extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.duration_seconds == 30.0

    def test_duration_seconds_none(self):
        """Test duration_seconds when not provided."""
        data = AsteriskRecordingData({})
        assert data.duration_seconds is None

    def test_start_time_iso(self, sample_ari_event):
        """Test start_time from ISO format."""
        data = AsteriskRecordingData(sample_ari_event)
        assert data.start_time.year == 2024
        assert data.start_time.month == 1
        assert data.start_time.day == 15

    def test_start_time_epoch(self):
        """Test start_time from epoch."""
        data = AsteriskRecordingData({"start_time": "1705312170"})
        expected = datetime.fromtimestamp(1705312170, tz=timezone.utc)
        assert data.start_time == expected

    def test_platform_tags(self, sample_ari_event):
        """Test platform_tags extraction."""
        data = AsteriskRecordingData(sample_ari_event)
        tags = data.platform_tags

        assert tags["asterisk_recording_name"] == "rec-abc123"
        assert tags["asterisk_channel_id"] == "channel-123"
        assert tags["asterisk_unique_id"] == "1705312170.123"
        assert tags["caller_name"] == "John Doe"
        assert tags["asterisk_context"] == "from-external"
        assert tags["asterisk_application"] == "MixMonitor"


class TestAsteriskVconBuilder:
    """Tests for AsteriskVconBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create builder instance."""
        return AsteriskVconBuilder(
            download_recordings=True,
            recording_format="wav",
            recordings_path="/var/spool/asterisk/recording",
            ari_url="http://localhost:8088",
            ari_auth=("asterisk", "secret"),
        )

    @pytest.fixture
    def sample_recording_data(self):
        """Create sample recording data."""
        return AsteriskRecordingData({
            "recording_name": "rec-abc123",
            "caller_id_num": "+15551234567",
            "connected_line_num": "+15559876543",
            "direction": "inbound",
            "duration": 30,
            "timestamp": "2024-01-15T10:29:30Z",
        })

    def test_adapter_source(self, builder):
        """Test adapter source tag."""
        assert builder.ADAPTER_SOURCE == "asterisk_adapter"

    @patch("adapters.asterisk.builder.requests.get")
    def test_download_recording_ari(self, mock_get, builder, sample_recording_data):
        """Test downloading recording via ARI."""
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        call_url = mock_get.call_args[0][0]
        assert "recordings/stored/rec-abc123/file" in call_url

    def test_download_recording_no_url(self, builder):
        """Test download fails gracefully with no URL."""
        recording_data = AsteriskRecordingData({
            "recording_name": "rec-abc123",
            "caller_id_num": "+15551234567",
            "connected_line_num": "+15559876543",
            "direction": "inbound",
        })

        result = builder._download_recording(recording_data)

        assert result is None

    @patch("adapters.asterisk.builder.requests.get")
    def test_build_vcon(self, mock_get, builder, sample_recording_data):
        """Test building vCon from recording data."""
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.parties) == 2
        assert len(vcon.dialog) == 1

    def test_build_vcon_no_download(self, sample_recording_data):
        """Test building vCon without downloading."""
        builder = AsteriskVconBuilder(download_recordings=False)

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
