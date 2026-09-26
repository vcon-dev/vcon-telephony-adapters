# Smart trunk, bring your own key

An experiment in a product shape: the customer keeps their own Telnyx account and
gives us an API key. Their carriage, numbers, and bill stay theirs.

We never sit in the media path. If we are down, their calls still complete and we
lose recordings for the outage window. That asymmetry is the point.

## Two ingest paths, one product

The same vCon comes out either way. The difference is entirely how the audio and
the call metadata reach us.

| Path | How audio arrives | Status |
|---|---|---|
| **API** | Telnyx records the call and we fetch it after `call.recording.saved` | **Plan of record since 2026-09-09.** Start here. |
| **SIPREC** | Telnyx forks live media to a Session Recording Server we operate | Parked. See the bottom of this file. |

The API path became the default once the goal was stated as self-serve. An SRS
needs a stable address and a reserved port range per tenant, which forces a host
per customer. Take the SRS out and one stateless HTTPS service serves everybody,
which is the difference between "book a call with us" and "paste a key."

The API path also reaches more customers. SIPREC needs a `call_control_id`, so it
excludes plain SIP connections. Recording is configured per number or outbound
profile and does not require a Call Control application, so those recordings
arrive through the same Recordings API and webhooks regardless.

## The API path

```
customer's Telnyx account              our service
  │
  ├── call.answered / .hangup ────────> POST /webhook/call      party identity, timing,
  │                                                             disposition, quality
  └── call.recording.saved ───────────> POST /webhook/recording  fetch audio, re-host,
                                                │                build vCon
                                                └──> conserver ──> vCon
```

No SRS, no fork, no RTP. Both routes live in `webhook.py`.

```bash
export TELNYX_API_KEY=<the customer's key>
export CONSERVER_URL=https://conserver.example.com
export MEDIA_BACKEND=s3                 # see "Keep the vCons thin", not optional
export LAWFUL_BASIS=legitimate_interests

vcon-adapter telnyx
```

Point the customer's webhook at **both** `/webhook/call` and `/webhook/recording`.

`TELNYX_AUTO_SIPREC` stays unset. Forking is off unless it is explicitly on,
because forking someone's calls should not start happening because a variable was
unset. With it off, `create_app` never builds the provisioner (`webhook.py:123`),
so `/webhook/call` accumulates session state and returns, and nothing touches the
media path.

The API key is still required, but **only for `api.telnyx.com`**: the authoritative
party-identity lookup described below (`webhook.py:37`). It must never reach the
recording URL itself. See "The key does not go to the recording URL".

## What a live call established

Two PSTN calls on 2026-09-09 proved the loop end to end and answered the questions
CON-844 was opened to ask. The payload facts below are measured, not read off the
docs, and several of them contradicted the docs.

| Question | Answer |
|---|---|
| Recording URL lifetime | Pre-signed, **600 seconds** |
| Does the fetch need the API key? | **No, and sending it breaks the fetch.** See below. |
| Format | `audio/wav`, dual channel (596,524 bytes on the test call) |
| Does `call.recording.saved` name the parties? | No. `from`, `to` and `direction` are all absent. |

### The key does not go to the recording URL

Recording URLs are pre-signed S3, not Telnyx-hosted. The builder used to attach
`Authorization: Bearer` to every download, which was two bugs in one line:

| Request | Result |
|---|---|
| With the key | **400**, S3 error XML |
| Without the key | **200**, `audio/wav`, 596,524 bytes |

S3 rejects a request carrying both a pre-signed signature and an Authorization
header, so every Telnyx recording download was failing. And under BYOK that token is
the customer's key, so it was being handed to `s3.amazonaws.com`.

Fixed by allowlisting the host: the header is attached only when the URL is on
`api.telnyx.com` (`builder.py:328`, `TELNYX_API_HOSTS`). Do not widen that allowlist
without re-reading this section.

### Two payload fields that do not mean what they look like

- **`duration_millis` does not exist** on the webhook payload. It carries
  `recording_started_at` and `recording_ended_at`, and duration is derived from them.
  Before this was found, duration was `None` on every call.
- **`payload.start_time` is overloaded.** It means *call* start on lifecycle events
  and *recording* start on the recording event, so folding one into the other produces
  a call that started after it was answered. The real answer time is the envelope's
  `occurred_at`. `payload.start_time` is byte-identical across `initiated`, `answered`
  and `hangup`, so it cannot be an answer time, and using it made ring duration always
  zero.

### Still open after those calls

The download fix is verified live (200, 596,524 bytes). The **re-hosting publish path
is not**: it was verified against the real captured payload but with synthetic audio,
because the pre-signed URL expired before it could be re-fetched. One more call closes
that gap.

## Party identity needs the lifecycle events

`call.recording.saved` names nobody: no `from`, `to`, or `direction`. Point the
customer's webhook at `/webhook/call` as well and the adapter correlates the two
by `call_session_id`.

Two layers, on purpose:

- **`CallSessionStore`** accumulates the lifecycle events in memory: parties,
  direction, ring and answer timing, hangup cause, SIP headers, negotiated
  codec, carrier MOS. A restart loses it.
- **The recordings API** is consulted when no session is found, and returns
  `from`, `to` and duration authoritatively. Stateless, so party identity
  survives a restart, a dropped webhook, or a replay.

Losing enrichment on a restart is acceptable. Losing party identity is not, which
is why only the latter has a stateless fallback.

Signalling detail lands in a `sip-message-trace` attachment under the
`sip-signaling` extension, the same shape the SIPREC SRS emits, so a vCon sourced
from either path describes a call identically.

## Keep the vCons thin

By default the adapter inlines audio as base64url, which makes a vCon roughly 1.3x
the size of the recording. A twenty-second call becomes an 800 KB JSON object; an
hour-long one becomes unusable. Re-host instead and the dialog carries a `url`
plus a `content_hash` — about **800x smaller**, and still verifiable.

```bash
MEDIA_BACKEND=s3
MEDIA_S3_BUCKET=vconic-media
MEDIA_S3_PREFIX=telnyx
MEDIA_S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com   # any S3-compatible store
MEDIA_BASE_URL=https://vconic-media.nyc3.digitaloceanspaces.com
```

`MEDIA_BACKEND=filesystem` with `MEDIA_FILESYSTEM_PATH` is the local equivalent.
S3 needs `boto3`, which is not a base dependency.

Measured on real Telnyx audio: a 575,404-byte dual-channel WAV went from an
811,574-byte vCon to **1,009 bytes**, an 804x reduction, with the `content_hash`
recomputed from the re-hosted file and matching, and `is_valid()` true.

**Re-hosting is not optional for Telnyx.** Recording URLs are pre-signed and
expire in 600 seconds, so a vCon that references the Telnyx URL directly has a
dead audio link within ten minutes. The audio must be fetched and re-hosted inside
that window, which also makes the fetch a hard deadline for the whole pipeline
rather than something that can be retried tomorrow.

## Lawful basis

Every vCon should record why the deployment is entitled to hold the recording.
Unset by default, and **never invented**: a fabricated consent record asserts
something about a data subject that nobody established, which is worse than no
record at all. Leave it unset and the attachment is absent and a warning is
logged; it is not quietly filled in.

```bash
LAWFUL_BASIS=legitimate_interests   # or consent | contract | legal_obligation
                                    #    | vital_interests | public_task
LAWFUL_BASIS_PURPOSES=recording,transcription,analysis
LAWFUL_BASIS_EXPIRATION=            # ISO 8601, or blank for indefinite
LAWFUL_BASIS_JUSTIFICATION="Telnyx BYOK trunk; the Data Controller manages data-subject consent separately."
```

An invalid value fails at startup rather than silently producing basis-less vCons
for a month.

Which basis is correct is a legal question for the deployment, not a default we
can pick. `legitimate_interests` suits carrier-side recording where the customer
is the Data Controller and manages consent themselves; `consent` suits a flow that
actually captures it per call, in which case `consentify` can detect it from the
transcript and record proof.

## Handling the customer's key

It is their key and it controls their telephony account: it can place calls, buy
numbers, and spend their money.

- Never logged. Redacted from every error this module raises (there is a test).
- Never written to disk by this code.
- Sent only to `api.telnyx.com`, never to a pre-signed media URL (`builder.py:328`).
- On the API path the calls we make with it are exactly: read recording metadata. On
  the parked SIPREC path, add list/create/update/delete a SIPREC connector,
  `siprec_start`, `siprec_stop`, and optionally `transcription_start`.

**Telnyx API keys are not scoped.** A key handed to us reads numbers, applications,
connections, profiles, recordings and account balance. BYOK on Telnyx therefore means
asking a customer for full control of their telephony account, and the signup flow has
to say so in those words rather than implying a read-only recordings token. See CON-809
and CON-847.

## Not built yet on the API path

Named here so nobody assumes otherwise from reading the plan.

**Realtime transcription is not wired.** `start_transcription` is only reached from
`handle_call_event` (`provision.py:317`), which runs only when auto-SIPREC is on.
Telnyx delivers `transcription_start` results by webhook rather than by media, so
this works without an SRS, but it needs a call site on the API path first.

**There is no tenant concept.** Config reads one `TELNYX_API_KEY` from the
environment, so this is a single-tenant process today. Per-tenant webhook tokens,
tenant-scoped storage and search, and key custody for N accounts in one place are
the bulk of the remaining work, and where the risk sits. See CON-846 and CON-809.

**The webhook URL strategy is undecided.** A Telnyx connection has one webhook URL.
If provisioning sets it and the customer's own application was already using it, we
break their app. `vcon-telnyx-provision status` already reads the existing URLs and
warns when they are occupied, and nothing in this codebase writes one. Decide the
default before onboarding is built: use the failover slot, have the customer paste
our URL, or accept theirs and forward on after processing. Read before writing, and
refuse rather than clobber. See CON-845.

## Parked: the SIPREC path

Not cancelled, deferred. It unblocks when a customer arrives with an SBC estate and
vendor neutrality is the sale, because RFC 7866 reaches every SBC and the API path
reaches only Telnyx.

```
customer's Telnyx account          our droplet
  │                                   │
  ├── SIPREC connector ───────────────┤ vcon-siprec-adapter  (SRS)
  │      (we create this)             │        │
  └── call.answered webhook ──────────┤        └──> conserver -> vCon
                                      │  this adapter starts the fork
```

`provision.py` creates the SIPREC connector in the customer's account and
starts/stops SIPREC per call. `POST /webhook/call` starts the fork on
`call.answered` when `TELNYX_AUTO_SIPREC` is on.

```bash
export TELNYX_API_KEY=<the customer's key>
export SRS_HOST=srs.example.com
export SRS_PORT=5060

vcon-telnyx-provision check         # is the key valid and does it have access?
vcon-telnyx-provision provision     # create or repoint the connector
vcon-telnyx-provision status        # what connectors exist in their account
vcon-telnyx-provision deprovision   # remove ours
```

`provision` is idempotent: re-running against an unchanged target does nothing, and
against a moved SRS repoints the existing connector rather than creating a second
one.

Two limits specific to this path:

**SIPREC needs Call Control.** `siprec_start` takes a `call_control_id`, so a bare
SIP connection cannot use it. The customer's connection has to be a Voice API or
TeXML application.

**Media is unencrypted.** `sip_transport` defaults to `udp` because the SRS cannot
decrypt SRTP yet (CON-800). Restrict the SIP and RTP ports to Telnyx source IPs.
This is a pilot-grade compromise, not a shippable posture for a compliance product.

## Status

**The API path is verified against live Telnyx.** Two PSTN calls on 2026-09-09
produced spec-valid vCons from real audio, and the findings are in "What a live call
established" above. The one remaining gap on that path is the re-hosting publish step,
which has been exercised only with synthetic audio.

**The SIPREC path is verified only against a mock** (unit tests plus an end-to-end CLI
run). Its request shapes come from the published docs, and the `PATCH`/`DELETE`
addressing of connectors by *name* rather than id is the most likely thing to be wrong
on first contact. Since the path is parked, nobody has found out.
