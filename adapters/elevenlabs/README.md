# ElevenLabs adapter

**Ingestion mode:** poller (long-running). Pulls conversations from the ElevenLabs Conversational AI API on an interval, builds spec-compliant vCons, ships them to a conserver.

## Run

```bash
vcon-adapter elevenlabs
```

Or from Python — useful if you want to drive conversion directly from your own pull logic without the poller loop:

```python
from adapters.elevenlabs import ElevenLabsVconBuilder, Conversation

conversation = Conversation.model_validate(raw_elevenlabs_payload)
vcon = ElevenLabsVconBuilder().build(conversation)
```

To embed audio inline (instead of emitting a URL reference), pass the bytes:

```python
vcon = ElevenLabsVconBuilder().build(conversation, audio_bytes=open("audio.wav", "rb").read())
```

## Configuration (env)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ELEVENLABS_API_KEY` | Yes | — | ElevenLabs API key |
| `ELEVENLABS_API_BASE` | No | `https://api.elevenlabs.io/v1` | API base URL |
| `WEBHOOK_URL` | Yes | — | Conserver endpoint to POST vCons to |
| `POLL_INTERVAL` | No | `300` | Seconds between polls |

## Scope of this port

This is a thin port focused on **builder correctness** — vCon construction is spec-compliant (syntax 0.4.0, `mediatype`, `base64url`, `content_hash`, lib-helper-only). The poller implements minimal pull-mode integration without cursor-based pagination or alternate exporters.

If you need richer features (cursor pagination, S3 export, AI summarization exporters), the original standalone repo [`vcon-eleven-labs-adapter`](https://github.com/vcon-dev/vcon-eleven-labs-adapter) carries those. Port additional pieces as you need them.

## Tests

```bash
pytest tests/adapters/elevenlabs/
```
