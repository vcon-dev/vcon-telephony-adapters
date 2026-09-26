"""Tests for the shared webhook-timestamp helper used by Telnyx and ElevenLabs."""

import pytest

from core.webhook_security import is_timestamp_fresh, parse_tolerance_seconds

FIXED_NOW = 1_705_312_170.0


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    monkeypatch.setattr("core.webhook_security.time.time", lambda: FIXED_NOW)


class TestIsTimestampFresh:
    def test_fresh_timestamp_accepted(self):
        assert is_timestamp_fresh(str(int(FIXED_NOW)), 300) is True

    def test_boundary_exactly_at_tolerance_accepted(self):
        stale_by_exactly_tolerance = str(int(FIXED_NOW) - 300)
        assert is_timestamp_fresh(stale_by_exactly_tolerance, 300) is True

    def test_stale_timestamp_rejected(self):
        one_second_past_tolerance = str(int(FIXED_NOW) - 301)
        assert is_timestamp_fresh(one_second_past_tolerance, 300) is False

    def test_future_timestamp_within_window_accepted(self):
        future_at_boundary = str(int(FIXED_NOW) + 300)
        assert is_timestamp_fresh(future_at_boundary, 300) is True

    def test_future_timestamp_beyond_window_rejected(self):
        future_beyond_tolerance = str(int(FIXED_NOW) + 301)
        assert is_timestamp_fresh(future_beyond_tolerance, 300) is False

    def test_missing_timestamp_rejected(self):
        assert is_timestamp_fresh(None, 300) is False

    def test_non_numeric_timestamp_rejected(self):
        assert is_timestamp_fresh("not-a-timestamp", 300) is False

    def test_empty_string_timestamp_rejected(self):
        assert is_timestamp_fresh("", 300) is False

    def test_numeric_types_accepted(self):
        assert is_timestamp_fresh(int(FIXED_NOW), 300) is True
        assert is_timestamp_fresh(FIXED_NOW, 300) is True


class TestParseToleranceSeconds:
    def test_valid_positive_integer(self):
        assert parse_tolerance_seconds("300", source="X") == 300

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError, match="X"):
            parse_tolerance_seconds("not-a-number", source="X")

    def test_zero_raises(self):
        with pytest.raises(ValueError, match="X"):
            parse_tolerance_seconds("0", source="X")

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="X"):
            parse_tolerance_seconds("-1", source="X")

    def test_float_string_raises(self):
        with pytest.raises(ValueError, match="X"):
            parse_tolerance_seconds("1.5", source="X")
