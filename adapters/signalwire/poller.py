"""SignalWire poller: fetches new recordings on a schedule and ships vCons.

SignalWire doesn't push a webhook for "a recording now exists" the way
Twilio does, so this polls the Compatibility API on an interval.

Dedupe/watermark safety (CON-703): the standalone `rescue/cursor-multi-mode-wip`
draft of this poller had two related bugs an audit flagged:

1. It marked a call's recordings as processed (`processed[call_sid] = ...`)
   unconditionally after attempting to post, including when the POST to the
   conserver failed — so a failed delivery was silently dropped forever
   instead of being retried.
2. Its poll watermark (`last_check_time`) advanced to `now()` every cycle
   regardless of outcome, so even if (1) were fixed, a failed call's
   recording would fall out of the `DateCreated>` query window on the very
   next poll and never be retried at all.

This version fixes both: `core.tracker.StateTracker` only marks a call
processed once `HttpPoster.post()` returns True (a build failure or a post
failure leaves it unmarked, logged as a warning, so the next cycle retries
it), and the watermark persisted to `<state_file>.watermark` only advances
past calls that were *successfully* shipped or exhausted — it never jumps
ahead of a call still pending, so that call stays inside the poll window
until it either succeeds or is dropped by the operator's own investigation.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from core.poster import HttpPoster
from core.tracker import StateTracker

from .builder import SignalWireRecordingData, SignalWireVconBuilder
from .config import SignalWireConfig

logger = logging.getLogger(__name__)


class SignalWirePoller:
    """Long-running poller that fetches new SignalWire recordings and ships vCons."""

    def __init__(
        self,
        config: SignalWireConfig,
        builder: SignalWireVconBuilder | None = None,
        poster: HttpPoster | None = None,
        tracker: StateTracker | None = None,
    ):
        self.config = config
        self.builder = builder or SignalWireVconBuilder(
            download_recordings=config.download_recordings,
            api_auth=config.api_auth(),
            publisher=config.build_publisher(),
            lawful_basis=config.build_lawful_basis(),
        )
        self.poster = poster or HttpPoster(
            config.conserver_url, config.get_headers(), config.ingress_lists
        )
        self.tracker = tracker or StateTracker(config.state_file)
        self._watermark_file = Path(f"{config.state_file}.watermark")
        self._running = True

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Poll until SIGINT / SIGTERM."""
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        while self._running:
            try:
                self.process()
            except Exception:
                logger.exception("SignalWire poll cycle failed")
            self._sleep(self.config.poll_interval_seconds)

    # ------------------------------------------------------------------
    # One poll cycle (extracted so tests can drive a single iteration)
    # ------------------------------------------------------------------
    def process(self) -> int:
        """Fetch + ship vCons for calls not yet processed. Returns count shipped."""
        since = self._load_watermark()
        recordings = self._fetch_recordings_since(since)

        by_call: dict[str, list[dict[str, Any]]] = {}
        for r in recordings:
            sid = r.get("call_sid")
            if not sid or self.tracker.is_processed(sid):
                continue
            by_call.setdefault(sid, []).append(r)

        shipped = 0
        earliest_unresolved: datetime | None = None
        now = datetime.now(UTC)

        for call_sid, group in by_call.items():
            call_meta = self._fetch_call_meta(call_sid)
            if not call_meta:
                earliest_unresolved = self._earlier(earliest_unresolved, since)
                continue

            transcripts = {r.get("sid", ""): self._fetch_transcriptions(r) for r in group}
            data = SignalWireRecordingData(
                call_meta=call_meta,
                recordings=group,
                transcriptions_by_recording_sid=transcripts,
            )
            vcon = self.builder.build(data)
            if vcon is None:
                logger.warning(f"Failed to build vCon for SignalWire call {call_sid}; will retry")
                earliest_unresolved = self._earlier(earliest_unresolved, since)
                continue

            if self.poster.post(vcon):
                self.tracker.mark_processed(call_sid, vcon.uuid, status="success")
                shipped += 1
            else:
                logger.warning(
                    f"Failed to post vCon {vcon.uuid} for SignalWire call {call_sid}; "
                    "not marking processed, will retry"
                )
                earliest_unresolved = self._earlier(earliest_unresolved, since)

        new_watermark = earliest_unresolved if earliest_unresolved is not None else now
        self._save_watermark(new_watermark)
        return shipped

    # ------------------------------------------------------------------
    # SignalWire API
    # ------------------------------------------------------------------
    def _fetch_recordings_since(self, since: datetime) -> list[dict]:
        url = f"{self.config.api_base()}/Recordings.json"
        params = {"DateCreated>": since.strftime("%Y-%m-%d")}
        try:
            r = requests.get(url, auth=self.config.api_auth(), params=params, timeout=30)
            r.raise_for_status()
            return r.json().get("recordings", [])
        except requests.RequestException as e:
            logger.error(f"SignalWire recordings fetch failed: {e}")
            return []

    def _fetch_call_meta(self, call_sid: str) -> dict | None:
        url = f"{self.config.api_base()}/Calls/{call_sid}.json"
        try:
            r = requests.get(url, auth=self.config.api_auth(), timeout=30)
        except requests.RequestException as e:
            logger.warning(f"SignalWire call meta fetch failed for {call_sid}: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"SignalWire call meta fetch failed for {call_sid}: {r.status_code}")
            return None
        return r.json()

    def _fetch_transcriptions(self, recording: dict) -> list[dict]:
        sub = recording.get("subresource_uris") or {}
        if "transcriptions" not in sub:
            return []
        url = f"{self.config.space_url.rstrip('/')}{sub['transcriptions']}"
        try:
            r = requests.get(url, auth=self.config.api_auth(), timeout=30)
        except requests.RequestException as e:
            logger.warning(f"SignalWire transcription fetch failed: {e}")
            return []
        if r.status_code != 200:
            return []
        return r.json().get("transcriptions", [])

    # ------------------------------------------------------------------
    # Watermark persistence
    # ------------------------------------------------------------------
    def _load_watermark(self) -> datetime:
        if self._watermark_file.exists():
            try:
                raw = json.loads(self._watermark_file.read_text())
                return datetime.fromisoformat(raw["watermark"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
                logger.warning(f"Could not read SignalWire watermark file: {e}; starting fresh")
        return datetime.now(UTC) - timedelta(seconds=self.config.poll_interval_seconds)

    def _save_watermark(self, watermark: datetime) -> None:
        try:
            self._watermark_file.write_text(json.dumps({"watermark": watermark.isoformat()}))
        except OSError as e:
            logger.error(f"Could not save SignalWire watermark file: {e}")

    @staticmethod
    def _earlier(a: datetime | None, b: datetime) -> datetime:
        if a is None or b < a:
            return b
        return a

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _stop(self, *_args) -> None:
        logger.info("Received stop signal")
        self._running = False

    def _sleep(self, seconds: int) -> None:
        # Broken into 1s ticks so SIGTERM is responsive.
        for _ in range(seconds):
            if not self._running:
                return
            time.sleep(1)
