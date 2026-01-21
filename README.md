# vCon Twilio Adapter

A webhook-based adapter that converts Twilio call recordings into vCon (Virtual Conversation) format and posts them to a vCon conserver.

## Overview

This adapter:
- Receives Twilio recording status webhooks when call recordings are completed
- Downloads the recording audio from Twilio (optional)
- Creates a vCon containing the recording, participant information, and metadata
- Posts the vCon to a conserver endpoint
- Tracks processed recordings to prevent duplicates

## Requirements

- Python 3.10+
- [vcon-lib](https://github.com/vcon-dev/vcon-lib) - The vCon library for creating vCon objects

## Installation

### Prerequisites

First, install the vcon library:

```bash
pip install git+https://github.com/vcon-dev/vcon-lib.git
```

Or if you have a local copy:

```bash
pip install /path/to/vcon-lib
```

### Using pip

```bash
pip install -e .
```

### Development installation

```bash
pip install -e ".[dev]"
```

## Configuration

1. Copy the example environment file:

```bash
cp .env.example .env
```

2. Edit `.env` with your configuration:

```bash
# Required
CONSERVER_URL=https://your-conserver.example.com/api/vcons
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token_here

# Optional
PORT=8080
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
```

### Configuration Options

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `CONSERVER_URL` | Yes | - | URL of your vCon conserver endpoint |
| `CONSERVER_API_TOKEN` | No | - | API token for conserver authentication |
| `CONSERVER_HEADER_NAME` | No | `x-conserver-api-token` | Header name for API token |
| `TWILIO_ACCOUNT_SID` | Yes* | - | Your Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Yes* | - | Your Twilio Auth Token |
| `HOST` | No | `0.0.0.0` | Host to bind the server to |
| `PORT` | No | `8080` | Port to run the server on |
| `VALIDATE_TWILIO_SIGNATURE` | No | `true` | Validate webhook signatures |
| `WEBHOOK_URL` | No | - | Public URL for signature validation |
| `DOWNLOAD_RECORDINGS` | No | `true` | Download and embed recording audio |
| `RECORDING_FORMAT` | No | `wav` | Recording format (wav or mp3) |
| `INGRESS_LISTS` | No | - | Comma-separated routing lists for conserver |
| `STATE_FILE` | No | `.twilio_adapter_state.json` | State tracking file |
| `LOG_LEVEL` | No | `INFO` | Logging level |

\* Required when `DOWNLOAD_RECORDINGS=true` or `VALIDATE_TWILIO_SIGNATURE=true`

## Usage

### Running the server

```bash
# Using the entry point
vcon-twilio-adapter

# Or directly with Python
python main.py
```

The server will start and listen for Twilio webhooks on `/webhook/recording`.

### Configuring Twilio

1. In your Twilio console, configure your phone number or TwiML app to record calls.

2. Set the Recording Status Callback URL to your adapter endpoint:
   ```
   https://your-domain.com/webhook/recording
   ```

3. Select the recording status events you want to receive (at minimum, `completed`).

### Using with TwiML

When recording calls with TwiML, add the `recordingStatusCallback` attribute:

```xml
<Response>
    <Record
        recordingStatusCallback="https://your-domain.com/webhook/recording"
        recordingStatusCallbackEvent="completed"
    />
</Response>
```

Or when using the REST API to create calls:

```python
from twilio.rest import Client

client = Client(account_sid, auth_token)
call = client.calls.create(
    to="+1234567890",
    from_="+0987654321",
    url="https://your-twiml-url.com/voice",
    record=True,
    recording_status_callback="https://your-domain.com/webhook/recording",
    recording_status_callback_event=["completed"]
)
```

## API Endpoints

### `POST /webhook/recording`

Receives Twilio recording status callbacks. This endpoint accepts form-encoded POST data from Twilio.

**Response**: Always returns `200 OK` with body `"OK"` to acknowledge receipt (prevents Twilio retries).

### `GET /health`

Health check endpoint.

**Response**:
```json
{
    "status": "healthy",
    "service": "vcon-twilio-adapter"
}
```

### `GET /status/{recording_sid}`

Get the processing status for a specific recording.

**Response** (200):
```json
{
    "recording_sid": "RExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "vcon_uuid": "550e8400-e29b-41d4-a716-446655440000",
    "status": "success"
}
```

**Response** (404 - not found):
```json
{
    "detail": "Recording RExxxxxxxx not found"
}
```

## vCon Structure

The adapter creates vCons with the following structure:

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

### Tags

The adapter adds metadata tags to each vCon:

| Tag | Description |
|-----|-------------|
| `source` | Always `twilio_adapter` |
| `recording_sid` | Twilio Recording SID |
| `call_sid` | Twilio Call SID |
| `account_sid` | Twilio Account SID |
| `direction` | Call direction (`inbound`, `outbound`, `outbound-api`) |
| `recording_source` | Recording source (e.g., `RecordVerb`) |
| `duration_seconds` | Recording duration in seconds |
| `caller_city`, `caller_state`, `caller_country` | Caller geographic info (if available) |
| `called_city`, `called_state`, `called_country` | Called party geographic info (if available) |

## Development

### Running tests

```bash
# Run all tests with coverage
pytest

# Run specific test file
pytest tests/test_builder.py

# Run with verbose output
pytest -v
```

The test suite includes 247 tests with 98% code coverage:
- `test_config.py` - Configuration management tests
- `test_builder.py` - vCon building tests
- `test_tracker.py` - State tracking tests
- `test_poster.py` - HTTP posting tests
- `test_webhook.py` - FastAPI webhook endpoint tests

### Code formatting

```bash
black .
ruff check .
```

### Type checking

```bash
mypy twilio_adapter
```

## Architecture

```
twilio_adapter/
├── __init__.py
├── config.py      # Configuration management
├── builder.py     # vCon construction from Twilio data
├── poster.py      # HTTP posting to conserver
├── tracker.py     # State tracking for duplicate prevention
└── webhook.py     # FastAPI webhook endpoints
```

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY . .

# Install vcon library (adjust path or use git URL)
RUN pip install git+https://github.com/vcon-dev/vcon-lib.git
RUN pip install -e .

EXPOSE 8080
CMD ["vcon-twilio-adapter"]
```

### Docker Compose

```yaml
version: '3.8'
services:
  twilio-adapter:
    build: .
    ports:
      - "8080:8080"
    environment:
      - CONSERVER_URL=https://your-conserver.example.com/api/vcons
      - CONSERVER_API_TOKEN=${CONSERVER_API_TOKEN}
      - TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}
      - TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}
      - VALIDATE_TWILIO_SIGNATURE=true
      - WEBHOOK_URL=https://your-public-domain.com/webhook/recording
    volumes:
      - ./state:/app/state
```

### Production Checklist

1. Set `VALIDATE_TWILIO_SIGNATURE=true` to prevent unauthorized webhook calls
2. Set `WEBHOOK_URL` to your public endpoint URL for signature validation
3. Store Twilio credentials securely (e.g., in a secrets manager)
4. Use HTTPS for all endpoints
5. Configure a persistent volume for the state file if running in containers
6. Set appropriate `LOG_LEVEL` (e.g., `WARNING` or `ERROR` for production)

## License

MIT
