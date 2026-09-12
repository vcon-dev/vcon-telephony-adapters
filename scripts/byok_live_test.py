"""Live BYOK loop test: a real Telnyx call becomes a spec-valid vCon.

A test rig, not shipped code. It stands in for both the Call Control application
and the conserver so one phone call exercises the whole path end to end and
records the unknowns CON-844 asks about.

Flow:

    call.initiated   -> answer
    call.answered    -> record_start, then speak a short announcement
    (the caller talks, then hangs up)
    call.recording.saved -> download, build a vCon, validate, save

The caller ends the call, not the rig. `--max-seconds` bounds the recording so a
forgotten call cannot run up a bill.

Set `LAWFUL_BASIS` or the vCon carries no record of why the recording may be
held. It is never invented: unset means absent plus a warning.

Everything Telnyx sends is written to `out/events/` verbatim, because the point
of the exercise is to learn the real payload shapes rather than trust the docs.

    TELNYX_API_KEY=... LAWFUL_BASIS=legitimate_interests \
        python scripts/byok_live_test.py --port 8080
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
from core.lawful_basis import LawfulBasisConfig  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("byok")

API = os.getenv("TELNYX_API_URL", "https://api.telnyx.com/v2")
KEY = os.getenv("TELNYX_API_KEY", "")
OUT = Path(__file__).resolve().parent.parent / "out"
EVENTS = OUT / "events"

SPOKEN = os.getenv(
    "BYOK_ANNOUNCEMENT",
    "This call is being recorded. Please speak after the tone, " "and hang up when you are done.",
)

# Ceiling on the recording, in seconds. The caller decides when the call ends,
# so this is the only thing standing between a forgotten handset and a bill.
MAX_SECONDS = int(os.getenv("BYOK_MAX_SECONDS", "120"))

# Built in main() so an invalid LAWFUL_BASIS fails at startup rather than after
# someone has already placed the call.
LAWFUL: LawfulBasisConfig | None = None


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
    (EVENTS / f"{stamp}_{event_type.replace('.', '_')}.json").write_text(json.dumps(body, indent=2))


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
            command(
                ccid,
                "record_start",
                {"format": "wav", "channels": "dual", "max_length": MAX_SECONDS},
            )
            time.sleep(0.5)
            command(
                ccid,
                "speak",
                {"payload": SPOKEN, "voice": "female", "language": "en-US"},
            )

        elif event_type == "call.speak.ended":
            # Deliberately no hangup. The caller ends the call, which is the
            # whole point of a demo where a person talks. `max_length` on the
            # recording is the cost ceiling.
            logger.info("   announcement done; recording until the caller hangs up")

        elif event_type == "call.hangup":
            logger.info("   caller hung up (%s)", payload.get("hangup_cause", "?"))

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

    publisher = None
    backend = os.getenv("MEDIA_BACKEND", "embed").lower()
    if backend == "filesystem":
        from core.media_publisher import FilesystemPublisher

        dest = Path(os.environ["MEDIA_FILESYSTEM_PATH"])
        dest.mkdir(parents=True, exist_ok=True)
        publisher = FilesystemPublisher(destination=dest, base_url=os.getenv("MEDIA_BASE_URL"))
    builder = TelnyxVconBuilder(
        download_recordings=True,
        recording_format="wav",
        api_key=KEY,
        publisher=publisher,
        lawful_basis=LAWFUL,
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
    logger.info("media mode  : %s", backend)
    logger.info("url         : %s", dialog.get("url") or "(embedded)")
    logger.info("content_hash: %s", dialog.get("content_hash") or "(none)")
    logger.info("vcon bytes  : %d", len(vcon.to_json()))
    logger.info("=" * 62)
    logger.info("vCon        : %s", vcon.uuid)
    logger.info("spec valid  : %s %s", valid, errors or "")
    logger.info("syntax      : %s", vcon.to_dict().get("vcon"))
    logger.info("mediatype   : %s", dialog.get("mediatype"))
    logger.info("duration    : %s", dialog.get("duration"))
    logger.info("audio       : %d base64 chars", body_len)
    logger.info(
        "lawful_basis: %s",
        basis or "ABSENT — set LAWFUL_BASIS; it is never invented",
    )
    logger.info("saved       : %s", path)
    logger.info("=" * 62)


def main() -> int:
    if not KEY:
        print("TELNYX_API_KEY is not set", file=sys.stderr)
        return 2
    global MAX_SECONDS, LAWFUL

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument(
        "--max-seconds",
        type=int,
        default=MAX_SECONDS,
        help="recording ceiling; the caller normally hangs up first",
    )
    args = ap.parse_args()
    MAX_SECONDS = args.max_seconds

    # Raises on an invalid basis, here rather than mid-call.
    LAWFUL = LawfulBasisConfig(
        lawful_basis=os.getenv("LAWFUL_BASIS"),
        purposes=[
            p.strip()
            for p in os.getenv("LAWFUL_BASIS_PURPOSES", "recording").split(",")
            if p.strip()
        ],
        expiration=os.getenv("LAWFUL_BASIS_EXPIRATION") or None,
        justification=os.getenv("LAWFUL_BASIS_JUSTIFICATION") or None,
    )
    if LAWFUL.enabled:
        logger.info("lawful basis: %s for %s", LAWFUL.lawful_basis, LAWFUL.purposes)
    else:
        logger.warning(
            "LAWFUL_BASIS is unset, so the vCon will carry no record of why "
            "this recording may be held. Fine for a lab call; do not demo it."
        )

    OUT.mkdir(parents=True, exist_ok=True)
    logger.info("listening on :%d, recording ceiling %ds", args.port, MAX_SECONDS)
    logger.info("writing to %s", OUT)
    uvicorn.run(build_app(), host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
