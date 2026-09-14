# vCon Telephony Adapters

[![PyPI version](https://badge.fury.io/py/vcon-telephony-adapters.svg)](https://pypi.org/project/vcon-telephony-adapters/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A monorepo of adapters that convert telephony, voice-AI, and conversational-agent events into vCon (Virtual Conversation) format and deliver them to a vCon conserver. Supports three ingestion modes — **webhook**, **poller**, and **frame observer** — so the same vCon construction layer works for inbound webhooks, API polling, and in-process pipeline integration.

**Spec target:** IETF `draft-ietf-vcon-vcon-core-02`, vCon syntax `"0.4.0"`. All adapters route through the [`vcon`](https://pypi.org/project/vcon/) library (>=0.9.4).

## Supported platforms

| Platform | Mode | Notes |
|----------|------|-------|
| Twilio | webhook | Voice, SMS/MMS, WhatsApp, Fax, Video, Conversations API |
| FreeSWITCH | webhook | `mod_http_cache` events |
| Asterisk | webhook | ARI events |
| Telnyx | webhook | Call control webhooks |
| Bandwidth | webhook | Voice API callbacks |
| SignalWire | poller | Polls Compatibility API |
| ElevenLabs | poller | Polls conversational-AI API |
| VAPI | webhook | End-of-call reports |
| Pipecat | frame observer | In-process integration (library, not a server) |

## Architecture

```
vcon-telephony-adapters/
├── core/                    # Shared mode-agnostic modules
│   ├── base_builder.py      # Abstract vCon builder (used by webhook adapters)
│   ├── base_config.py       # Base configuration
│   ├── poster.py            # HTTP delivery to conserver
│   └── tracker.py           # State tracking for duplicates
├── adapters/
│   ├── twilio/              # webhook
│   ├── freeswitch/          # webhook
│   ├── asterisk/            # webhook
│   ├── telnyx/              # webhook
│   ├── bandwidth/           # webhook
│   ├── vapi/                # webhook
│   ├── signalwire/          # poller
│   ├── elevenlabs/          # poller
│   └── pipecat/             # frame observer (library)
├── tests/
│   ├── test_spec_compliance.py   # Cross-adapter spec contract
│   └── adapters/<platform>/      # Per-platform tests
└── main.py                  # CLI entry point (8 of 9 — pipecat is library-only)
```

## Requirements

- Python 3.12+
- [vcon-lib](https://github.com/vcon-dev/vcon-lib) - The vCon library for creating vCon objects

## Installation

### From PyPI (Recommended)

```bash
pip install vcon-telephony-adapters
```

The `vcon` library (>=0.9.4) is pulled in automatically as a dependency.

### From Source

```bash
git clone https://github.com/vcon-dev/vcon-telephony-adapters.git
cd vcon-telephony-adapters
pip install -e .
```

### Development Installation

```bash
pip install -e ".[dev]"
```

## Quick Start

### Running an adapter

Webhook adapters bind a FastAPI server on `$PORT`:

```bash
vcon-adapter twilio        # Twilio recording status callbacks
vcon-adapter freeswitch    # FreeSWITCH mod_http_cache events
vcon-adapter asterisk      # Asterisk ARI events
vcon-adapter telnyx        # Telnyx call control webhooks
vcon-adapter bandwidth     # Bandwidth voice API callbacks
vcon-adapter vapi          # VAPI end-of-call reports
```

Pollers are long-running processes that hit the platform's API on an interval:

```bash
vcon-adapter signalwire    # SignalWire Compatibility API poller
vcon-adapter elevenlabs    # ElevenLabs conversational-AI poller
```

Pipecat is **not** a CLI invocation — it's an in-process frame observer that plugs into your own Pipecat pipeline. See [`adapters/pipecat/README.md`](adapters/pipecat/README.md) for the integration pattern.

---

## Twilio Adapter

Multi-mode Twilio ingress: voice recordings, call status (incomplete calls), SMS/MMS/WhatsApp messaging, fax, video compositions, and Conversations API events. See [`adapters/twilio/README.md`](adapters/twilio/README.md) for Twilio Console setup.

### Webhook URL matrix

| Twilio product | Adapter endpoint | vCon output |
|----------------|------------------|-------------|
| Voice recording | `POST /webhook/recording` | `recording` dialog, `application: twilio_voice` |
| Voice status (no-answer, busy, failed) | `POST /webhook/voice/status` | `incomplete` dialog with disposition |
| SMS / MMS / WhatsApp / RCS | `POST /webhook/messaging` | `text` dialog + MMS attachments |
| Fax | `POST /webhook/fax` | `recording` dialog (`application/pdf`) |
| Video compositions | `POST /webhook/video` | `video` dialog (`video/mp4`) |
| Conversations API | `POST /webhook/conversations` | `text` dialog per message event |
| Demo TwiML | `GET/POST /webhook/demo` | Answers and records for testing |

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here

# Optional — server
PORT=8080
VALIDATE_TWILIO_SIGNATURE=true
WEBHOOK_URL=https://your-domain.com
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav

# Optional — per-mode toggles (all default true)
TWILIO_ENABLE_VOICE_RECORDING=true
TWILIO_ENABLE_VOICE_STATUS=true
TWILIO_ENABLE_MESSAGING=true
TWILIO_ENABLE_FAX=true
TWILIO_ENABLE_VIDEO=true
TWILIO_ENABLE_CONVERSATIONS=true

# Optional — media download
DOWNLOAD_MESSAGING_MEDIA=true
DOWNLOAD_FAX=true
DOWNLOAD_VIDEO=true
MESSAGING_SESSION_WINDOW_HOURS=24
```

### Twilio Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TWILIO_ACCOUNT_SID` | Yes* | - | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Yes* | - | Your Twilio Auth Token |
| `VALIDATE_TWILIO_SIGNATURE` | No | `true` | Validate webhook signatures |
| `WEBHOOK_URL` | No | - | Public URL for signature validation |
| `TWILIO_ENABLE_*` | No | `true` | Enable/disable each communication mode |
| `DOWNLOAD_MESSAGING_MEDIA` | No | `true` | Embed MMS media with `content_hash` |
| `MESSAGING_SESSION_WINDOW_HOURS` | No | `24` | Session tracking window for messaging |

\* Required when downloading media or `VALIDATE_TWILIO_SIGNATURE=true`

### Configuring Twilio (voice)

1. Configure your phone number or TwiML app to record calls.
2. Set Recording Status Callback URL to `https://your-domain.com/webhook/recording` (event: `completed`).
3. Set Status Callback URL to `https://your-domain.com/webhook/voice/status` for incomplete call capture.

### Using with TwiML

```xml
<Response>
    <Record
        recordingStatusCallback="https://your-domain.com/webhook/recording"
        recordingStatusCallbackEvent="completed"
    />
</Response>
```

### Tests

```bash
pip install -e ".[dev]"
pytest tests/adapters/twilio_adapter/ tests/test_spec_compliance.py
```

Fixtures for each mode live in `tests/fixtures/twilio/`.

---

## FreeSWITCH Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons

# FreeSWITCH-specific
FREESWITCH_HOST=localhost
FREESWITCH_ESL_PORT=8021
FREESWITCH_RECORDINGS_PATH=/var/lib/freeswitch/recordings
FREESWITCH_RECORDINGS_URL_BASE=https://fs.example.com/recordings
FREESWITCH_WEBHOOK_SECRET=your_webhook_secret

# Optional
PORT=8082
VALIDATE_FREESWITCH_WEBHOOK=false
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### FreeSWITCH Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FREESWITCH_HOST` | No | `localhost` | FreeSWITCH host |
| `FREESWITCH_ESL_PORT` | No | `8021` | ESL port |
| `FREESWITCH_RECORDINGS_PATH` | No | `/var/lib/freeswitch/recordings` | Local recordings path |
| `FREESWITCH_RECORDINGS_URL_BASE` | No | - | URL base for HTTP downloads |
| `FREESWITCH_WEBHOOK_SECRET` | No | - | HMAC secret for webhook validation |
| `VALIDATE_FREESWITCH_WEBHOOK` | No | `false` | Enable webhook signature validation |

### Configuring FreeSWITCH

Add mod_http_cache or a custom event handler to send recording events to the webhook:

```bash
# In your dialplan, after recording:
<action application="curl" data="https://your-domain.com/webhook/recording post ${recording_data}"/>
```

The webhook expects JSON data with FreeSWITCH event variables (uuid, caller_id_number, destination_number, etc.).

---

## Asterisk Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons

# Asterisk-specific (for ARI access)
ASTERISK_HOST=localhost
ASTERISK_ARI_PORT=8088
ASTERISK_ARI_USERNAME=asterisk
ASTERISK_ARI_PASSWORD=asterisk
ASTERISK_RECORDINGS_PATH=/var/spool/asterisk/recording

# Optional
PORT=8083
VALIDATE_ASTERISK_WEBHOOK=false
ASTERISK_WEBHOOK_SECRET=your_webhook_secret
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Asterisk Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ASTERISK_HOST` | No | `localhost` | Asterisk host |
| `ASTERISK_ARI_PORT` | No | `8088` | ARI HTTP port |
| `ASTERISK_ARI_USERNAME` | No | - | ARI username |
| `ASTERISK_ARI_PASSWORD` | No | - | ARI password |
| `ASTERISK_RECORDINGS_PATH` | No | `/var/spool/asterisk/recording` | Local recordings path |
| `ASTERISK_WEBHOOK_SECRET` | No | - | HMAC secret for webhook validation |
| `VALIDATE_ASTERISK_WEBHOOK` | No | `false` | Enable webhook signature validation |

### Configuring Asterisk

Use a Stasis application or AGI script to send recording events to the webhook endpoint:

```javascript
// ARI recording finished handler example
client.on('RecordingFinished', function(event) {
    // POST to https://your-domain.com/webhook/recording
});
```

---

## Telnyx Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons
TELNYX_API_KEY=KEY_xxxxxxxxxxxxx

# Optional
PORT=8084
VALIDATE_TELNYX_WEBHOOK=false
TELNYX_PUBLIC_KEY=your_public_key_for_signature_validation
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Telnyx Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELNYX_API_KEY` | Yes* | - | Telnyx API key for downloading recordings |
| `TELNYX_PUBLIC_KEY` | No | - | Public key for webhook signature validation |
| `VALIDATE_TELNYX_WEBHOOK` | No | `false` | Enable webhook signature validation |

\* Required when `DOWNLOAD_RECORDINGS=true`

### Configuring Telnyx

1. In the Telnyx Mission Control Portal, configure your Call Control Application.

2. Set the Webhook URL for recording events:
   ```
   https://your-domain.com/webhook/recording
   ```

3. Enable `call.recording.saved` events.

---

## Bandwidth Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons
BANDWIDTH_ACCOUNT_ID=your_account_id
BANDWIDTH_USERNAME=your_api_username
BANDWIDTH_PASSWORD=your_api_password

# Optional
PORT=8085
VALIDATE_BANDWIDTH_WEBHOOK=false
BANDWIDTH_WEBHOOK_USERNAME=webhook_user
BANDWIDTH_WEBHOOK_PASSWORD=webhook_pass
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Bandwidth Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BANDWIDTH_ACCOUNT_ID` | Yes | - | Bandwidth Account ID |
| `BANDWIDTH_USERNAME` | Yes* | - | API username for downloading recordings |
| `BANDWIDTH_PASSWORD` | Yes* | - | API password for downloading recordings |
| `BANDWIDTH_WEBHOOK_USERNAME` | No | - | HTTP Basic Auth username for webhook |
| `BANDWIDTH_WEBHOOK_PASSWORD` | No | - | HTTP Basic Auth password for webhook |
| `VALIDATE_BANDWIDTH_WEBHOOK` | No | `false` | Enable HTTP Basic Auth validation |

\* Required when `DOWNLOAD_RECORDINGS=true`

### Configuring Bandwidth

1. In the Bandwidth Dashboard, configure your Voice Application.

2. Set the Callback URL for recording events:
   ```
   https://your-domain.com/webhook/recording
   ```

3. Optionally configure HTTP Basic Authentication credentials.

---

## Common Configuration

All adapters share these common configuration options:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONSERVER_URL` | Yes | - | URL of your vCon conserver endpoint |
| `CONSERVER_API_TOKEN` | No | - | API token for conserver authentication |
| `CONSERVER_HEADER_NAME` | No | `x-conserver-api-token` | Header name for API token |
| `HOST` | No | `0.0.0.0` | Host to bind the server to |
| `PORT` | No | `8080` | Port to run the server on |
| `DOWNLOAD_RECORDINGS` | No | `true` | Download and embed recording audio |
| `RECORDING_FORMAT` | No | `wav` | Recording format (wav or mp3) |
| `INGRESS_LISTS` | No | - | Comma-separated routing lists for conserver |
| `STATE_FILE` | No | `.{adapter}_state.json` | State tracking file |
| `LOG_LEVEL` | No | `INFO` | Logging level |

## HTTP endpoints (webhook adapters only)

Pollers and the Pipecat observer don't bind an HTTP server. The webhook adapters do — most use a common endpoint shape, VAPI is the exception.

**Twilio, FreeSWITCH, Asterisk, Telnyx, Bandwidth** expose:

- `POST /webhook/recording` — receives recording events; always returns `200 OK` with body `"OK"`
- `GET /health` — health check; returns `{"status": "healthy", "service": "vcon-{platform}-adapter"}`
- `GET /status/{recording_id}` — processing status; returns `{"recording_id": ..., "vcon_uuid": ..., "status": ...}`

**VAPI** is shaped slightly differently:

- `POST /vapi` — receives end-of-call reports; returns `{"status": "ok", "uuid": ...}` on success, `{"status": "ignored"}` for non-end-of-call messages
- `GET /healthz` — health check; returns `{"status": "ok"}`

## vCon Structure

All adapters produce spec-compliant vCons (IETF `draft-ietf-vcon-vcon-core-02`, syntax `0.4.0`). Inline audio uses RFC 4648 `base64url` (URL-safe alphabet, no padding) paired with a `content_hash` over the original bytes; the field is `mediatype` (never `mimetype`); attachments use `purpose` (never `type`) and carry `party` + `dialog` indices.

```json
{
    "vcon": "0.4.0",
    "uuid": "auto-generated-uuid",
    "created_at": "2026-05-19T10:30:00+00:00",
    "parties": [
        {"tel": "+15551234567"},
        {"tel": "+15559876543"}
    ],
    "dialog": [
        {
            "type": "recording",
            "start": "2026-05-19T10:30:00+00:00",
            "duration": 120.0,
            "parties": [0, 1],
            "originator": 0,
            "mediatype": "audio/wav",
            "filename": "RE123.wav",
            "body": "base64url-encoded-audio-no-padding",
            "encoding": "base64url",
            "content_hash": "sha512-<base64url-of-sha512-digest>"
        }
    ],
    "attachments": [
        {
            "purpose": "tags",
            "party": 0,
            "dialog": 0,
            "encoding": "json",
            "body": ["source:twilio_adapter", "call_sid:CA123"]
        }
    ],
    "analysis": []
}
```

### Platform-Specific Tags

Each adapter adds a `tags`-purpose attachment with a `source` value and platform-specific metadata.

> The original 5 webhook adapters use the `<platform>_adapter` form for the `source` tag; the 4 newer adapters (SignalWire, ElevenLabs, VAPI, Pipecat) use the bare platform name. This is intentional historical convention; do not "fix" it.

**Twilio** — `source: twilio_adapter`; `recording_sid`, `call_sid`, `account_sid`, `direction`, `recording_source`, caller/called geographic info

**FreeSWITCH** — `source: freeswitch_adapter`; `freeswitch_uuid`, `caller_name`, `account_code`, `freeswitch_context`, `sip_user_agent`

**Asterisk** — `source: asterisk_adapter`; `asterisk_channel`, `asterisk_uniqueid`, `linkedid`, `dialplan_context`, `sip_user_agent`, `asterisk_language`

**Telnyx** — `source: telnyx_adapter`; `telnyx_recording_id`, `telnyx_call_session_id`, `telnyx_call_control_id`, `telnyx_connection_id`, `recording_channels`

**Bandwidth** — `source: bandwidth_adapter`; `bandwidth_recording_id`, `bandwidth_call_id`, `bandwidth_account_id`, `bandwidth_application_id`, `recording_channels`

**SignalWire** — `source: signalwire`; `call_sid`

**ElevenLabs** — `source: elevenlabs`; `conversation_id`, `agent_id`, `status`, `user_tag` (per user-supplied tag)

**VAPI** — `source: vapi`; `call_id`

**Pipecat** — `source: pipecat`; `conversation_id`, `user_tag` (per user-supplied tag)

## Development

### Running tests

```bash
# Run all tests with coverage
pytest

# Run tests for a specific adapter
pytest tests/adapters/twilio/
pytest tests/adapters/freeswitch/
pytest tests/adapters/asterisk/
pytest tests/adapters/telnyx/
pytest tests/adapters/bandwidth/
pytest tests/adapters/signalwire/
pytest tests/adapters/elevenlabs/
pytest tests/adapters/vapi/
pytest tests/adapters/pipecat/

# Just the cross-adapter spec compliance smoke tests
pytest tests/test_spec_compliance.py

# Verbose
pytest -v
```

The test suite includes 454 tests covering all adapters: configuration, builder, webhook/poller/observer, and a cross-adapter spec-compliance contract in [`tests/test_spec_compliance.py`](tests/test_spec_compliance.py). New adapters must keep that file green. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full checklist of spec-correct vCon construction.

### Code formatting

```bash
black .
ruff check .
```

### Type checking

```bash
mypy core adapters
```

## Adding New Adapters

Pick the ingestion mode that matches how the platform delivers events. Each mode has a reference implementation already in the tree.

### Webhook adapter (platform POSTs to us)

Reference: `adapters/twilio/`

```
adapters/yourplatform/
├── __init__.py
├── config.py        # Extend BaseConfig
├── builder.py       # Subclass BaseRecordingData + BaseVconBuilder; implement _download_recording()
└── webhook.py       # create_app(config) returning a FastAPI instance
```

### Poller adapter (we hit the platform API on an interval)

Reference: `adapters/signalwire/`

```
adapters/yourplatform/
├── __init__.py
├── config.py        # @dataclass with .from_env(); no need to extend BaseConfig
├── builder.py       # Plain class with build(data) -> Vcon; uses lib helpers directly
└── poller.py        # Long-running loop; uses core.poster.HttpPoster for delivery
```

### Frame observer (in-process integration with an agent framework)

Reference: `adapters/pipecat/`

```
adapters/yourplatform/
├── __init__.py
├── builder.py       # Builds vCon from accumulated ConversationState
├── observer.py      # Hooks into the host framework's event/frame loop
└── README.md        # Document the integration pattern — this is the user-facing interface
```

### Common requirements (all modes)

- Route vCon construction through the [`vcon`](https://pypi.org/project/vcon/) library helpers (`add_party`, `add_dialog`, `add_attachment`, `add_analysis`, `add_tag`) — don't write to `vcon_dict[...]` directly. See [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Keep [`tests/test_spec_compliance.py`](tests/test_spec_compliance.py) green; add a `tests/adapters/<platform>/` subdirectory for platform-specific tests.
- Register webhook + poller adapters in `main.py`'s `ADAPTERS` dict (frame observers are libraries, not processes — leave them out).

## Deployment

### Docker

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install from PyPI (pulls in vcon>=0.9.4 automatically)
RUN pip install vcon-telephony-adapters

EXPOSE 8080
CMD ["vcon-adapter", "twilio"]
```

### Docker Compose (Multiple Adapters)

```yaml
version: '3.8'
services:
  twilio-adapter:
    build: .
    ports:
      - "8080:8080"
    environment:
      - CONSERVER_URL=https://your-conserver.example.com/api/vcons
      - TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
      - TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
    command: ["vcon-adapter", "twilio"]

  telnyx-adapter:
    build: .
    ports:
      - "8084:8084"
    environment:
      - CONSERVER_URL=https://your-conserver.example.com/api/vcons
      - TELNYX_API_KEY=${TELNYX_API_KEY}
      - PORT=8084
    command: ["vcon-adapter", "telnyx"]

  bandwidth-adapter:
    build: .
    ports:
      - "8085:8085"
    environment:
      - CONSERVER_URL=https://your-conserver.example.com/api/vcons
      - BANDWIDTH_ACCOUNT_ID=${BANDWIDTH_ACCOUNT_ID}
      - BANDWIDTH_USERNAME=${BANDWIDTH_USERNAME}
      - BANDWIDTH_PASSWORD=${BANDWIDTH_PASSWORD}
      - PORT=8085
    command: ["vcon-adapter", "bandwidth"]
```

### Production Checklist

1. Enable webhook validation for your platform
2. Store credentials securely (e.g., in a secrets manager)
3. Use HTTPS for all endpoints
4. Configure a persistent volume for the state file
5. Set appropriate `LOG_LEVEL` (e.g., `WARNING` or `ERROR` for production)
6. Consider running multiple adapter instances behind a load balancer

## License

MIT
