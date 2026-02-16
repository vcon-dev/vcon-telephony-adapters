"""Tests for FreeSWITCH vCon builder."""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from adapters.freeswitch.builder import FreeSwitchRecordingData, FreeSwitchVconBuilder


class TestFreeSwitchRecordingData:
    """Tests for FreeSwitchRecordingData class."""

    @pytest.fixture
    def sample_event_data(self):
        """Sample FreeSWITCH event data."""
        return {
            "uuid": "abc123-def456",
            "caller_id_number": "+15551234567",
            "caller_id_name": "John Doe",
            "destination_number": "+15559876543",
            "direction": "inbound",
            "recording_file": "/var/lib/freeswitch/recordings/abc123-def456.wav",
            "recording_url": "https://fs.example.com/recordings/abc123-def456.wav",
            "record_seconds": "30",
            "start_epoch": "1705312170",
            "accountcode": "ACC001",
            "context": "default",
            "sip_user_agent": "Ooma/1.0",
        }

    def test_recording_id(self, sample_event_data):
        """Test recording_id extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.recording_id == "abc123-def456"

    def test_recording_id_fallback(self):
        """Test recording_id fallback to call_uuid."""
        data = FreeSwitchRecordingData({"call_uuid": "xyz789"})
        assert data.recording_id == "xyz789"

    def test_from_number(self, sample_event_data):
        """Test from_number extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.from_number == "+15551234567"

    def test_from_number_fallback(self):
        """Test from_number fallback to Caller-Caller-ID-Number."""
        data = FreeSwitchRecordingData({"Caller-Caller-ID-Number": "+15550001111"})
        assert data.from_number == "+15550001111"

    def test_to_number(self, sample_event_data):
        """Test to_number extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.to_number == "+15559876543"

    def test_direction(self, sample_event_data):
        """Test direction extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.direction == "inbound"

    def test_direction_outbound(self):
        """Test outbound direction."""
        data = FreeSwitchRecordingData({"direction": "Outbound"})
        assert data.direction == "outbound"

    def test_recording_url(self, sample_event_data):
        """Test recording_url extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.recording_url == "https://fs.example.com/recordings/abc123-def456.wav"

    def test_recording_file_path(self, sample_event_data):
        """Test recording_file_path extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.recording_file_path == "/var/lib/freeswitch/recordings/abc123-def456.wav"

    def test_duration_seconds(self, sample_event_data):
        """Test duration_seconds extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        assert data.duration_seconds == 30.0

    def test_duration_seconds_none(self):
        """Test duration_seconds when not provided."""
        data = FreeSwitchRecordingData({})
        assert data.duration_seconds is None

    def test_start_time_epoch(self, sample_event_data):
        """Test start_time from epoch."""
        data = FreeSwitchRecordingData(sample_event_data)
        expected = datetime.fromtimestamp(1705312170, tz=timezone.utc)
        assert data.start_time == expected

    def test_start_time_microseconds(self):
        """Test start_time with microseconds epoch."""
        data = FreeSwitchRecordingData({
            "Caller-Channel-Created-Time": "1705312170000000"
        })
        expected = datetime.fromtimestamp(1705312170, tz=timezone.utc)
        assert data.start_time == expected

    def test_start_time_iso_format(self):
        """Test start_time from ISO format."""
        data = FreeSwitchRecordingData({
            "start_time": "2024-01-15T10:29:30Z"
        })
        assert data.start_time.year == 2024
        assert data.start_time.month == 1
        assert data.start_time.day == 15

    def test_platform_tags(self, sample_event_data):
        """Test platform_tags extraction."""
        data = FreeSwitchRecordingData(sample_event_data)
        tags = data.platform_tags

        assert tags["freeswitch_uuid"] == "abc123-def456"
        assert tags["caller_name"] == "John Doe"
        assert tags["account_code"] == "ACC001"
        assert tags["freeswitch_context"] == "default"
        assert tags["sip_user_agent"] == "Ooma/1.0"


class TestFreeSwitchVconBuilder:
    """Tests for FreeSwitchVconBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create builder instance."""
        return FreeSwitchVconBuilder(
            download_recordings=True,
            recording_format="wav",
            recordings_path="/var/lib/freeswitch/recordings",
            recordings_url_base="https://fs.example.com/recordings",
        )

    @pytest.fixture
    def sample_recording_data(self):
        """Create sample recording data."""
        return FreeSwitchRecordingData({
            "uuid": "abc123",
            "caller_id_number": "+15551234567",
            "destination_number": "+15559876543",
            "direction": "inbound",
            "recording_url": "https://fs.example.com/recordings/abc123.wav",
            "record_seconds": "30",
            "start_epoch": "1705312170",
        })

    def test_adapter_source(self, builder):
        """Test adapter source tag."""
        assert builder.ADAPTER_SOURCE == "freeswitch_adapter"

    @patch("adapters.freeswitch.builder.requests.get")
    def test_download_recording_http(self, mock_get, builder, sample_recording_data):
        """Test downloading recording via HTTP."""
        mock_response = MagicMock()
        mock_response.content = b"fake audio data"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = builder._download_recording(sample_recording_data)

        assert result == b"fake audio data"
        mock_get.assert_called_once()

    @patch("builtins.open", create=True)
    @patch("os.path.isabs", return_value=True)
    @patch("adapters.freeswitch.builder.requests.get")
    def test_download_recording_local_file(
        self, mock_get, mock_isabs, mock_open, builder
    ):
        """Test reading recording from local file."""
        mock_get.side_effect = Exception("Network error")
        mock_file = MagicMock()
        mock_file.read.return_value = b"local audio data"
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_open.return_value = mock_file

        recording_data = FreeSwitchRecordingData({
            "uuid": "abc123",
            "recording_file": "/var/lib/freeswitch/recordings/abc123.wav",
            "caller_id_number": "+15551234567",
            "destination_number": "+15559876543",
            "direction": "inbound",
        })

        result = builder._download_recording(recording_data)

        assert result == b"local audio data"

    def test_build_vcon_no_download_check(self, sample_recording_data):
        """Test building vCon creates proper structure."""
        builder = FreeSwitchVconBuilder(
            download_recordings=False,
            recording_format="wav",
        )

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.parties) == 2
        assert len(vcon.dialog) == 1

    def test_build_vcon_no_download(self, sample_recording_data):
        """Test building vCon without downloading."""
        builder = FreeSwitchVconBuilder(
            download_recordings=False,
            recording_format="wav",
        )

        vcon = builder.build(sample_recording_data)

        assert vcon is not None
        assert len(vcon.dialog) == 1
        # Should have URL reference instead of embedded audio
        assert "url" in str(vcon.dialog[0])
