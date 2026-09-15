"""Step 0 of the live-layer plan: does a Telnyx call stream and record at once?

A throwaway spike rig, not shipped code. It is one FastAPI app that plays both
halves of the test: the Call Control webhook that drives the call, and the
WebSocket sink that Telnyx streams media into. One phone call answers the four
questions the live-layer plan gates on (Writing/smart-trunk/plan-vcon-realtime-integration.md,
build order step 0):

  1. Does record_start coexist with streaming_start on the same call?
     -> both commands are fired on call.answered; we log each response, and
        whether call.recording.saved still arrives while the stream is up.
  2. Is L16 big-endian on the wire?
     -> every track is written to TWO wavs, <track>_le.wav (bytes as-is) and
        <track>_be.wav (16-bit byteswapped). Listen: the intelligible one wins.
  3. What does the `start` message carry (call_control_id, from, to, format)?
     -> the full start payload is logged and saved to out/stream/.
  4. Does siprec_start on the same call kill the stream? (optional)
     -> with --try-siprec, a fork is started a few seconds in; watch whether
        media frames stop. Off by default because it muddies question 1.

Run:

    pip install 'uvicorn[standard]'   # or: pip install websockets   (WS backend)
    TELNYX_API_KEY=... \
    PUBLIC_WSS=wss://<public-host>/media \
        python scripts/telnyx_stream_spike.py --port 8080

PUBLIC_WSS is the address Telnyx dials back for media: a public wss:// URL that
reaches this process (ngrok, the test droplet's Caddy, etc.) at the /media path.
The Call Control application's webhook points at http(s)://<same-host>/hook.

Everything Telnyx sends is written verbatim to out/stream/, because the point of
a spike is to learn the real payload shapes, not to trust the docs.
"""

from __future__ import annotations

import argparse
import array
import base64
import json
import logging
import os
import sys
import threading
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("spike")

API = os.getenv("TELNYX_API_URL", "https://api.telnyx.com/v2")
KEY = os.getenv("TELNYX_API_KEY", "")
PUBLIC_WSS = os.getenv("PUBLIC_WSS", "")
OUT = Path(__file__).resolve().parent.parent / "out" / "stream"

# streaming_start defaults. L16/16k is the media server's native format and the
# thing we most want to confirm; every field is env-overridable because the
# exact parameter names are part of what the spike is here to verify.
STREAM_TRACK = os.getenv("TELNYX_STREAM_TRACK", "both_tracks")
STREAM_CODEC = os.getenv("TELNYX_STREAM_CODEC", "L16")
STREAM_RATE = int(os.getenv("TELNYX_STREAM_SAMPLE_RATE", "16000"))
# Anything the two knobs above cannot express: a JSON object merged last, so a
# wrong guess above can be corrected from the shell without editing this file.
STREAM_EXTRA = json.loads(os.getenv("TELNYX_STREAM_EXTRA", "{}"))

MAX_SECONDS = int(os.getenv("SPIKE_MAX_SECONDS", "120"))

# Filled from CLI in main().
TRY_SIPREC = False
SIPREC_CONNECTOR = os.getenv("TELNYX_SIPREC_CONNECTOR", "")
SIPREC_DELAY = int(os.getenv("SPIKE_SIPREC_DELAY", "8"))
NO_RECORD = False


def command(call_control_id: str, action: str, payload: dict | None = None) -> tuple[int, dict]:
    """Issue a Call Control command and log the outcome."""
    url = f"{API}/calls/{call_control_id}/actions/{action}"
    r = requests.post(
        url,
        json=payload or {},
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        timeout=20,
    )
    logger.info("%s %s -> %s", action, call_control_id[:18], r.status_code)
    if r.status_code >= 400:
        logger.error("  %s", r.text[:400])
    return r.status_code, (r.json() if r.content else {})


def save_event(kind: str, body: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%H%M%S_%f")
    (OUT / f"{stamp}_{kind.replace('.', '_')}.json").write_text(json.dumps(body, indent=2))


def streaming_start_payload() -> dict:
    """Best-guess streaming_start body; STREAM_EXTRA overrides any field."""
    payload = {
        "stream_url": PUBLIC_WSS,
        "stream_track": STREAM_TRACK,
        "stream_codec": STREAM_CODEC,
        "stream_sample_rate": STREAM_RATE,
    }
    payload.update(STREAM_EXTRA)
    return payload


def write_wavs(track: str, raw: bytes, sample_rate: int) -> list[str]:
    """Write both endian interpretations of 16-bit PCM so a listener can tell.

    WAV PCM is little-endian by definition, so _le.wav plays the bytes as they
    arrived and _be.wav plays them 16-bit byteswapped. audioop is gone in 3.13+,
    so the swap uses array.byteswap.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix, data in (("le", raw), ("be", _byteswap16(raw))):
        path = OUT / f"{track}_{suffix}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(data)
        written.append(str(path))
    return written


def _byteswap16(raw: bytes) -> bytes:
    a = array.array("h")
    a.frombytes(raw[: len(raw) - (len(raw) % 2)])  # drop a stray odd byte
    a.byteswap()
    return a.tobytes()


def build_app() -> FastAPI:
    app = FastAPI(title="Telnyx stream spike")
    # Shared, single-call rig: no locking, one call at a time. Correct because
    # the spike is driven by one operator placing one call.
    # ponytail: single-call rig, add per-call keying only if it ever runs concurrent calls
    state: dict[str, Any] = {
        "recording_saved": False,
        "stream_started_ok": None,
        "media_format": None,
        "frames": {},          # track -> bytearray
        "start_seen": False,
    }

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/hook", response_class=PlainTextResponse)
    @app.post("/", response_class=PlainTextResponse)
    async def hook(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return "OK"
        data = body.get("data", body)
        event_type = data.get("event_type", "?")
        payload = data.get("payload", {})
        ccid = payload.get("call_control_id", "")
        save_event(event_type, body)
        logger.info("<- %s", event_type)

        if event_type == "call.initiated":
            if payload.get("direction") == "incoming":
                command(ccid, "answer")

        elif event_type == "call.answered":
            if not NO_RECORD:
                command(ccid, "record_start",
                        {"format": "wav", "channels": "dual", "max_length": MAX_SECONDS})
            sc, _ = command(ccid, "streaming_start", streaming_start_payload())
            state["stream_started_ok"] = sc < 400
            if sc >= 400:
                logger.error("streaming_start rejected; adjust TELNYX_STREAM_* / TELNYX_STREAM_EXTRA")
            if TRY_SIPREC and SIPREC_CONNECTOR:
                threading.Timer(
                    SIPREC_DELAY, _start_siprec, args=(ccid,)
                ).start()

        elif event_type in ("call.recording.saved", "recording.saved"):
            state["recording_saved"] = True
            logger.info("   RECORDING SAVED while streaming=%s  <- question 1 answered",
                        state["stream_started_ok"])

        elif event_type == "call.hangup":
            logger.info("   caller hung up (%s)", payload.get("hangup_cause", "?"))
            _flush(state)

        return "OK"

    def _start_siprec(ccid: str) -> None:
        logger.info("   starting SIPREC fork on the live stream (question 4)")
        command(ccid, "siprec_start", {"connector_name": SIPREC_CONNECTOR})

    @app.websocket("/media")
    async def media(ws: WebSocket):
        await ws.accept()
        logger.info("-> media socket connected")
        try:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("text") is not None:
                    _on_text(state, msg["text"])
                elif msg.get("bytes") is not None:
                    state["frames"].setdefault("binary", bytearray()).extend(msg["bytes"])
        except WebSocketDisconnect:
            pass
        finally:
            logger.info("-> media socket closed")
            _flush(state)

    return app


def _on_text(state: dict, text: str) -> None:
    try:
        m = json.loads(text)
    except json.JSONDecodeError:
        return
    event = m.get("event")
    if event == "start":
        state["start_seen"] = True
        start = m.get("start", m)
        state["media_format"] = start.get("media_format", {})
        save_event("ws_start", m)
        logger.info("   START media_format=%s call_control_id=%s from=%s to=%s",
                    state["media_format"], start.get("call_control_id"),
                    start.get("from"), start.get("to"))
    elif event == "media":
        media = m.get("media", {})
        track = media.get("track", "inbound")
        chunk = media.get("payload") or media.get("chunk") or ""
        if chunk:
            try:
                state["frames"].setdefault(track, bytearray()).extend(base64.b64decode(chunk))
            except Exception:  # noqa: BLE001
                pass
    elif event == "stop":
        save_event("ws_stop", m)


def _flush(state: dict) -> None:
    """Write the WAVs and a findings summary once, at end of call."""
    if state.get("_flushed"):
        return
    state["_flushed"] = True
    fmt = state.get("media_format") or {}
    rate = int(fmt.get("sample_rate") or STREAM_RATE)
    wavs: list[str] = []
    for track, buf in state.get("frames", {}).items():
        if buf:
            wavs += write_wavs(track, bytes(buf), rate)
    findings = {
        "q1_recording_saved_while_streaming": state.get("recording_saved"),
        "q1_streaming_start_accepted": state.get("stream_started_ok"),
        "q2_endianness": "listen to *_le.wav vs *_be.wav; the clear one names the wire order",
        "q3_media_format": fmt,
        "q3_start_seen": state.get("start_seen"),
        "bytes_per_track": {t: len(b) for t, b in state.get("frames", {}).items()},
        "wavs": wavs,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "findings.json").write_text(json.dumps(findings, indent=2))
    logger.info("=" * 62)
    logger.info("FINDINGS: %s", json.dumps(findings))
    logger.info("=" * 62)


def _selfcheck() -> int:
    """Runnable check: the WAV writer and byteswap, no call needed."""
    import tempfile

    global OUT
    OUT = Path(tempfile.mkdtemp()) / "stream"
    raw = bytes([0x00, 0x01, 0xFF, 0x7F])  # two 16-bit samples
    assert _byteswap16(raw) == bytes([0x01, 0x00, 0x7F, 0xFF]), "byteswap wrong"
    assert _byteswap16(raw[:3]) == bytes([0x01, 0x00]), "odd-length not trimmed"
    paths = write_wavs("inbound", raw, 16000)
    assert len(paths) == 2 and all(Path(p).exists() for p in paths), "wavs not written"
    with wave.open(paths[0], "rb") as w:
        assert w.getframerate() == 16000 and w.getsampwidth() == 2 and w.getnchannels() == 1
    print("selfcheck ok:", paths)
    return 0


def main() -> int:
    global MAX_SECONDS, TRY_SIPREC, NO_RECORD
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    ap.add_argument("--try-siprec", action="store_true",
                    help="start a SIPREC fork mid-stream to test the one-slot rule")
    ap.add_argument("--no-record", action="store_true",
                    help="isolate streaming; skip record_start")
    ap.add_argument("--selfcheck", action="store_true", help="run the offline self-check and exit")
    args = ap.parse_args()
    if args.selfcheck:
        return _selfcheck()
    if not KEY:
        print("TELNYX_API_KEY is not set", file=sys.stderr)
        return 2
    if not PUBLIC_WSS.startswith("wss://") and not PUBLIC_WSS.startswith("ws://"):
        print("PUBLIC_WSS must be the ws(s):// URL Telnyx dials back for media", file=sys.stderr)
        return 2
    MAX_SECONDS = args.max_seconds
    TRY_SIPREC = args.try_siprec
    NO_RECORD = args.no_record

    logger.info("listening on :%d  media sink at %s  record=%s  try_siprec=%s",
                args.port, PUBLIC_WSS, not NO_RECORD, TRY_SIPREC)
    logger.info("streaming_start payload: %s", json.dumps(streaming_start_payload()))
    logger.info("writing to %s", OUT)
    uvicorn.run(build_app(), host="0.0.0.0", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
