# Contributing

## Spec Compliance Checklist

**Every PR that touches vCon construction MUST tick these boxes.** Based on [vcon-speckit](https://github.com/vcon-dev/vcon-speckit) non-negotiables and IETF `draft-ietf-vcon-vcon-core-02`.

**Always use the [`vcon`](https://pypi.org/project/vcon/) library helpers** (`Vcon.add_party`, `add_dialog`, `add_attachment`, `add_analysis`, `add_tag`) rather than writing to `vcon_dict[...]` directly. The lib (>=0.9.4) is spec-correct out of the box.

### Top-level vCon
- [ ] `vcon` syntax param is `"0.4.0"` (the lib sets this automatically; don't rely on writing it yourself)
- [ ] `uuid` is a v4 UUID string
- [ ] All timestamps are ISO-8601 with timezone (`Z` or explicit offset)
- [ ] No empty `group: []` or `redacted: {}` placeholders
- [ ] `subject` (if set) written via `v.vcon_dict["subject"]` — the lib has no setter

### Dialog objects
- [ ] Built via `Dialog(type=..., start=..., parties=..., ...)` — pass kwargs by name
- [ ] **Field is `mediatype`, NEVER `mimetype`** — `Dialog.__init__` takes `**kwargs` so the wrong name slides through silently
- [ ] Inline audio uses `encoding="base64url"` (RFC 4648 URL-safe alphabet, no padding) — NOT `"base64"`
- [ ] Inline audio body is paired with `content_hash="sha512-<base64url-of-digest>"`
- [ ] External-media (URL-only) dialogs MUST include `content_hash` — without it, the dialog is not spec-compliant
- [ ] Empty `metadata: {}` / `meta: {}` placeholders stripped (lib's `Dialog.to_dict` emits them when unset)

### Analysis objects
- [ ] Use `Vcon.add_analysis(type=..., dialog=..., vendor=..., body=..., encoding=..., schema=..., ...)`
- [ ] `vendor` is REQUIRED (the lib enforces this as a kwarg)
- [ ] Field name is `schema`, NEVER `schema_version`
- [ ] `body` is always a string. If JSON-bodied, paired with `encoding="json"`
- [ ] **Transcripts go in `analysis[]`, NOT `attachments[]`**

### Attachment objects
- [ ] Use `Vcon.add_attachment(purpose=..., body=..., encoding=..., party=..., dialog=...)`
- [ ] Field name is `purpose`, NEVER `type` (in core)
- [ ] Always pass `party` and `dialog` (use `0, 0` for vCon-level attachments)
- [ ] JSON bodies: `encoding="json"` with a `json.dumps(...)`'d string body
- [ ] **Exception**: the `lawful_basis` extension attachment uses `type: "lawful_basis"` per its draft. This is documented; do not "fix" it.

### Tags
- [ ] Use `Vcon.add_tag(name, value)` directly — vcon-lib >=0.9.3 writes `party`/`dialog` correctly
- [ ] Don't write to `vcon_dict["attachments"]` for tags

### Legacy field names to never write out
- `appended` (use spec `amended`)
- `must_support` (use spec `critical`)
- `schema_version` on analysis (use `schema`)
- `type` on core attachments (use `purpose`)
- `mimetype` on dialog (use `mediatype`)
- `encoding: "base64"` (use `"base64url"`)

## Picking an ingestion mode

This monorepo supports three. Pick the one that matches how the source platform delivers events:

- **Webhook** — platform POSTs to us. Use `BaseRecordingData` + `BaseVconBuilder` from `core/`. Reference: `adapters/twilio/`.
- **Poller** — we hit the platform's API on an interval. Skip the base classes; use a plain `@dataclass` recording data + plain builder. Reference: `adapters/signalwire/`.
- **Frame observer** — in-process callbacks from another framework (e.g. Pipecat). Document the integration pattern in a per-adapter README. Reference: `adapters/pipecat/`.

## Dev workflow

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy core adapters
```

## Releasing

1. Update version in `pyproject.toml`
2. Tag: `git tag vX.Y.Z && git push --tags`
3. Build and upload to PyPI (CI publishes on tag push if configured)
