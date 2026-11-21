"""Tests for scripts/dialpad_provision.py with requests mocked."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "dialpad_provision.py"


@pytest.fixture
def prov():
    spec = importlib.util.spec_from_file_location("dialpad_provision", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resp(status, data):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = data
    r.text = json.dumps(data)
    return r


def test_provision_creates_webhook_then_subscription(prov, monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return _resp(200, {"id": "193"} if url.endswith("/webhooks") else {"id": 7})

    monkeypatch.setattr(prov.requests, "post", fake_post)
    out = prov.provision("https://dialpad.com", "KEY", "https://h/x", "S", ["recording"])
    assert out == {"webhook_id": "193", "subscription_id": 7}
    assert calls[0][0] == "https://dialpad.com/api/v2/webhooks"
    assert calls[0][1] == {"hook_url": "https://h/x", "secret": "S"}
    assert calls[1][0] == "https://dialpad.com/api/v2/subscriptions/call"
    assert calls[1][1] == {"endpoint_id": 193, "call_states": ["recording"], "enabled": True}
    assert calls[0][2]["Authorization"] == "Bearer KEY"


def test_dry_run_sends_nothing_and_hides_secrets(prov, monkeypatch, capsys):
    monkeypatch.setattr(prov.requests, "post", lambda *a, **k: pytest.fail("network"))
    rc = prov.main(
        ["--hook-url", "https://h/x", "--secret", "TOPSECRET", "--api-key", "KEY123", "--dry-run"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "TOPSECRET" not in out and "KEY123" not in out
    assert "recap_summary" in out


def test_error_does_not_leak_key(prov, monkeypatch, capsys):
    monkeypatch.setattr(prov.requests, "post", lambda *a, **k: _resp(401, {"error": "nope"}))
    rc = prov.main(["--hook-url", "https://h/x", "--api-key", "KEY123", "--secret", "S"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "KEY123" not in captured.err + captured.out


def test_missing_hook_url(prov, capsys):
    assert prov.main(["--api-key", "k"]) == 2
