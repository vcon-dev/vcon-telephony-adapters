"""OpenTelemetry tracing, inert until `OTEL_EXPORTER_OTLP_ENDPOINT` is set.

With the variable unset nothing here imports OpenTelemetry and every helper is a no-op, so an
adapter behaves exactly as it did before. With it set, `init_tracing` instruments FastAPI
(server spans) and `requests` (client spans, so the conserver post carries a W3C
`traceparent`) and exports traces over OTLP/HTTP.

Spans carry ids, never conversation content. The exporter is wrapped so every span is
sanitized on a copy just before it leaves the process:

* query strings and fragments are cut from span names and URL attributes (a webhook token and
  a signed recording URL both live in a query), including on events, links and status text;
* `exception.stacktrace` is dropped, and exception messages go through `redact_text`;
* phone numbers in any text value are masked to their last four digits;
* header and credential-looking attributes are dropped.

Propagation is W3C trace context only (no baggage), so a caller cannot inject headers into
outbound requests. The exporter timeout and the shutdown are both bounded, so a collector
that is down never blocks a webhook or a stop. Traces only: no metric or log exporter starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import threading
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from core.log_safe import mask_number, redact_text, redact_text_urls, redact_url

logger = logging.getLogger(__name__)

ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
EXPORT_TIMEOUT_SECONDS = 3.0
SHUTDOWN_TIMEOUT_SECONDS = 3.0

# Attributes whose value is a URL or a request target; the query and fragment are cut.
_URL_KEYS = frozenset({"http.url", "http.target", "url.full", "url.path", "http.request.target"})
# Attributes that only ever hold a query string.
_DROP_KEYS = frozenset({"url.query", "exception.stacktrace"})
# Attribute names that can hold a credential or a header value.
_DROP_KEY_RE = re.compile(
    r"(^http\.(request|response)\.header\.)|authorization|cookie|token|secret|signature|password",
    re.IGNORECASE,
)
# Attributes that hold an id this program sets itself. Their values are never number-masked.
_ID_KEYS = frozenset({"call.session_id", "call.control_id", "vcon.uuid", "stream.id", "tenant.id"})
# Integer attributes are masked only when the name suggests a phone number, so timestamps,
# epoch values and byte counts keep their value.
_NUMBER_KEY_RE = re.compile(
    r"number|phone|caller|callee|(?:^|[._])(?:from|to|ani|dnis)$", re.IGNORECASE
)

_state: dict[str, Any] = {}


def tracing_enabled() -> bool:
    """True once `init_tracing` has started the SDK."""
    return "provider" in _state


# ---------------------------------------------------------------------------
# Sanitizing. Pure functions with no OpenTelemetry imports.
# ---------------------------------------------------------------------------


def _cut_query(text: str) -> str:
    return text.split("#", 1)[0].split("?", 1)[0]


def scrub_text(text: str) -> str:
    """Strip URL queries from free text and mask phone numbers in it."""
    return redact_text(text)


def scrub_name(name: str) -> str:
    """A span or event name with no query, fragment, or phone number."""
    return scrub_text(_cut_query(str(name)))


def _scrub_value(key: str, value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        if _NUMBER_KEY_RE.search(key) and len(str(abs(value))) >= 10:
            return mask_number(value)
        return value
    if isinstance(value, str):
        if key in _ID_KEYS:
            return redact_text_urls(value)
        if key in _URL_KEYS:
            value = redact_url(_cut_query(value))
        return scrub_text(value)
    if isinstance(value, (list, tuple)):
        return tuple(_scrub_value(key, v) for v in value)
    return value


def scrub_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """A copy of `attributes` that is safe to export."""
    out: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if key in _DROP_KEYS or _DROP_KEY_RE.search(key):
            continue
        out[key] = _scrub_value(key, value)
    return out


def _sanitized_copy(span: Any, *, skeleton: bool = False) -> Any:
    """A `ReadableSpan` copy of `span` with nothing sensitive on it.

    `skeleton` keeps only the name, ids, timing and status code: the fallback when the full
    sanitize raised, so the span still exports.
    """
    from opentelemetry.sdk.trace import Event, ReadableSpan
    from opentelemetry.trace import Link, Status

    status = span.status
    description = None if skeleton else scrub_text(status.description or "") or None
    new_status = Status(status.status_code, description)
    if skeleton:
        attributes: dict[str, Any] = {}
        events: list[Any] = []
        links: list[Any] = []
    else:
        attributes = scrub_attributes(span.attributes)
        events = [
            Event(
                name=scrub_name(e.name),
                attributes=scrub_attributes(e.attributes),
                timestamp=e.timestamp,
            )
            for e in span.events
        ]
        links = [Link(link.context, scrub_attributes(link.attributes)) for link in span.links]
    return ReadableSpan(
        name=scrub_name(span.name),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=links,
        kind=span.kind,
        status=new_status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def sanitize_span(span: Any) -> Any | None:
    """Sanitize one span; never raises. None only if even the skeleton could not be built."""
    try:
        return _sanitized_copy(span)
    except Exception:
        logger.debug("span sanitize failed, exporting a skeleton", exc_info=True)
    try:
        return _sanitized_copy(span, skeleton=True)
    except Exception:
        logger.debug("span skeleton failed, span dropped", exc_info=True)
        return None


class SanitizingExporter:
    """Wraps a span exporter so every span is sanitized on a copy before export."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def export(self, spans: Any) -> Any:
        clean = [s for s in (sanitize_span(span) for span in spans) if s is not None]
        return self._inner.export(clean)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# ---------------------------------------------------------------------------
# Start and stop.
# ---------------------------------------------------------------------------


def _resource(default_service: str) -> Any:
    """Resource for the provider. The default service name yields to the standard variables."""
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    environment = os.environ.get("OTEL_DEPLOYMENT_ENVIRONMENT", "").strip()
    extra = {"deployment.environment": environment} if environment else {}
    # Resource.create merges OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME over the dict it is
    # given, but only for keys it does not hold, so the default name is added after that merge.
    resource = Resource.create(extra)
    if resource.attributes.get(SERVICE_NAME) in (None, "unknown_service"):
        resource = resource.merge(Resource({SERVICE_NAME: default_service}))
    return resource


def init_tracing(
    app: Any = None,
    adapter: str = "",
    *,
    inner_exporter: Any = None,
    simple: bool = False,
) -> bool:
    """Start tracing when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; return whether it started.

    Call before the app serves. `app` is instrumented for server spans. `inner_exporter` and
    `simple` exist for tests: an in-memory exporter, flushed on every span end.
    """
    if tracing_enabled():
        if app is not None:
            _instrument_app(app)
        return True
    if not os.environ.get(ENDPOINT_ENV, "").strip():
        return False
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return False
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        # Trace context only: no baggage in or out.
        set_global_textmap(TraceContextTextMapPropagator())
        provider = TracerProvider(
            resource=_resource(f"vcon-telephony-adapter-{adapter}" if adapter else "vcon-adapter")
        )
        exporter = SanitizingExporter(
            inner_exporter or OTLPSpanExporter(timeout=EXPORT_TIMEOUT_SECONDS)
        )
        if simple:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        else:
            provider.add_span_processor(
                BatchSpanProcessor(
                    exporter, export_timeout_millis=int(EXPORT_TIMEOUT_SECONDS * 1000)
                )
            )
        # Not set as the global provider: only the instrumentors below and `traced_span` use it.
        _state["provider"] = provider
        _state["tracer"] = provider.get_tracer("vcon-telephony-adapters")
        RequestsInstrumentor().instrument(tracer_provider=provider)
        if app is not None:
            _instrument_app(app)
    except Exception:
        logger.warning("Tracing did not start; continuing without it", exc_info=True)
        _state.clear()
        return False
    logger.info("Tracing started (OTLP/HTTP, traces only)")
    return True


def _instrument_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    if app in _state.setdefault("apps", []):
        return
    FastAPIInstrumentor.instrument_app(
        app, tracer_provider=_state["provider"], excluded_urls="/health"
    )
    _state["apps"].append(app)

    # uvicorn re-raises SIGTERM with the default handler once the server has shut down, so code
    # after `uvicorn.run` never runs on a systemd stop. The lifespan shutdown does run, and it
    # runs after the last request, so the final spans are flushed there.
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: Any) -> AsyncIterator[Any]:
        try:
            async with inner(a) as state:
                yield state
        finally:
            await asyncio.to_thread(shutdown_tracing)

    app.router.lifespan_context = lifespan


def shutdown_tracing(timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
    """Flush and stop tracing, waiting at most `timeout` seconds. Never raises."""
    provider = _state.get("provider")
    if provider is None:
        return
    apps = list(_state.get("apps", []))
    _state.clear()
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        for app in apps:
            FastAPIInstrumentor.uninstrument_app(app)
        RequestsInstrumentor().uninstrument()
    except Exception:
        logger.debug("uninstrument failed", exc_info=True)
    worker = threading.Thread(target=provider.shutdown, name="otel-shutdown", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.warning("Tracing shutdown timed out after %.1fs; abandoning it", timeout)


# ---------------------------------------------------------------------------
# Helpers the adapters call. No-ops while tracing is off.
# ---------------------------------------------------------------------------


def call_attributes(
    *,
    call_session_id: str | None = None,
    call_control_id: str | None = None,
    carrier: str | None = None,
    tenant_id: str | None = None,
    vcon_uuid: str | None = None,
) -> dict[str, str]:
    """The span attributes that name one call, for the values that are known."""
    pairs = {
        "call.session_id": call_session_id,
        "call.control_id": call_control_id,
        "call.carrier": carrier,
        "tenant.id": tenant_id,
        "vcon.uuid": vcon_uuid,
    }
    return {k: str(v) for k, v in pairs.items() if v}


def annotate_current_span(**ids: str | None) -> None:
    """Put the call ids on the active span (the webhook's server span)."""
    if not tracing_enabled():
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        for key, value in call_attributes(**ids).items():
            span.set_attribute(key, value)
    except Exception:
        logger.debug("annotate failed", exc_info=True)


@contextlib.contextmanager
def traced_span(name: str, **ids: str | None) -> Iterator[None]:
    """An internal span carrying the call ids; a plain no-op block while tracing is off."""
    tracer = _state.get("tracer")
    if tracer is None:
        yield
        return
    with tracer.start_as_current_span(name, attributes=call_attributes(**ids)):
        yield
