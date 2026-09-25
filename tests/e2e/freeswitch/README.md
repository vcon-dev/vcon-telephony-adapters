# FreeSWITCH end-to-end harness

Manual test harness for the FreeSWITCH adapter against a real, locally
running FreeSWITCH instance (macOS/Homebrew). Ported from a private
`vcon-freeswitch-tester` repo, reviewed for secrets/hostnames/personal data
before copying in; one line in `setup_local.sh` naming a specific machine
and its LAN IP/username was redacted to a generic comment.

Not part of the default `pytest` run: `conftest.py` in this directory
excludes it from collection (it needs a live FreeSWITCH, Redis, and this
repo's own adapter running locally; CI has none of those, and
`test_ws_client.py` also imports `websockets`, which is not a project
dependency).

## Contents

- `setup_local.sh` - installs/configures a local FreeSWITCH, Redis, and this
  repo's `.venv`, and writes example `.env` files. Adjust paths for your
  machine before running.
- `freeswitch_configs/` - `event_socket.conf.xml` (ESL access),
  `recording_webhook.lua` and `test_extensions.xml` (dialplan entries that
  record a test call and POST it to `/webhook/recording`).
- `mock_conserver.py` - a minimal FastAPI stand-in for a real conserver;
  stores posted vCons to disk under `./vcons` and serves them back for
  inspection.
- `test_call.sh` - drives `fs_cli` to originate a loopback echo/record call.
- `test_webhook.sh` - POSTs a synthetic recording-complete event straight to
  the adapter, bypassing FreeSWITCH.
- `test_e2e.sh` - starts the mock conserver, the FreeSWITCH adapter, and
  (if present in a sibling checkout) `vcon-real-time`, then runs the call,
  webhook and WebSocket checks above and reports what arrived.
- `test_ws_client.py` - streams synthetic PCM audio to a `vcon-real-time`
  WebSocket endpoint. Requires `pip install -r requirements.txt`.

## Running

```bash
cd tests/e2e/freeswitch
./setup_local.sh        # once, adjust paths for your machine first
python3 mock_conserver.py &
cd ../../.. && python main.py freeswitch &
cd tests/e2e/freeswitch
./test_webhook.sh       # or ./test_e2e.sh for the full pipeline
```
