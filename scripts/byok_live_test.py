"""Live BYOK loop test: a real Telnyx call becomes a spec-valid vCon.

A test rig, not shipped code. It stands in for both the Call Control application
and the conserver so one phone call exercises the whole path end to end and
records the unknowns CON-844 asks about.

Flow:

    call.initiated   -> answer
    call.answered    -> record_start, then speak a short prompt
    call.speak.ended -> hangup
    call.recording.saved -> download, build a vCon, validate, save

Everything Telnyx sends is written to `out/events/` verbatim, because the point
of the exercise is to learn the real payload shapes rather than trust the docs.

    TELNYX_API_KEY=... python scripts/byok_live_test.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("byok")

API = os.getenv("TELNYX_API_URL", "https://api.telnyx.com/v2")
KEY = os.getenv("TELNYX_API_KEY", "")
OUT = Path(__file__).resolve().parent.parent / "out"
EVENTS = OUT / "events"

SPOKEN = (
    "This is a V conic bring your own key test call. "
    "The quick brown fox jumps over the lazy dog. "
    "Recording should now be saved and turned into a V con."
)


def command(call_control_id: str, action: str, payload: dict | None = None) -> dict:
    """Issue a Call Control command and log the outcome."""
    url = f"{API}/calls/{call_control_id}/actions/{action}"
    r = requests.post(
        url,
        json=payload or {},
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        timeout=20,
    )
    ok = r.status_code < 400
    logger.info("%s %s -> %s", action, call_control_id[:18], r.status_code)
    if not ok:
        logger.error("  %s", r.text[:300])
    return r.json() if r.content else {}


def save_event(event_type: str, body: dict) -> None:
    EVENTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S_%f")
    (EVENTS / f"{stamp}_{event_type.replace('.', '_')}.json").write_text(
        json.dumps(body, indent=2)
    )


def probe_recording_urls(payload: dict[str, Any]) -> dict[str, Any]:
    """Answer the questions CON-844 actually asks about the recording URL.

    Specifically: does fetching it need the API key, or is it pre-signed? The
    current adapter always sends the key, which is harmless either way, but the
    retention and custody design depends on knowing.
    """
    findings: dict[str, Any] = {}
    urls = payload.get("recording_urls") or {}
    public = payload.get("public_recording_urls") or {}
    findings["recording_urls"] = urls
    findings["public_recording_urls"] = public

    for label, url in (("wav", urls.get("wav")), ("mp3", urls.get("mp3"))):
        if not url:
            continue
        for auth in (True, False):
            try:
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {KEY}"} if auth else {},
                    timeout=30,
                    stream=True,
                )
                findings[f"{label}_with_key" if auth else f"{label}_no_key"] = {
                    "status": r.status_code,
                    "content_type": r.headers.get("content-type"),
                    "content_length": r.headers.get("content-length"),
                }
                r.close()
            except Exception as exc:  # noqa: BLE001
                findings[f"{label}_with_key" if auth else f"{label}_no_key"] = {
                    "error": str(exc)[:200]
                }
    return findings


def build_app() -> FastAPI:
    app = FastAPI(title="BYOK live test")
    state: dict[str, Any] = {"recording_seen": False}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/hook", response_class=PlainTextResponse)
    @app.post("/", response_class=PlainTextResponse)
    async def hook(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            logger.warning("non-JSON webhook body")
            return "OK"

        data = body.get("data", body)
        event_type = data.get("event_type", "?")
        payload = data.get("payload", {})
        ccid = payload.get("call_control_id", "")

        save_event(event_type, body)
        logger.info("<- %s", event_type)

        if event_type == "call.initiated":
            # Only answer inbound legs; answering our own outbound leg loops.
            if payload.get("direction") == "incoming":
                command(ccid, "answer")

        elif event_type == "call.answered":
            command(ccid, "record_start", {"format": "wav", "channels": "dual"})
            time.sleep(0.5)
            command(
                ccid,
                "speak",
                {"payload": SPOKEN, "voice": "female", "language": "en-US"},
            )

        elif event_type == "call.speak.ended":
            command(ccid, "hangup")

        elif event_type in ("call.recording.saved", "recording.saved"):
            state["recording_seen"] = True
            handle_recording(body, payload)

        return "OK"

    return app


def handle_recording(body: dict, payload: dict) -> None:
    """The payoff: real recording -> real vCon, validated and saved."""
    OUT.mkdir(parents=True, exist_ok=True)

    findings = probe_recording_urls(payload)
    (OUT / "recording_url_findings.json").write_text(json.dumps(findings, indent=2))
    logger.info("recording URL findings written")

    builder = TelnyxVconBuilder(
        download_recordings=True, recording_format="wav", api_key=KEY
    )
    vcon = builder.build(TelnyxRecordingData(body))
    if vcon is None:
        logger.error("builder returned None; see out/events/ for the payload")
        return

    valid, errors = vcon.is_valid()
    basis = vcon.find_lawful_basis_attachments()

    path = OUT / f"vcon_{vcon.uuid}.json"
    path.write_text(vcon.to_json())

    dialog = vcon.to_dict()["dialog"][0]
    body_len = len(dialog.get("body") or "")
    logger.info("=" * 62)
    logger.info("vCon        : %s", vcon.uuid)
    logger.info("spec valid  : %s %s", valid, errors or "")
    logger.info("syntax      : %s", vcon.to_dict().get("vcon"))
    logger.info("mediatype   : %s", dialog.get("mediatype"))
    logger.info("duration    : %s", dialog.get("duration"))
    logger.info("audio       : %d base64 chars", body_len)
    logger.info("lawful_basis: %s", basis or "ABSENT (CON-814)")
    logger.info("saved       : %s", path)
    logger.info("=" * 62)


def main() -> int:
    if not KEY:
        print("TELNYX_API_KEY is not set", file=sys.stderr)
        return 2
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logger.info("listening on :%d, writing to %s", args.port, OUT)
    uvicorn.run(build_app(), host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
