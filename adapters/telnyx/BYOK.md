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
