"""OpenTelemetry tracing: inert without an endpoint, and nothing sensitive in an exported span.

The webhook tests run the real Telnyx route, builder and poster against a local server that
plays both the carrier's recording host and the conserver, so the `requests` client spans are
real. Numbers are synthetic 555-01xx.
"""

import http.server
import json
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
TENANT = "t-0123456789abcdef"
SECRET = "hunter2-callback-token"
SIGNATURE = "SIGNATUREVALUE123"
FROM_NUMBER = "+15085550100"
TO_NUMBER = "+14015550101"

FORM = {
    "CallSid": "v3:CALLCONTROLID",
    "CallSessionId": "session-abc-123",
    "Direction": "inbound",
    "From": FROM_NUMBER,
    "To": TO_NUMBER,
    "RecordingChannels": "2",
    "RecordingEndTime": "2026-09-13T02:35:25.068185Z",
    "RecordingSid": "rec-0001",
    "RecordingStartTime": "2026-09-13T02:35:02.522170Z",
    "RecordingStatus": "completed",
    "Annotation-tenant": TENANT,
}


def _run_python(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os

    full = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    full.update(env or {})
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=full,
        cwd=REPO_ROOT,
        timeout=60,
    )


def test_unset_endpoint_loads_no_opentelemetry_sdk():
    """Unset: no SDK, exporter or instrumentation module loads, and the helpers add nothing.

    Some third-party packages (recent FastAPI) import the small `opentelemetry` API package on
    their own; that is not ours and is not the SDK, so the helpers are checked against it.
    """
    result = _run_python("""
        import sys
        import main
        from adapters.telnyx import webhook
        def loaded():
            return {m for m in sys.modules if m.split(".")[0] == "opentelemetry"}
        before = loaded()
        from core import telemetry
        assert telemetry.init_tracing(None, "telnyx") is False
        telemetry.annotate_current_span(call_session_id="x")
        with telemetry.traced_span("s", vcon_uuid="u"):
            pass
        telemetry.shutdown_tracing()
        after = loaded()
        assert after == before, sorted(after - before)
        heavy = [m for m in after if m.startswith(
            ("opentelemetry.sdk", "opentelemetry.exporter", "opentelemetry.instrumentation"))]
        assert not heavy, heavy
        """)
    assert result.returncode == 0, result.stdout + result.stderr


def test_set_endpoint_starts_the_sdk():
    result = _run_python(
        """
        import sys
        from core import telemetry
        assert telemetry.init_tracing(None, "telnyx") is True
        assert "opentelemetry.sdk.trace" in sys.modules
        telemetry.shutdown_tracing()
        """,
        env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"},
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_service_name_and_environment_from_env():
    result = _run_python(
        """
        from core import telemetry
        telemetry.init_tracing(None, "telnyx")
        attrs = telemetry._state["provider"].resource.attributes
        print(attrs["service.name"], attrs["deployment.environment"])
        telemetry.shutdown_tracing()
        """,
        env={
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
            "OTEL_SERVICE_NAME": "adapter-ledger",
            "OTEL_DEPLOYMENT_ENVIRONMENT": "staging",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["adapter-ledger", "staging"]


class _Handler(http.server.BaseHTTPRequestHandler):
    posts: list = []

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.end_headers()
        self.wfile.write(b"ID3" + b"\x00" * 64)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _Handler.posts.append({"headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    _Handler.posts = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def traced_client(monkeypatch, tmp_path, local_server):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from adapters.telnyx.config import TelnyxConfig
    from adapters.telnyx.webhook import create_app
    from core import telemetry

    monkeypatch.setenv("CONSERVER_URL", f"{local_server}/vcon")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setenv("VALIDATE_TELNYX_WEBHOOK", "false")
    monkeypatch.setenv("TELNYX_TEXML_CALLBACK_TOKEN", SECRET)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    exporter = InMemorySpanExporter()
    app = create_app(TelnyxConfig())
    assert telemetry.init_tracing(app, "telnyx", inner_exporter=exporter, simple=True)
    try:
        yield TestClient(app), exporter, local_server
    finally:
        telemetry.shutdown_tracing()


def _everything(spans) -> str:
    """All text a span could carry, as one string."""
    parts = []
    for s in spans:
        parts.append(s.name)
        parts.append(json.dumps(dict(s.attributes or {}), default=str))
        parts.append(s.status.description or "")
        for e in s.events:
            parts.append(e.name)
            parts.append(json.dumps(dict(e.attributes or {}), default=str))
        for link in s.links:
            parts.append(json.dumps(dict(link.attributes or {}), default=str))
    return "\n".join(parts)


def test_webhook_spans_carry_ids_and_nothing_sensitive(traced_client):
    client, exporter, base = traced_client
    form = {**FORM, "RecordingUrl": f"{base}/rec.mp3?X-Amz-Signature={SIGNATURE}&X-Amz-Expires=600"}
    r = client.post(f"/webhook/texml-recording?token={SECRET}", data=form)
    assert r.status_code == 200
    assert len(_Handler.posts) == 1, "the vCon should reach the local conserver"

    spans = exporter.get_finished_spans()
    assert spans, "no spans exported"
    text = _everything(spans)
    for forbidden in (
        SECRET,
        SIGNATURE,
        "X-Amz",
        "token=",
        "?",
        FROM_NUMBER,
        TO_NUMBER,
        "15085550100",
        "14015550101",
        "stacktrace",
    ):
        assert forbidden not in text, forbidden
    for s in spans:
        for key in ("http.url", "url.full", "http.target", "url.path"):
            if key in (s.attributes or {}):
                assert "?" not in s.attributes[key]
        assert "url.query" not in (s.attributes or {})

    # the rule was applied to real URL-bearing spans, not to an empty set
    assert any(s.kind.name == "CLIENT" for s in spans)
    assert any(s.kind.name == "SERVER" for s in spans)


def test_ids_and_tenant_are_span_attributes(traced_client):
    client, exporter, base = traced_client
    form = {**FORM, "RecordingUrl": f"{base}/rec.mp3?X-Amz-Signature={SIGNATURE}"}
    client.post(f"/webhook/texml-recording?token={SECRET}", data=form)
    spans = exporter.get_finished_spans()

    server = next(s for s in spans if s.kind.name == "SERVER" and "texml-recording" in s.name)
    assert server.attributes["call.session_id"] == "session-abc-123"
    assert server.attributes["call.control_id"] == "v3:CALLCONTROLID"
    assert server.attributes["call.carrier"] == "telnyx"
    assert server.attributes["tenant.id"] == TENANT
    assert server.attributes["vcon.uuid"]

    post = next(s for s in spans if s.name == "vcon.post")
    assert post.attributes["call.session_id"] == "session-abc-123"
    assert post.attributes["vcon.uuid"] == server.attributes["vcon.uuid"]
    assert post.parent.span_id == server.context.span_id


def test_conserver_post_carries_traceparent_and_no_baggage(traced_client):
    client, exporter, base = traced_client
    form = {**FORM, "RecordingUrl": f"{base}/rec.mp3?X-Amz-Signature={SIGNATURE}"}
    client.post(
        f"/webhook/texml-recording?token={SECRET}",
        data=form,
        headers={"baggage": "injected=1"},
    )
    headers = {k.lower(): v for k, v in _Handler.posts[0]["headers"].items()}
    assert headers["traceparent"].startswith("00-")
    assert "baggage" not in headers
    # the post is the child of the webhook trace
    trace_id = headers["traceparent"].split("-")[1]
    server = next(s for s in exporter.get_finished_spans() if s.kind.name == "SERVER")
    assert format(server.context.trace_id, "032x") == trace_id


def test_inbound_baggage_is_not_forwarded(traced_client):
    client, _, base = traced_client
    form = {**FORM, "RecordingUrl": f"{base}/rec.mp3?X-Amz-Signature={SIGNATURE}"}
    tp = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
    client.post(
        f"/webhook/texml-recording?token={SECRET}",
        data=form,
        headers={"traceparent": tp, "baggage": "k=v"},
    )
    headers = {k.lower(): v for k, v in _Handler.posts[0]["headers"].items()}
    assert headers["traceparent"].split("-")[1] == "ab" * 16
    assert "baggage" not in headers


def test_sanitizer_on_hostile_spans():
    """Names, events, links and status are cleaned; a stack trace never survives."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import Link, Status, StatusCode

    from core.telemetry import SanitizingExporter

    inner = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(SanitizingExporter(inner)))
    tracer = provider.get_tracer("t")
    other = tracer.start_span("other")
    other.end()
    url = f"https://bucket.example.test/a.mp3?X-Amz-Signature={SIGNATURE}#frag"
    with pytest.raises(RuntimeError):
        with tracer.start_as_current_span(
            f"GET {url}",
            attributes={
                "http.url": url,
                "url.full": url,
                "http.target": f"/webhook?token={SECRET}",
                "url.query": f"token={SECRET}",
                "url.path": f"/p?token={SECRET}",
                "http.request.header.authorization": "Bearer abc",
                "note": f"called {FROM_NUMBER} and (401) 555-0102",
                "list": [f"x {url}"],
            },
            links=[Link(other.get_span_context(), {"u": url})],
        ) as span:
            span.add_event(f"saw {url}", {"u": url})
            span.set_status(Status(StatusCode.ERROR, f"failed {url} for {TO_NUMBER}"))
            raise RuntimeError(f"boom fetching {url}")
    spans = inner.get_finished_spans()
    span = next(s for s in spans if s.name.startswith("GET"))
    text = _everything(spans)
    for forbidden in (
        SIGNATURE,
        SECRET,
        "?",
        "#frag",
        "Bearer",
        "5085550100",
        "4015550101",
        "0102 ",
    ):
        assert forbidden not in text, forbidden
    assert "***0100" in text
    assert any(e.name == "exception" for e in span.events)
    exc = next(e for e in span.events if e.name == "exception")
    assert "exception.stacktrace" not in exc.attributes
    assert exc.attributes["exception.type"] == "RuntimeError"
    assert "boom fetching" in exc.attributes["exception.message"]
    assert span.status.status_code == StatusCode.ERROR


def test_sanitizer_never_throws_and_still_exports():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from core import telemetry

    inner = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(telemetry.SanitizingExporter(inner)))
    original = telemetry.scrub_attributes

    def boom(_):
        raise ValueError("bad")

    telemetry.scrub_attributes = boom
    try:
        with provider.get_tracer("t").start_as_current_span("s?q=1", attributes={"a": "b"}):
            pass
    finally:
        telemetry.scrub_attributes = original
    (span,) = inner.get_finished_spans()
    assert span.name == "s"
    assert dict(span.attributes or {}) == {}


def test_shutdown_with_unreachable_endpoint_is_bounded():
    """A collector that accepts the connection and never answers must not hold up a stop."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    port = server.getsockname()[1]
    code = """
        import time
        from core import telemetry
        telemetry.init_tracing(None, "telnyx")
        with telemetry.traced_span("one", call_session_id="s"):
            pass
        t = time.monotonic()
        telemetry.shutdown_tracing()
        print("ELAPSED", time.monotonic() - t)
    """
    start = time.monotonic()
    result = _run_python(code, env={"OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{port}"})
    server.close()
    assert result.returncode == 0, result.stderr
    elapsed = float(result.stdout.split("ELAPSED")[1].split()[0])
    assert elapsed < 4, elapsed
    assert time.monotonic() - start < 20


@pytest.mark.parametrize(
    "text",
    [
        "http://127.0.0.1/rec/out-5085550123-1001-20260101/file.wav",
        "http://127.0.0.1/rec/15085550123_inbound.wav",
        "call from 1-508-555-0123 ended",
        "call from 508-555-0123x12 ended",
        "call from (508) 555-0123 ended",
        "from +1 508 555 0123",
        "sip:+15085550123@example.test",
    ],
)
def test_numbers_are_masked_inside_paths_and_formats(text):
    from core.telemetry import scrub_text

    out = scrub_text(text)
    assert "508" not in out and "5550123" not in out and "50855" not in out, out
    assert "***0123" in out, out


def test_uuids_and_ids_are_not_mistaken_for_numbers():
    from core.telemetry import scrub_text

    for value in (
        "7f1e2d3c-1111-4222-8333-123456789012",
        "session-abc-123",
        "t-0123456789abcdef",
        "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01",
    ):
        assert scrub_text(value) == value


def test_integer_attributes_that_are_numbers_are_masked():
    from core.telemetry import scrub_attributes

    out = scrub_attributes({"n": 15085550123, "port": 8080, "ok": True, "ids": [15085550123, 7]})
    assert out["n"] == "***0123"
    assert out["port"] == 8080 and out["ok"] is True
    assert out["ids"] == ("***0123", 7)


def test_spans_are_flushed_on_sigterm_of_the_real_main(tmp_path):
    """uvicorn re-raises SIGTERM after shutting down, so the flush must run in the lifespan."""
    import os
    import signal
    import urllib.request

    received: list[bytes] = []

    class Collector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=collector.serve_forever, daemon=True).start()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    env.update(
        {
            "CONSERVER_URL": "http://127.0.0.1:9/vcon",
            "STATE_FILE": str(tmp_path / "state.json"),
            "VALIDATE_TELNYX_WEBHOOK": "false",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DOWNLOAD_RECORDINGS": "false",
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{collector.server_address[1]}",
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "main.py", "telnyx"],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(f"{base}/health", timeout=1).read()
                break
            except Exception:
                time.sleep(0.2)
        else:
            pytest.fail("adapter did not start")
        body = "&".join(
            f"{k}={v}"
            for k, v in {
                "RecordingStatus": "completed",
                "RecordingUrl": "http://127.0.0.1:9/x.mp3",
                "RecordingSid": "rec-sigterm",
                "CallSessionId": "session-sigterm",
            }.items()
        ).encode()
        urllib.request.urlopen(f"{base}/webhook/texml-recording", data=body, timeout=10).read()
        assert not received, "the batch should still be waiting when the stop arrives"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
        collector.shutdown()
    assert received, "no spans reached the collector after SIGTERM"
    assert b"texml-recording" in b"".join(received)
    assert b"session-sigterm" in b"".join(received)
