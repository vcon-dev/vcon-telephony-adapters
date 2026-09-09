# Smart trunk, bring your own key

An experiment in a product shape: the customer keeps their own Telnyx account and
gives us an API key. We point their SIPREC client at an SRS we operate and fork
each answered call to it. Their carriage, numbers, and bill stay theirs.

We never sit in the media path. If our SRS is down, their calls still complete and
we lose recordings for the outage window — that asymmetry is the point.

## What runs

```
customer's Telnyx account          our droplet
  │                                   │
  ├── SIPREC connector ───────────────┤ vcon-siprec-adapter  (SRS)
  │      (we create this)             │        │
  └── call.answered webhook ──────────┤        └──> conserver -> vCon
                                      │  this adapter starts the fork
```

Two moving parts, both here:

- `provision.py` — creates the SIPREC connector in the customer's account and
  starts/stops SIPREC per call.
- `POST /webhook/call` in `webhook.py` — on `call.answered`, starts the fork.

## Provisioning

```bash
export TELNYX_API_KEY=<the customer's key>
export SRS_HOST=srs.example.com
export SRS_PORT=5060

vcon-telnyx-provision check         # is the key valid and does it have access?
vcon-telnyx-provision provision     # create or repoint the connector
vcon-telnyx-provision status        # what connectors exist in their account
vcon-telnyx-provision deprovision   # remove ours
```

`provision` is idempotent: re-running against an unchanged target does nothing,
and against a moved SRS repoints the existing connector rather than creating a
second one.

## Running the trunk

```bash
export TELNYX_API_KEY=<the customer's key>
export TELNYX_AUTO_SIPREC=true
export TELNYX_CONNECTOR_NAME=vconic-smart-trunk
export TELNYX_REALTIME_TRANSCRIPTION=false   # Telnyx-side ASR, billed to them
export CONSERVER_URL=https://conserver.example.com

vcon-adapter telnyx
```

Point the customer's Telnyx webhook URL at `/webhook/call`. Auto-forking is off
unless `TELNYX_AUTO_SIPREC` is explicitly on — forking someone's calls should not
start happening because a variable was unset.

## Party identity needs the lifecycle events

`call.recording.saved` names nobody: no `from`, `to`, or `direction`. Point the
customer's webhook at `/webhook/call` as well as `/webhook/recording` and the
adapter correlates them by `call_session_id`.

Two layers, on purpose:

- **`CallSessionStore`** accumulates the lifecycle events in memory: parties,
  direction, ring and answer timing, hangup cause, SIP headers, negotiated
  codec, carrier MOS. A restart loses it.
- **The recordings API** is consulted when no session is found, and returns
  `from`, `to` and duration authoritatively. Stateless, so party identity
  survives a restart, a dropped webhook, or a replay.

Losing enrichment on a restart is acceptable. Losing party identity is not,
which is why only the latter has a stateless fallback.

Signalling detail lands in a `sip-message-trace` attachment under the
`sip-signaling` extension, the same shape the SIPREC SRS emits, so a
Telnyx-sourced vCon and a SIPREC-sourced one describe a call identically.

## Keep the vCons thin

By default the adapter inlines audio as base64, which makes a vCon roughly 1.3x
the size of the recording. A twenty-second call becomes an 800 KB JSON object;
an hour-long one becomes unusable. Re-host instead and the dialog carries a
`url` plus a `content_hash` — about **800x smaller**, and still verifiable.

```bash
MEDIA_BACKEND=s3
MEDIA_S3_BUCKET=vconic-media
MEDIA_S3_PREFIX=telnyx
MEDIA_S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com   # any S3-compatible store
MEDIA_BASE_URL=https://vconic-media.nyc3.digitaloceanspaces.com
```

`MEDIA_BACKEND=filesystem` with `MEDIA_FILESYSTEM_PATH` is the local equivalent.
S3 needs `boto3`, which is not a base dependency.

**Re-hosting is not optional for Telnyx.** Recording URLs are pre-signed and
expire in 600 seconds, so a vCon that references the Telnyx URL directly has a
dead audio link within ten minutes. The audio must be fetched and re-hosted
inside that window, which also makes the fetch a hard deadline for the whole
pipeline rather than something that can be retried tomorrow.

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

An invalid value fails at startup rather than silently producing basis-less
vCons for a month.

Which basis is correct is a legal question for the deployment, not a default we
can pick. `legitimate_interests` suits carrier-side recording where the customer
is the Data Controller and manages consent themselves; `consent` suits a flow
that actually captures it per call, in which case `consentify` can detect it
from the transcript and record proof.

## Handling the customer's key

It is their key and it controls their telephony account.

- Never logged. Redacted from every error this module raises (there is a test).
- Never written to disk by this code.
- The calls we make with it are exactly: list/create/update/delete a SIPREC
  connector, `siprec_start`, `siprec_stop`, and optionally
  `transcription_start`. Nothing else.

## Two limits worth knowing before a demo

**SIPREC needs Call Control.** `siprec_start` takes a `call_control_id`, so a bare
SIP connection cannot use this. The customer's connection has to be a Voice API or
TeXML application. Customers on a plain SIP trunk get the post-call path
(`call.recording.saved`) instead — same vCons, no realtime.

**Media is unencrypted.** `sip_transport` defaults to `udp` because the SRS cannot
decrypt SRTP yet. Restrict the SIP and RTP ports to Telnyx source IPs. This is a
pilot-grade compromise, not a shippable posture for a compliance product.

## Status

Verified against a mock Telnyx (unit tests plus an end-to-end CLI run).
**Not yet verified against the live Telnyx API** — the request shapes come from
their published docs, and the `PATCH`/`DELETE` addressing by connector *name*
rather than id is the most likely thing to be wrong on first contact.
