"""Comprehensive tests for state tracker module."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from twilio_adapter.tracker import StateTracker


class TestStateTrackerInit:
    """Tests for StateTracker initialization."""

    def test_creates_empty_state_for_new_file(self, temp_state_file):
        """New tracker starts with empty state."""
        tracker = StateTracker(temp_state_file)

        assert tracker.state == {}

    def test_creates_state_file_on_first_write(self):
        """State file is created on first mark_processed call."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)  # Delete it so tracker creates it

        try:
            tracker = StateTracker(path)
            assert not Path(path).exists()

            tracker.mark_processed("RE123", "vcon-123")
            assert Path(path).exists()
        finally:
            if Path(path).exists():
                Path(path).unlink()

    def test_loads_existing_state(self, temp_state_file):
        """Loads existing state from file."""
        # Pre-populate state file
        existing_state = {
            "RE123": {
                "vcon_uuid": "vcon-123",
                "timestamp": "2025-01-21T10:00:00+00:00",
                "status": "success"
            }
        }
        with open(temp_state_file, 'w') as f:
            json.dump(existing_state, f)

        tracker = StateTracker(temp_state_file)

        assert tracker.is_processed("RE123")
        assert tracker.get_vcon_uuid("RE123") == "vcon-123"


class TestStateTrackerIsProcessed:
    """Tests for is_processed method."""

    def test_returns_false_for_new_recording(self, tracker):
        """Returns False for unprocessed recording."""
        assert tracker.is_processed("RE_NEW") is False

    def test_returns_true_after_marking(self, tracker):
        """Returns True after marking as processed."""
        tracker.mark_processed("RE123", "vcon-123")
        assert tracker.is_processed("RE123") is True

    def test_returns_true_for_preloaded(self, preloaded_tracker):
        """Returns True for pre-existing processed recordings."""
        assert preloaded_tracker.is_processed("RE_EXISTING_1") is True
        assert preloaded_tracker.is_processed("RE_EXISTING_2") is True

    def test_case_sensitive(self, tracker):
        """Recording IDs are case sensitive."""
        tracker.mark_processed("RE123", "vcon-123")

        assert tracker.is_processed("RE123") is True
        assert tracker.is_processed("re123") is False
        assert tracker.is_processed("Re123") is False


class TestStateTrackerMarkProcessed:
    """Tests for mark_processed method."""

    def test_marks_with_minimal_args(self, tracker):
        """Can mark with just recording_sid and vcon_uuid."""
        tracker.mark_processed("RE123", "vcon-123")

        assert tracker.is_processed("RE123")
        assert tracker.get_vcon_uuid("RE123") == "vcon-123"
        assert tracker.get_processing_status("RE123") == "success"  # Default

    def test_marks_with_all_args(self, tracker):
        """Can mark with all optional arguments."""
        tracker.mark_processed(
            "RE123",
            "vcon-123",
            status="success",
            call_sid="CA456",
            from_number="+15551234567",
            to_number="+15559876543"
        )

        assert tracker.is_processed("RE123")

    def test_stores_custom_status(self, tracker):
        """Custom status is stored."""
        tracker.mark_processed("RE123", "vcon-123", status="failed")

        assert tracker.get_processing_status("RE123") == "failed"

    def test_stores_timestamp(self, tracker):
        """Timestamp is stored."""
        before = datetime.now(timezone.utc)
        tracker.mark_processed("RE123", "vcon-123")
        after = datetime.now(timezone.utc)

        entry = tracker.state["RE123"]
        timestamp = datetime.fromisoformat(entry["timestamp"])

        assert before <= timestamp <= after

    def test_updates_existing_entry(self, tracker):
        """Can update an existing entry."""
        tracker.mark_processed("RE123", "vcon-123", status="processing")
        tracker.mark_processed("RE123", "vcon-123", status="success")

        assert tracker.get_processing_status("RE123") == "success"

    def test_stores_call_sid(self, tracker, temp_state_file):
        """Call SID is stored when provided."""
        tracker.mark_processed("RE123", "vcon-123", call_sid="CA456")

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        assert data["RE123"]["call_sid"] == "CA456"

    def test_stores_phone_numbers(self, tracker, temp_state_file):
        """Phone numbers are stored when provided."""
        tracker.mark_processed(
            "RE123", "vcon-123",
            from_number="+15551234567",
            to_number="+15559876543"
        )

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        assert data["RE123"]["from"] == "+15551234567"
        assert data["RE123"]["to"] == "+15559876543"


class TestStateTrackerGetVconUuid:
    """Tests for get_vcon_uuid method."""

    def test_returns_uuid_for_processed(self, tracker):
        """Returns UUID for processed recording."""
        tracker.mark_processed("RE123", "vcon-abc-123")

        assert tracker.get_vcon_uuid("RE123") == "vcon-abc-123"

    def test_returns_none_for_unprocessed(self, tracker):
        """Returns None for unprocessed recording."""
        assert tracker.get_vcon_uuid("RE_UNKNOWN") is None

    def test_returns_correct_uuid_for_multiple(self, tracker):
        """Returns correct UUID when multiple recordings exist."""
        tracker.mark_processed("RE001", "vcon-001")
        tracker.mark_processed("RE002", "vcon-002")
        tracker.mark_processed("RE003", "vcon-003")

        assert tracker.get_vcon_uuid("RE001") == "vcon-001"
        assert tracker.get_vcon_uuid("RE002") == "vcon-002"
        assert tracker.get_vcon_uuid("RE003") == "vcon-003"


class TestStateTrackerGetProcessingStatus:
    """Tests for get_processing_status method."""

    def test_returns_status_for_processed(self, tracker):
        """Returns status for processed recording."""
        tracker.mark_processed("RE123", "vcon-123", status="success")

        assert tracker.get_processing_status("RE123") == "success"

    def test_returns_none_for_unprocessed(self, tracker):
        """Returns None for unprocessed recording."""
        assert tracker.get_processing_status("RE_UNKNOWN") is None

    def test_returns_different_statuses(self, preloaded_tracker):
        """Returns correct status for different recordings."""
        assert preloaded_tracker.get_processing_status("RE_EXISTING_1") == "success"
        assert preloaded_tracker.get_processing_status("RE_FAILED") == "failed"


class TestStateTrackerPersistence:
    """Tests for state persistence."""

    def test_state_persists_across_instances(self, temp_state_file):
        """State persists when creating new tracker instance."""
        tracker1 = StateTracker(temp_state_file)
        tracker1.mark_processed("RE123", "vcon-123")

        tracker2 = StateTracker(temp_state_file)
        assert tracker2.is_processed("RE123")
        assert tracker2.get_vcon_uuid("RE123") == "vcon-123"

    def test_state_persists_multiple_recordings(self, temp_state_file):
        """Multiple recordings persist across instances."""
        tracker1 = StateTracker(temp_state_file)
        tracker1.mark_processed("RE001", "vcon-001")
        tracker1.mark_processed("RE002", "vcon-002")
        tracker1.mark_processed("RE003", "vcon-003")

        tracker2 = StateTracker(temp_state_file)
        assert tracker2.is_processed("RE001")
        assert tracker2.is_processed("RE002")
        assert tracker2.is_processed("RE003")

    def test_state_file_is_valid_json(self, tracker, temp_state_file):
        """State file contains valid JSON."""
        tracker.mark_processed("RE123", "vcon-123")

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert "RE123" in data

    def test_state_file_is_readable_json(self, tracker, temp_state_file):
        """State file is human-readable (indented)."""
        tracker.mark_processed("RE123", "vcon-123")

        with open(temp_state_file, 'r') as f:
            content = f.read()

        # Should have indentation (newlines after braces)
        assert "\n" in content


class TestStateTrackerErrorHandling:
    """Tests for error handling."""

    def test_handles_corrupted_json(self):
        """Handles corrupted JSON in state file."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        with open(path, 'w') as f:
            f.write("not valid json {{{")

        try:
            tracker = StateTracker(path)

            # Should start with empty state
            assert tracker.state == {}
            assert not tracker.is_processed("RE123")

            # Should be able to add new entries
            tracker.mark_processed("RE456", "vcon-456")
            assert tracker.is_processed("RE456")
        finally:
            Path(path).unlink()

    def test_handles_empty_file(self):
        """Handles empty state file."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        # File exists but is empty
        with open(path, 'w') as f:
            pass

        try:
            tracker = StateTracker(path)
            assert tracker.state == {}
        finally:
            Path(path).unlink()

    def test_handles_non_dict_json(self):
        """Handles JSON that isn't a dict."""
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        with open(path, 'w') as f:
            json.dump(["list", "not", "dict"], f)

        try:
            tracker = StateTracker(path)
            # Should handle gracefully - likely treats as corrupted
            # and starts fresh
        finally:
            Path(path).unlink()

    def test_handles_missing_directory(self):
        """Handles state file in non-existent directory."""
        path = "/nonexistent/directory/state.json"

        # Should not raise during init (file doesn't exist yet)
        tracker = StateTracker(path)
        assert tracker.state == {}

        # Writing will fail, but should be handled gracefully
        # (logged but not raised)
        tracker.mark_processed("RE123", "vcon-123")


class TestStateTrackerConcurrency:
    """Tests for concurrent access scenarios."""

    def test_multiple_marks_same_recording(self, tracker):
        """Multiple marks for same recording updates state."""
        tracker.mark_processed("RE123", "vcon-v1", status="processing")
        tracker.mark_processed("RE123", "vcon-v1", status="success")

        assert tracker.get_processing_status("RE123") == "success"

    def test_rapid_successive_marks(self, tracker):
        """Rapid successive marks all persist."""
        for i in range(100):
            tracker.mark_processed(f"RE{i:03d}", f"vcon-{i:03d}")

        for i in range(100):
            assert tracker.is_processed(f"RE{i:03d}")
            assert tracker.get_vcon_uuid(f"RE{i:03d}") == f"vcon-{i:03d}"


class TestStateTrackerStateFileFormat:
    """Tests for state file format."""

    def test_complete_entry_format(self, tracker, temp_state_file):
        """Complete entry has all expected fields."""
        tracker.mark_processed(
            "RE123",
            "vcon-abc-123-def-456",
            status="success",
            call_sid="CA789",
            from_number="+15551234567",
            to_number="+15559876543"
        )

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        entry = data["RE123"]
        assert entry["vcon_uuid"] == "vcon-abc-123-def-456"
        assert entry["status"] == "success"
        assert entry["call_sid"] == "CA789"
        assert entry["from"] == "+15551234567"
        assert entry["to"] == "+15559876543"
        assert "timestamp" in entry

    def test_minimal_entry_format(self, tracker, temp_state_file):
        """Minimal entry has required fields."""
        tracker.mark_processed("RE123", "vcon-123")

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        entry = data["RE123"]
        assert "vcon_uuid" in entry
        assert "status" in entry
        assert "timestamp" in entry

    def test_timestamp_is_iso_format(self, tracker, temp_state_file):
        """Timestamp is in ISO format."""
        tracker.mark_processed("RE123", "vcon-123")

        with open(temp_state_file, 'r') as f:
            data = json.load(f)

        timestamp = data["RE123"]["timestamp"]
        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(timestamp)
        assert parsed is not None


class TestStateTrackerEdgeCases:
    """Tests for edge cases."""

    def test_empty_recording_sid(self, tracker):
        """Handles empty recording SID."""
        tracker.mark_processed("", "vcon-123")
        assert tracker.is_processed("")

    def test_special_characters_in_recording_sid(self, tracker):
        """Handles special characters in recording SID."""
        special_sids = [
            "RE-with-dashes",
            "RE_with_underscores",
            "RE.with.dots",
            "RE:with:colons",
        ]

        for sid in special_sids:
            tracker.mark_processed(sid, f"vcon-{sid}")
            assert tracker.is_processed(sid)

    def test_unicode_in_vcon_uuid(self, tracker):
        """Handles unicode in vCon UUID."""
        tracker.mark_processed("RE123", "vcon-unicöde-测试")
        assert tracker.get_vcon_uuid("RE123") == "vcon-unicöde-测试"

    def test_very_long_recording_sid(self, tracker):
        """Handles very long recording SID."""
        long_sid = "RE" + "x" * 1000
        tracker.mark_processed(long_sid, "vcon-123")
        assert tracker.is_processed(long_sid)
