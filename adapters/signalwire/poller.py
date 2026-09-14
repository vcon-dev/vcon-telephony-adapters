"""SignalWire poller — fetches new recordings on a schedule and ships vCons.

SignalWire doesn't push webhooks for recording completion the way Twilio does, so
we poll the Compatibility API on an interval. The poller maintains a small JSON
state file of processed call SIDs to prevent duplicates and ages out old records.
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
    ):
        self.config = config
        self.builder = builder or SignalWireVconBuilder()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.webhook_auth_header_value:
            headers[config.webhook_auth_header_name] = config.webhook_auth_header_value
        self.poster = poster or HttpPoster(url=config.webhook_url, headers=headers)
        self._running = True

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Poll until SIGINT / SIGTERM."""
        signal.signal(signal.SIGINT, self._stop)
        signal.signal(signal.SIGTERM, self._stop)

        last_check_time = datetime.now(UTC) - timedelta(seconds=self.config.poll_interval_seconds)
        while self._running:
            try:
                self.process(last_check_time)
            except Exception as e:
                logger.exception(f"poll loop error: {e}")
            last_check_time = datetime.now(UTC)
            self._sleep(self.config.poll_interval_seconds)

    # ------------------------------------------------------------------
    # One poll cycle (extracted so tests can drive a single iteration)
    # ------------------------------------------------------------------
    def process(self, since: datetime) -> int:
        """Fetch + ship vCons newer than `since`. Returns number shipped."""
        processed = self._load_processed_calls()
        recordings = self._fetch_recordings_since(since)
        by_call: dict[str, list[dict[str, Any]]] = {}
        for r in recordings:
            sid = r.get("call_sid")
            if not sid:
                continue
            if sid in processed:
                continue
            by_call.setdefault(sid, []).append(r)

        shipped = 0
        for call_sid, group in by_call.items():
            call_meta = self._fetch_call_meta(call_sid)
            if not call_meta:
                continue
            transcripts = {
                r.get("sid", ""): self._fetch_transcriptions(r) for r in group
            }
            data = SignalWireRecordingData(
                call_meta=call_meta,
                recordings=group,
                transcriptions_by_recording_sid=transcripts,
            )
            vcon = self.builder.build(data)
            if vcon is None:
                continue

            if self.config.debug_mode:
                self._write_debug(vcon, call_sid)
            else:
                self.poster.post(vcon)

            processed[call_sid] = datetime.now(UTC).isoformat()
            shipped += 1

        processed = self._cleanup_processed_calls(processed)
        self._save_processed_calls(processed)
        return shipped

    # ------------------------------------------------------------------
    # SignalWire API
    # ------------------------------------------------------------------
    def _api_base(self) -> str:
        return f"{self.config.space_url.rstrip('/')}/api/laml/2010-04-01/Accounts/{self.config.project_id}"

    def _auth(self) -> tuple[str, str]:
        return (self.config.project_id, self.config.auth_token)

    def _fetch_recordings_since(self, since: datetime) -> list[dict]:
        url = f"{self._api_base()}/Recordings.json"
        params = {"DateCreated>": since.strftime("%Y-%m-%d")}
        r = requests.get(url, auth=self._auth(), params=params, timeout=30)
        r.raise_for_status()
        return r.json().get("recordings", [])

    def _fetch_call_meta(self, call_sid: str) -> dict | None:
        url = f"{self._api_base()}/Calls/{call_sid}.json"
        r = requests.get(url, auth=self._auth(), timeout=30)
        if r.status_code != 200:
            logger.warning(f"call meta fetch failed for {call_sid}: {r.status_code}")
            return None
        return r.json()

    def _fetch_transcriptions(self, recording: dict) -> list[dict]:
        sub = recording.get("subresource_uris") or {}
        if "transcriptions" not in sub:
            return []
        url = f"{self.config.space_url.rstrip('/')}{sub['transcriptions']}"
        r = requests.get(url, auth=self._auth(), timeout=30)
        if r.status_code != 200:
            return []
        return r.json().get("transcriptions", [])

    # ------------------------------------------------------------------
    # State file
    # ------------------------------------------------------------------
    def _load_processed_calls(self) -> dict[str, str]:
        p = Path(self.config.processed_calls_file)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"could not read {p}: {e}; starting fresh")
            return {}

    def _save_processed_calls(self, processed: dict[str, str]) -> None:
        Path(self.config.processed_calls_file).write_text(json.dumps(processed))

    def _cleanup_processed_calls(self, processed: dict[str, str]) -> dict[str, str]:
        cutoff = datetime.now(UTC) - timedelta(days=self.config.retention_days)
        out: dict[str, str] = {}
        for sid, iso in processed.items():
            try:
                if datetime.fromisoformat(iso) > cutoff:
                    out[sid] = iso
            except ValueError:
                # malformed entry; drop it
                continue
        return out

    # ------------------------------------------------------------------
    # Debug + lifecycle
    # ------------------------------------------------------------------
    def _write_debug(self, vcon, call_sid: str) -> None:
        d = Path(self.config.debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{call_sid}.vcon.json"
        out.write_text(json.dumps(vcon.vcon_dict, indent=2, default=str))
        logger.info(f"wrote debug vcon to {out}")

    def _stop(self, *_args) -> None:
        logger.info("received stop signal")
        self._running = False

    def _sleep(self, seconds: int) -> None:
        # broken into 1s ticks so SIGTERM is responsive
        for _ in range(seconds):
            if not self._running:
                return
            time.sleep(1)
