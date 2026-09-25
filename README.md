# vCon Telephony Adapters

[![PyPI version](https://badge.fury.io/py/vcon-telephony-adapters.svg)](https://pypi.org/project/vcon-telephony-adapters/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A monorepo of webhook-based adapters that convert telephony platform recordings into vCon (Virtual Conversation) format and post them to a vCon conserver.

## Overview

This project provides a unified framework for converting call recordings from various telephony platforms into the standardized vCon format:

- **Twilio** - Convert Twilio call recordings via webhooks
- **FreeSWITCH** - Convert FreeSWITCH recordings via mod_http_cache events
- **Asterisk** - Convert Asterisk recordings via ARI events
- **Telnyx** - Convert Telnyx call recordings via webhooks
- **Bandwidth** - Convert Bandwidth call recordings via webhooks

## Architecture

```
vcon-telephony-adapters/
├── core/                    # Shared core modules
│   ├── base_builder.py      # Abstract vCon builder
│   ├── base_config.py       # Base configuration
│   ├── poster.py            # HTTP posting to conserver
│   └── tracker.py           # State tracking for duplicates
├── adapters/                # Platform-specific adapters
│   ├── twilio/              # Twilio adapter
│   ├── freeswitch/          # FreeSWITCH adapter
│   ├── asterisk/            # Asterisk adapter
│   ├── telnyx/              # Telnyx adapter
│   └── bandwidth/           # Bandwidth adapter
├── twilio_adapter/          # Backwards compatibility layer
├── tests/                   # Test suite
└── main.py                  # CLI entry point
```

## Requirements

- Python 3.12+
- [vcon](https://pypi.org/project/vcon/) - The vCon library for creating vCon objects (declared as a dependency, installed automatically)

## Installation

### From PyPI (Recommended)

```bash
pip install vcon-telephony-adapters
```

The `vcon` library is a declared dependency and installs automatically.

### From Source

```bash
git clone https://github.com/vcon-dev/vcon-telephony-adapters.git
cd vcon-telephony-adapters
pip install .
```

### Optional extras

```bash
# S3 media publishing (adds boto3; only needed with MEDIA_BACKEND=s3)
pip install ".[s3]"

# Development / test tooling
pip install ".[dev]"

# Everything
pip install ".[all]"
```

## Quick Start

### Running an adapter

```bash
# Twilio
vcon-adapter twilio

# FreeSWITCH
vcon-adapter freeswitch

# Asterisk
vcon-adapter asterisk

# Telnyx
vcon-adapter telnyx

# Bandwidth
vcon-adapter bandwidth
```

---

## Twilio Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here

# Optional
PORT=8080
VALIDATE_TWILIO_SIGNATURE=true
WEBHOOK_URL=https://your-domain.com
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Twilio Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TWILIO_ACCOUNT_SID` | Yes* | - | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Yes** | - | Your Twilio Auth Token |
| `VALIDATE_TWILIO_SIGNATURE` | No | `true` | Validate webhook signatures |
| `WEBHOOK_URL` | No | - | Public URL for signature validation |

\* Required when `DOWNLOAD_RECORDINGS=true`.
\** Required when `VALIDATE_TWILIO_SIGNATURE=true` (the default): the adapter refuses to
start otherwise, rather than accept unauthenticated webhooks. See
[Webhook authentication](#webhook-authentication) below for the escape hatch.

### Configuring Twilio

1. In your Twilio console, configure your phone number or TwiML app to record calls.

2. Set the Recording Status Callback URL to your adapter endpoint:
   ```
   https://your-domain.com/webhook/recording
   ```

3. Select the recording status events you want to receive (at minimum, `completed`).

### Using with TwiML

```xml
<Response>
    <Record
        recordingStatusCallback="https://your-domain.com/webhook/recording"
        recordingStatusCallbackEvent="completed"
    />
</Response>
```

---

## FreeSWITCH Adapter

### Configuration

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons

# FreeSWITCH-specific
FREESWITCH_HOST=localhost
FREESWITCH_ESL_PORT=8021
# Required if/when the ESL path is used; no stock default (FreeSWITCH ships
# with "ClueCon", which this adapter deliberately does not default to)
FREESWITCH_ESL_PASSWORD=your_esl_password
FREESWITCH_RECORDINGS_PATH=/var/lib/freeswitch/recordings
FREESWITCH_RECORDINGS_URL_BASE=https://fs.example.com/recordings
FREESWITCH_WEBHOOK_SECRET=your_webhook_secret

# Optional
PORT=8082
VALIDATE_FREESWITCH_WEBHOOK=true
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### FreeSWITCH Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `FREESWITCH_HOST` | No | `localhost` | FreeSWITCH host |
| `FREESWITCH_ESL_PORT` | No | `8021` | ESL port |
| `FREESWITCH_ESL_PASSWORD` | No* | - | ESL password; no default (required if/when the ESL path is used) |
| `FREESWITCH_RECORDINGS_PATH` | No | `/var/lib/freeswitch/recordings` | Local recordings path |
| `FREESWITCH_RECORDINGS_URL_BASE` | No | - | URL base for HTTP downloads |
| `FREESWITCH_WEBHOOK_SECRET` | Yes** | - | HMAC secret for webhook validation |
| `VALIDATE_FREESWITCH_WEBHOOK` | No | `true` | Enable webhook signature validation |

\** Required when `VALIDATE_FREESWITCH_WEBHOOK=true` (the default): the adapter refuses
to start otherwise. See [Webhook authentication](#webhook-authentication) below.

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
VALIDATE_ASTERISK_WEBHOOK=true
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
| `ASTERISK_WEBHOOK_SECRET` | Yes* | - | HMAC secret for webhook validation |
| `VALIDATE_ASTERISK_WEBHOOK` | No | `true` | Enable webhook signature validation |

\* Required when `VALIDATE_ASTERISK_WEBHOOK=true` (the default): the adapter refuses to
start otherwise. See [Webhook authentication](#webhook-authentication) below.

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
VALIDATE_TELNYX_WEBHOOK=true
TELNYX_PUBLIC_KEY=your_public_key_for_signature_validation
# Protects /webhook/texml-recording (see "Smart trunk TeXML callback" below)
TELNYX_TEXML_CALLBACK_TOKEN=a_long_random_shared_secret
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Telnyx Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELNYX_API_KEY` | Yes* | - | Telnyx API key for downloading recordings |
| `TELNYX_PUBLIC_KEY` | Yes** | - | Public key for Call Control webhook signature validation |
| `TELNYX_TEXML_CALLBACK_TOKEN` | Yes** | - | Shared-secret token that protects `/webhook/texml-recording` |
| `VALIDATE_TELNYX_WEBHOOK` | No | `true` | Enable webhook validation (both Call Control signatures and the TeXML token) |

\* Required when `DOWNLOAD_RECORDINGS=true`.
\** Required when `VALIDATE_TELNYX_WEBHOOK=true` (the default): the adapter refuses to
start otherwise. See [Webhook authentication](#webhook-authentication) below. The
`cryptography` package is also a hard requirement whenever validation is on, since it is
what verifies the Ed25519 Call Control signature.

### Configuring Telnyx

1. In the Telnyx Mission Control Portal, configure your Call Control Application.

2. Set the Webhook URL for recording events:
   ```
   https://your-domain.com/webhook/recording
   ```

3. Enable `call.recording.saved` events.

#### Smart trunk TeXML callback

Telnyx's TeXML `recordingStatusCallback` (path B of the smart trunk, handled at
`/webhook/texml-recording`) is a Twilio-compatible form POST with no Ed25519 signature of
its own, unlike the Call Control JSON webhooks above. It is gated instead with a
shared-secret token compared using a constant-time comparison
(`hmac.compare_digest`). Set `TELNYX_TEXML_CALLBACK_TOKEN` to a long random value and
configure the callback URL with it as a query parameter:

```
https://your-domain.com/webhook/texml-recording?token=<TELNYX_TEXML_CALLBACK_TOKEN>
```

With `VALIDATE_TELNYX_WEBHOOK=true` (the default), the adapter refuses to start unless
this token is set.

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
VALIDATE_BANDWIDTH_WEBHOOK=true
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
| `BANDWIDTH_WEBHOOK_USERNAME` | Yes** | - | HTTP Basic Auth username for webhook |
| `BANDWIDTH_WEBHOOK_PASSWORD` | Yes** | - | HTTP Basic Auth password for webhook |
| `VALIDATE_BANDWIDTH_WEBHOOK` | No | `true` | Enable HTTP Basic Auth validation |

\* Required when `DOWNLOAD_RECORDINGS=true`.
\** Both required when `VALIDATE_BANDWIDTH_WEBHOOK=true` (the default): the adapter
refuses to start otherwise. See [Webhook authentication](#webhook-authentication) below.

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
| `ALLOW_UNSIGNED_WEBHOOKS` | No | `false` | See [Webhook authentication](#webhook-authentication) |

## Webhook authentication

Every adapter validates its incoming webhooks by default (`VALIDATE_*_WEBHOOK=true`):
an HMAC secret for Asterisk/FreeSWITCH, Twilio's own signature scheme, Telnyx's Ed25519
Call Control signature plus a token on the TeXML callback, or HTTP Basic Auth for
Bandwidth, depending on the platform.

If validation is enabled but the corresponding secret/key/credentials are not
configured, **the adapter refuses to start** rather than silently accepting
unauthenticated webhooks. This is deliberate: a webhook receiver that fails open when
misconfigured is a receiver anyone can post fabricated recording events to.

To run an adapter without webhook authentication anyway (a lab box, a local demo, a
platform that genuinely offers no way to verify these particular requests), set:

```bash
ALLOW_UNSIGNED_WEBHOOKS=true
```

This is a loud, explicit opt-out: it logs a warning at startup and again wherever a
secret is actually missing, but it lets every adapter start and accept unauthenticated
webhooks. Do not set this in production.

## API Endpoints

All adapters expose the same endpoint structure:

### `POST /webhook/recording`

Receives recording events from the telephony platform.

**Response**: Always returns `200 OK` with body `"OK"` to acknowledge receipt.

### `GET /health`

Health check endpoint.

**Response**:
```json
{
    "status": "healthy",
    "service": "vcon-{platform}-adapter"
}
```

### `GET /status/{recording_id}`

Get the processing status for a specific recording.

**Response** (200):
```json
{
    "recording_id": "rec-xxxxx",
    "vcon_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "status": "success"
}
```

## vCon Structure

All adapters create vCons with this structure:

```json
{
    "vcon": "0.0.1",
    "uuid": "auto-generated-uuid",
    "created_at": "2025-01-21T10:30:00+00:00",
    "parties": [
        {"tel": "+15551234567"},
        {"tel": "+15559876543"}
    ],
    "dialog": [
        {
            "type": "recording",
            "start": "2025-01-21T10:30:00+00:00",
            "duration": 120.0,
            "parties": [0, 1],
            "originator": 0,
            "mimetype": "audio/wav",
            "body": "base64-encoded-audio-data",
            "encoding": "base64"
        }
    ]
}
```

### Platform-Specific Tags

Each adapter adds metadata tags to the vCon:

**Twilio:**
- `source`: `twilio_adapter`
- `recording_sid`, `call_sid`, `account_sid`
- `direction`, `recording_source`
- Caller/called geographic info

**FreeSWITCH:**
- `source`: `freeswitch_adapter`
- `freeswitch_uuid`, `caller_name`
- `account_code`, `freeswitch_context`
- `sip_user_agent`

**Asterisk:**
- `source`: `asterisk_adapter`
- `asterisk_channel`, `asterisk_uniqueid`
- `linkedid`, `dialplan_context`
- `sip_user_agent`, `asterisk_language`

**Telnyx:**
- `source`: `telnyx_adapter`
- `telnyx_recording_id`, `telnyx_call_session_id`
- `telnyx_call_control_id`, `telnyx_connection_id`
- `recording_channels`

**Bandwidth:**
- `source`: `bandwidth_adapter`
- `bandwidth_recording_id`, `bandwidth_call_id`
- `bandwidth_account_id`, `bandwidth_application_id`
- `recording_channels`

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

# Run with verbose output
pytest -v
```

The test suite includes 157+ tests covering all adapters with configuration, builder, and webhook tests.

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

To add support for a new telephony platform:

1. Create a new directory in `adapters/`:
   ```
   adapters/
   └── yourplatform/
       ├── __init__.py
       ├── config.py      # Extend BaseConfig
       ├── builder.py     # Extend BaseVconBuilder
       └── webhook.py     # FastAPI endpoints
   ```

2. Implement the platform-specific recording data class by extending `BaseRecordingData`

3. Implement the builder by extending `BaseVconBuilder` and implementing `_download_recording()`

4. Create the webhook endpoints in a `create_app()` function

5. Register the adapter in `main.py`

## Deployment

### Running in Docker

The repo ships a single `Dockerfile`. The adapter name is the container's
command (see `main.py`), so one image serves every adapter:

```bash
docker build -t vcon-telephony-adapters .

docker run --rm -d \
  --name vcon-twilio \
  -p 8080:8080 \
  --env-file .env \
  vcon-telephony-adapters twilio
```

The image runs as a non-root user and exposes a `HEALTHCHECK` against
`GET /health` on `PORT` (default `8080`):

```bash
curl http://localhost:8080/health
# {"status": "healthy", "service": "vcon-telephony-adapters-twilio"}
```

`CONSERVER_URL` is required (see `.env.example`); the container exits at
startup without it.

### Docker Compose (Multiple Adapters)

Copy `compose.example.yaml` to `compose.yaml` and `.env.example` to `.env`,
then start the adapters you need:

```bash
docker compose up twilio
```

Each service in the example file builds the same image and only differs by
`command` (the adapter name) and the `PORT` it publishes.

### Publishing images

`.github/workflows/docker-publish.yml` builds and pushes
`ghcr.io/<org>/vcon-telephony-adapters` on any `v*` tag push. It is not
wired to run outside of a tag push, and no tag is pushed as part of this
change.

### Production Checklist

1. Enable webhook validation for your platform
2. Store credentials securely (e.g., in a secrets manager)
3. Use HTTPS for all endpoints
4. Configure a persistent volume for the state file
5. Set appropriate `LOG_LEVEL` (e.g., `WARNING` or `ERROR` for production)
6. Consider running multiple adapter instances behind a load balancer

## License

MIT
