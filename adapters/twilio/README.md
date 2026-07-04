# Twilio Adapter — Twilio Console Setup

Multi-mode vCon ingress for Twilio. Base URL examples use `https://adapter.example.com`.

## Voice (PSTN / SIP / WebRTC)

| Console location | Setting | Value |
|------------------|---------|-------|
| Phone number → Voice | A call comes in | Your TwiML app or `<Record>` TwiML |
| Phone number → Voice | Recording status callback | `https://adapter.example.com/webhook/recording` |
| Phone number → Voice | Status callback URL | `https://adapter.example.com/webhook/voice/status` |
| TwiML `<Record>` | `recordingStatusCallbackEvent` | `completed` |

Demo endpoint for quick testing: `GET/POST /webhook/demo` returns TwiML that records to `/webhook/recording`.

## Messaging (SMS, MMS, WhatsApp, RCS)

| Console location | Setting | Value |
|------------------|---------|-------|
| Phone number → Messaging | A message comes in | `https://adapter.example.com/webhook/messaging` |
| Messaging Service | Inbound request URL | `https://adapter.example.com/webhook/messaging` |
| WhatsApp sender | When a message comes in | Same URL |

Each inbound message produces one vCon with a `text` dialog. MMS media is downloaded when `DOWNLOAD_MESSAGING_MEDIA=true`.

## Fax

| Console location | Setting | Value |
|------------------|---------|-------|
| Fax-enabled number | Status callback | `https://adapter.example.com/webhook/fax` |

## Video

| Console location | Setting | Value |
|------------------|---------|-------|
| Video room / composition | Status callback URL | `https://adapter.example.com/webhook/video` |

Set `StatusCallbackEvent` to include `composition-available` or `recording-completed`.

## Conversations API

| Console location | Setting | Value |
|------------------|---------|-------|
| Conversations Service → Webhooks | Post-event URL | `https://adapter.example.com/webhook/conversations` |
| Events | onMessageAdded (minimum) | enabled |

Conversations webhooks POST JSON. Signature validation applies to form-encoded webhooks; JSON payloads are accepted when signature validation is disabled or when using a reverse proxy that validates upstream.

## Environment

Copy [`.env.example`](../../.env.example) to `.env`. Disable modes you do not use:

```bash
TWILIO_ENABLE_FAX=false
TWILIO_ENABLE_VIDEO=false
```

## Tests

```bash
pytest tests/adapters/twilio_adapter/ -v
```

Sample payloads: `tests/fixtures/twilio/`.
