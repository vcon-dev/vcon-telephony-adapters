# SignalWire adapter

**Ingestion mode:** poller (long-running). The SignalWire Compatibility API doesn't push a webhook on recording completion the way Twilio does, so this adapter polls the API on an interval.

## Run

```bash
vcon-adapter signalwire
```

Or from Python:

```python
from adapters.signalwire import SignalWireConfig, SignalWirePoller
SignalWirePoller(SignalWireConfig.from_env()).run()
```

## Configuration (env)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SIGNALWIRE_PROJECT_ID` | Yes | — | SignalWire project ID |
| `SIGNALWIRE_AUTH_TOKEN` | Yes | — | SignalWire auth token |
| `SIGNALWIRE_SPACE_URL` | Yes | — | e.g. `https://example.signalwire.com` |
| `WEBHOOK_URL` | Yes (unless `DEBUG_MODE=true`) | — | Conserver endpoint to POST vCons to |
| `POLL_INTERVAL` | No | `300` | Seconds between API polls |
| `S3_ENABLED` | No | `false` | Re-host recordings to S3 with presigned URLs |
| `S3_BUCKET` | If `S3_ENABLED=true` | — | Target bucket |
| `DEBUG_MODE` | No | `false` | Write vCons to disk instead of POSTing |
| `RETENTION_DAYS` | No | `30` | How long to remember processed call SIDs |

## What's distinctive

SignalWire returns multiple `Recording` objects per call (one per leg) from a single Calls API query. This builder produces **one vCon per call with multiple dialogs**, not one vCon per recording. Recording-level metadata goes in per-dialog `recording_metadata` attachments; per-recording transcripts go in `analysis[]` with `vendor: "signalwire"`.

## Tests

```bash
pytest tests/adapters/signalwire/
```
