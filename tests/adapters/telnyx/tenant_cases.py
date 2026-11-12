"""Real-shaped recording forwards for the tenant attachment tests, and the golden vCons.

The smart trunk receiver forwards three kinds of recording callback to this adapter's
`/webhook/texml-recording`, and only those three carry `Annotation-tenant`: a Telnyx TeXML
`recordingStatusCallback`, a bridge leg's recording callback, and a SignalWire `record_call`
callback the receiver reshapes into the TeXML form. Field names and their order are the ones
the receiver sends; every value is synthetic. The tenant field, when present, is the last
field, as the receiver adds it.

The files under `fixtures/tenant/` are the normalized wire JSON (what `HttpPoster` sends, with
the generated uuid and times replaced by placeholders) that the builder produced for these
inputs before the tenant attachment existed, at 20d3030. To re-check them, check out that
commit's `adapters/` and `core/` and run:

    python -m tests.adapters.telnyx.tenant_cases --write-goldens
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

GOLDENS = Path(__file__).parent / "fixtures" / "tenant"

TENANT = "t-00000000000000a1"
INVALID = "t-4f2a"  # the design's shorthand id: too short to be a real one

# Client addresses for the connection. The receiver posts from loopback on the same host.
LOOPBACK = ("127.0.0.1", 40000)
REMOTE = ("203.0.113.9", 40000)  # documentation range (RFC 5737)

FORMS: dict[str, list[tuple[str, str]]] = {
    # Telnyx TeXML recording callback, forwarded by the receiver's /recording.
    "telnyx": [
        ("CallSid", "v3:synthetic-call-1"),
        ("CallSessionId", "synthetic-session-1"),
        ("AccountSid", "synthetic-account"),
        ("RecordingSid", "synthetic-recording-1"),
        (
            "RecordingUrl",
            "https://media.example.test/synthetic-recording-1.mp3"
            "?X-Amz-Expires=600&X-Amz-Signature=synthetic",
        ),
        ("RecordingStatus", "completed"),
        ("RecordingDuration", "23"),
        ("RecordingChannels", "2"),
        ("RecordingSource", "DialVerb"),
        ("RecordingStartTime", "2026-10-09T16:44:49.750Z"),
        ("RecordingEndTime", "2026-10-09T16:45:12.750Z"),
        ("From", "+15550100001"),
        ("To", "+15550005001"),
        ("Direction", "inbound"),
        ("Annotation-notice", "notice-v1.wav"),
    ],
    # A bridge leg's recording callback, forwarded through the same receiver path.
    "bridge": [
        ("RecordingStatus", "completed"),
        ("RecordingSid", "synthetic-bridge-recording-1"),
        ("CallSid", "v3:synthetic-bridge-1"),
        ("CallSessionId", "synthetic-bridge-session-1"),
        ("RecordingUrl", "https://media.example.test/synthetic-bridge-recording-1.mp3"),
        ("To", "+15550005001"),
        ("From", "+15550100001"),
    ],
    # A SignalWire record_call callback as the receiver reshapes it for /sw/recording.
    "signalwire": [
        ("RecordingStatus", "completed"),
        ("RecordingSid", "synthetic-sw-recording-1"),
        ("CallSid", "synthetic-sw-call-1"),
        ("CallSessionId", "synthetic-sw-call-1"),
        ("RecordingUrl", "https://media.example.test/synthetic-sw-recording-1.mp3"),
        ("RecordingChannels", "2"),
        ("RecordingStartTime", "2026-10-09T16:44:49.750000Z"),
        ("RecordingEndTime", "2026-10-09T16:45:12.250000Z"),
        ("From", "+15550100001"),
        ("To", "+15550005003"),
        ("Direction", "inbound"),
        ("RecordingSource", "signalwire"),
    ],
}

CARRIERS = tuple(FORMS)

# None means no tenant field at all.
CASES: dict[str, str | None] = {"absent": None, "valid": TENANT, "invalid": INVALID}

# Every variable the adapter's config reads is dropped first, so a developer's shell cannot
# change what the builder emits.
_ADAPTER_ENV_PREFIXES = (
    "TELNYX_",
    "LAWFUL_BASIS",
    "MEDIA_",
    "CONSERVER_",
    "DOWNLOAD_RECORDINGS",
    "RECORDING_FORMAT",
    "INGRESS_LISTS",
    "STATE_FILE",
    "ALLOW_UNSIGNED_WEBHOOKS",
    "VALIDATE_",
    "WEBHOOK_",
)

ADAPTER_ENV = {
    "CONSERVER_URL": "https://conserver.example.test/vcon",
    "VALIDATE_TELNYX_WEBHOOK": "false",
    # No network: the dialog references the recording URL rather than fetching it.
    "DOWNLOAD_RECORDINGS": "false",
    # A deployment that configured a basis, so the comparison covers that attachment too.
    "LAWFUL_BASIS": "legitimate_interests",
}


def form_body(carrier: str, tenant: str | None = None) -> bytes:
    """The urlencoded body the receiver forwards, with the tenant field last when given."""
    pairs = list(FORMS[carrier])
    if tenant is not None:
        pairs.append(("Annotation-tenant", tenant))
    return urllib.parse.urlencode(pairs).encode()


def adapter_env(state_dir: str | os.PathLike[str], **overrides: str | None) -> dict[str, str]:
    """The process environment for one test app: this shell's, minus every adapter variable,
    plus `ADAPTER_ENV` and `overrides` (None drops a variable)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_ADAPTER_ENV_PREFIXES)}
    env.update(ADAPTER_ENV, STATE_FILE=str(Path(state_dir) / "state.json"))
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


def post_forward(
    body: bytes,
    state_dir: str | os.PathLike[str],
    *,
    peer: tuple[str, int] = LOOPBACK,
    query: str = "",
    headers: dict[str, str] | None = None,
    wrap=None,
    expect_status: int = 200,
    **env_overrides: str | None,
):
    """POST one forward through the real webhook and the real builder.

    Only the conserver post is replaced. `peer` is the connection's client address (the
    receiver posts from loopback), `query` the URL query, `wrap` an optional ASGI middleware
    put in front of the app. `env_overrides` change `ADAPTER_ENV`, None dropping a variable.
    Returns the Vcon the adapter would have posted, or None if it posted nothing.
    `built_after` is set on the result: a time just before the build, so `normalized` can tell
    the times the build generated.
    """
    built_after = datetime.now(timezone.utc)
    from adapters.telnyx.config import TelnyxConfig
    from adapters.telnyx.webhook import create_app

    with (
        patch.dict(os.environ, adapter_env(state_dir, **env_overrides), clear=True),
        patch("adapters.telnyx.webhook.HttpPoster") as poster_cls,
    ):
        poster = MagicMock()
        poster.post.return_value = True
        poster_cls.return_value = poster
        app = create_app(TelnyxConfig())
        client = TestClient(wrap(app) if wrap else app, client=peer)
        response = client.post(
            "/webhook/texml-recording" + (f"?{query}" if query else ""),
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
        )
    assert response.status_code == expect_status, response.text
    if expect_status != 200:
        return None
    assert response.text == "OK"
    if not poster.post.called:
        return None
    (vcon,), _ = poster.post.call_args
    vcon.built_after = built_after
    return vcon


def _generated(value: object, built_after: datetime) -> bool:
    """True for a timestamp the build produced, not one the form carried."""
    if not isinstance(value, str):
        return False
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return when.tzinfo is not None and when >= built_after


def normalized(vcon) -> str:
    """The wire JSON with only the generated uuid and times replaced by placeholders.

    Generated times are `created_at`, every `start` and `granted_at` stamped during the build
    (a bridge form names no recording time, so its dialog starts "now"). The result is
    re-serialized with `json.dumps`, which is what `Vcon.to_json()` does, so every other byte
    is the builder's own; the round trip is checked first.
    """
    wire = vcon.to_json()
    assert json.dumps(json.loads(wire)) == wire, "wire JSON does not round-trip byte for byte"
    doc = json.loads(wire)
    doc["uuid"] = "<uuid>"
    stamped = [doc, *doc.get("dialog", []), *doc.get("attachments", [])]
    for attachment in doc.get("attachments", []):
        if attachment.get("purpose") == "lawful_basis":
            stamped += attachment["body"].get("purpose_grants", [])
    for obj in stamped:
        for key in ("created_at", "start", "granted_at"):
            if _generated(obj.get(key), vcon.built_after):
                obj[key] = "<now>"
    return json.dumps(doc)


def golden_path(carrier: str, case: str) -> Path:
    return GOLDENS / f"{carrier}-{case}.json"


def write_goldens() -> None:
    GOLDENS.mkdir(parents=True, exist_ok=True)
    for carrier in CARRIERS:
        for case, tenant in CASES.items():
            with tempfile.TemporaryDirectory() as state_dir:
                vcon = post_forward(form_body(carrier, tenant), state_dir)
            golden_path(carrier, case).write_text(normalized(vcon) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-goldens"]:
        sys.exit("usage: python -m tests.adapters.telnyx.tenant_cases --write-goldens")
    write_goldens()
