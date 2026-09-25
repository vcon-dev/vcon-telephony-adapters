"""Tests for the SignalWire poller: dedupe/watermark safety, API error
handling, and the failed-POST-not-marked-processed fix (CON-703).

The standalone rescue/cursor-multi-mode-wip draft of this poller had two bugs
an audit flagged: it marked a call processed even when the POST to the
conserver failed, and it advanced its poll watermark to now() every cycle
regardless of outcome, so a failed call would fall out of the fetch window on
the very next poll and never be retried. These tests pin the fixed behavior.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from adapters.signalwire.config import SignalWireConfig
from adapters.signalwire.poller import SignalWirePoller
from core.tracker import StateTracker


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSERVER_URL", "https://conserver.example.com/vcon")
    monkeypatch.setenv("SIGNALWIRE_PROJECT_ID", "PJ123")
    monkeypatch.setenv("SIGNALWIRE_AUTH_TOKEN", "TOKEN123")
    monkeypatch.setenv("SIGNALWIRE_SPACE_URL", "https://example.signalwire.com")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    return SignalWireConfig()


def make_poller(config, *, builder=None, poster=None, tracker=None):
    builder = builder or MagicMock()
    poster = poster or MagicMock()
    tracker = tracker or StateTracker(config.state_file)
    return SignalWirePoller(config, builder=builder, poster=poster, tracker=tracker)


class TestSignalWirePollerSuccessPath:
    def test_successful_post_marks_processed_and_advances_watermark(self, config):
        builder = MagicMock()
        vcon = MagicMock()
        vcon.uuid = "vcon-1"
        builder.build.return_value = vcon

        poster = MagicMock()
        poster.post.return_value = True

        poller = make_poller(config, builder=builder, poster=poster)
        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock(return_value={"sid": "CA1"})
        poller._fetch_transcriptions = MagicMock(return_value=[])

        shipped = poller.process()

        assert shipped == 1
        assert poller.tracker.is_processed("CA1")
        assert poller.tracker.get_vcon_uuid("CA1") == "vcon-1"

    def test_already_processed_calls_are_skipped(self, config):
        builder = MagicMock()
        poster = MagicMock()
        poller = make_poller(config, builder=builder, poster=poster)
        poller.tracker.mark_processed("CA1", "vcon-old", status="success")

        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock()

        shipped = poller.process()

        assert shipped == 0
        builder.build.assert_not_called()
        poller._fetch_call_meta.assert_not_called()


class TestSignalWirePollerFailurePath:
    def test_failed_post_does_not_mark_processed(self, config):
        """CON-703: a failed POST must not be recorded as processed."""
        builder = MagicMock()
        vcon = MagicMock()
        vcon.uuid = "vcon-1"
        builder.build.return_value = vcon

        poster = MagicMock()
        poster.post.return_value = False  # conserver rejected/unreachable

        poller = make_poller(config, builder=builder, poster=poster)
        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock(return_value={"sid": "CA1"})
        poller._fetch_transcriptions = MagicMock(return_value=[])

        shipped = poller.process()

        assert shipped == 0
        assert not poller.tracker.is_processed("CA1")

    def test_failed_post_keeps_watermark_from_advancing_past_it(self, config):
        """A failed call must stay inside the next poll's fetch window."""
        builder = MagicMock()
        vcon = MagicMock()
        vcon.uuid = "vcon-1"
        builder.build.return_value = vcon

        poster = MagicMock()
        poster.post.return_value = False

        poller = make_poller(config, builder=builder, poster=poster)
        since = datetime.now(UTC) - timedelta(hours=1)
        poller._load_watermark = MagicMock(return_value=since)
        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock(return_value={"sid": "CA1"})
        poller._fetch_transcriptions = MagicMock(return_value=[])

        saved = {}
        poller._save_watermark = lambda wm: saved.setdefault("watermark", wm)

        poller.process()

        # Watermark must not have advanced past the original `since`, or the
        # failed call would drop out of the next poll's DateCreated> window.
        assert saved["watermark"] <= since

    def test_build_failure_does_not_mark_processed(self, config):
        builder = MagicMock()
        builder.build.return_value = None  # build failed

        poster = MagicMock()
        poller = make_poller(config, builder=builder, poster=poster)
        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock(return_value={"sid": "CA1"})
        poller._fetch_transcriptions = MagicMock(return_value=[])

        shipped = poller.process()

        assert shipped == 0
        assert not poller.tracker.is_processed("CA1")
        poster.post.assert_not_called()

    def test_call_meta_fetch_failure_does_not_mark_processed(self, config):
        builder = MagicMock()
        poster = MagicMock()
        poller = make_poller(config, builder=builder, poster=poster)
        poller._fetch_recordings_since = MagicMock(return_value=[{"call_sid": "CA1", "sid": "RE1"}])
        poller._fetch_call_meta = MagicMock(return_value=None)  # API error / 404

        shipped = poller.process()

        assert shipped == 0
        assert not poller.tracker.is_processed("CA1")
        builder.build.assert_not_called()

    def test_recordings_fetch_api_error_returns_empty_without_raising(self, config):
        """requests raising should not crash the poll cycle."""
        import requests

        poller = make_poller(config)

        class BoomSession:
            def get(self, *args, **kwargs):
                raise requests.RequestException("boom")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "adapters.signalwire.poller.requests.get",
                lambda *a, **k: (_ for _ in ()).throw(requests.RequestException("boom")),
            )
            recordings = poller._fetch_recordings_since(datetime.now(UTC))

        assert recordings == []


class TestSignalWirePollerWatermarkPersistence:
    def test_watermark_round_trips_through_file(self, config, tmp_path):
        poller = make_poller(config)
        wm = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
        poller._save_watermark(wm)

        poller2 = make_poller(config)
        loaded = poller2._load_watermark()
        assert loaded == wm

    def test_missing_watermark_file_falls_back_to_poll_interval(self, config):
        poller = make_poller(config)
        before = datetime.now(UTC) - timedelta(seconds=config.poll_interval_seconds)
        loaded = poller._load_watermark()
        # Allow a couple seconds of test execution slack.
        assert abs((loaded - before).total_seconds()) < 5

    def test_corrupt_watermark_file_falls_back_safely(self, config):
        poller = make_poller(config)
        poller._watermark_file.write_text("not json")
        # Must not raise.
        loaded = poller._load_watermark()
        assert loaded is not None
