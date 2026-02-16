# vCon Telephony Adapters

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

- Python 3.10+
- [vcon-lib](https://github.com/vcon-dev/vcon-lib) - The vCon library for creating vCon objects

## Installation

### Prerequisites

First, install the vcon library:

```bash
pip install git+https://github.com/vcon-dev/vcon-lib.git
```

### Using pip

```bash
pip install -e .
```

### Development installation

```bash
pip install -e ".[dev]"
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
| `TWILIO_AUTH_TOKEN` | Yes* | - | Your Twilio Auth Token |
| `VALIDATE_TWILIO_SIGNATURE` | No | `true` | Validate webhook signatures |
| `WEBHOOK_URL` | No | - | Public URL for signature validation |

\* Required when `DOWNLOAD_RECORDINGS=true` or `VALIDATE_TWILIO_SIGNATURE=true`

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

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

# Install vcon library
RUN pip install git+https://github.com/vcon-dev/vcon-lib.git
RUN pip install -e .

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
