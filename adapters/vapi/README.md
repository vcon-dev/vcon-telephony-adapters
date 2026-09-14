# VAPI adapter

**Ingestion mode:** webhook. [VAPI](https://vapi.ai) sends an `end-of-call-report` webhook when a voice-AI call completes. This adapter receives that report, builds a spec-compliant vCon, and ships it to a conserver.

## Run

```bash
vcon-adapter vapi
```

Then point your VAPI assistant's `serverUrl` at this adapter's `/vapi` endpoint.

## Configuration (env)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `WEBHOOK_URL` | No | — | Conserver endpoint; if empty the adapter returns the vCon inline (debugging only) |
| `WEBHOOK_AUTH_HEADER_NAME` | No | `x-conserver-api-token` | Auth header for delivery |
| `WEBHOOK_AUTH_HEADER_VALUE` | No | — | Auth header value |
| `VAPI_CUSTOMER_NAME` | No | `Customer` | Party 0 display name |
| `VAPI_AGENT_NAME` | No | `AI Agent` | Party 1 display name |
| `PORT` | No | `8080` | Server port |
| `HOST` | No | `0.0.0.0` | Bind address |

## Endpoints

- `POST /vapi` — receives the end-of-call report. Returns `{"status": "ok", "uuid": ...}` on success, `{"status": "ignored"}` if the message isn't an end-of-call.
- `GET /healthz` — health check; returns `{"status": "ok"}`.

## What's in the vCon

| Source field on VAPI payload | Lands as |
|------------------------------|----------|
| `artifact.messages[]` (role=user/bot) | One text dialog per turn; system messages skipped |
| `recordingUrl` | `attachment.purpose="recording_url"` (note: missing `content_hash` since the bytes aren't fetched — non-spec-compliant for external media, logged as warning) |
| `transcript` | `analysis.type="transcript"` with `vendor="vapi"` |
| `analysis.summary` | `analysis.type="summary"` |
| `analysis.successEvaluation` | `analysis.type="success_evaluation"` |
| `cost`, `durationSeconds`, `endedReason`, `call.id` | `attachment.purpose="call_record"` (JSON body) |

## Tests

```bash
pytest tests/adapters/vapi/
```
