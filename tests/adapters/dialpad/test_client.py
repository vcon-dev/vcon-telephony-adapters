"""Tests for the Dialpad API client.

HTTP is mocked; no network. Response shapes are constructed from Dialpad's
documented API, not captured traffic.
"""

from unittest.mock import MagicMock

import pytest
import requests

from adapters.dialpad import client as client_module
from adapters.dialpad.client import DialpadAPIError, DialpadClient


def make_response(status=200, json_data=None, content=b"", headers=None):
    response = MagicMock(spec=requests.Response)
    response.status_code = status
    response.headers = headers or {}
    response.content = content
    response.json.return_value = json_data
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status}")
    else:
        response.raise_for_status.return_value = None
    return response


def make_client(*responses):
    session = MagicMock()
    session.request.side_effect = list(responses)
    return DialpadClient("secret-key", session=session), session


class TestDialpadClient:
    def test_get_call_sends_bearer_auth(self):
        client, session = make_client(make_response(json_data={"call_id": 1}))
        assert client.get_call(1) == {"call_id": 1}
        args, kwargs = session.request.call_args
        assert args == ("GET", "https://dialpad.com/api/v2/call/1")
        assert kwargs["headers"]["Authorization"] == "Bearer secret-key"

    def test_get_transcript_and_recap_paths(self):
        client, session = make_client(
            make_response(json_data={"lines": []}), make_response(json_data={"summary": {}})
        )
        client.get_transcript(7)
        client.get_ai_recap(7)
        urls = [c.args[1] for c in session.request.call_args_list]
        assert urls == [
            "https://dialpad.com/api/v2/transcripts/7",
            "https://dialpad.com/api/v2/call/7/ai_recap",
        ]

    def test_404_returns_none(self):
        client, _ = make_client(make_response(status=404))
        assert client.get_transcript(1) is None

    def test_network_error_raises_transient(self):
        client, session = make_client()
        session.request.side_effect = requests.ConnectionError("boom")
        with pytest.raises(DialpadAPIError):
            client.get_call(1)

    @pytest.mark.parametrize("status", [500, 502, 503, 408])
    def test_transient_status_raises(self, status):
        for method in ("get_call", "get_transcript", "get_ai_recap"):
            client, _ = make_client(make_response(status=status))
            with pytest.raises(DialpadAPIError):
                getattr(client, method)(1)

    @pytest.mark.parametrize("status", [404, 403, 401])
    def test_not_found_or_forbidden_is_none(self, status):
        # Absent, or a scope that is missing: retrying will not help.
        for method in ("get_call", "get_transcript", "get_ai_recap"):
            client, _ = make_client(make_response(status=status))
            assert getattr(client, method)(1) is None

    def test_download_failure_still_returns_none(self):
        client, _ = make_client(make_response(status=503))
        detail = {"url": "https://dialpad.com/r/1", "recording_type": "callrecording"}
        assert client.download_recording(detail) is None

    def test_429_retries_with_retry_after(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
        client, session = make_client(
            make_response(status=429, headers={"Retry-After": "2"}),
            make_response(json_data={"call_id": 1}),
        )
        assert client.get_call(1) == {"call_id": 1}
        assert sleeps == [2.0]
        assert session.request.call_count == 2

    def test_429_gives_up_after_bounded_retries(self, monkeypatch):
        monkeypatch.setattr(client_module.time, "sleep", lambda s: None)
        client, session = make_client(*[make_response(status=429) for _ in range(10)])
        with pytest.raises(DialpadAPIError):
            client.get_call(1)
        assert session.request.call_count == client_module.MAX_RETRIES + 1

    def test_retry_after_capped(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(client_module.time, "sleep", sleeps.append)
        client, _ = make_client(
            make_response(status=429, headers={"Retry-After": "9999"}),
            make_response(json_data={}),
        )
        client.get_call(1)
        assert sleeps == [client_module.MAX_BACKOFF_SECONDS]

    def test_download_callrecording(self):
        client, session = make_client(make_response(content=b"mp3-bytes"))
        detail = {"id": "r1", "url": "https://dialpad.com/r/r1", "recording_type": "callrecording"}
        assert client.download_recording(detail) == b"mp3-bytes"
        assert session.request.call_args.args == ("GET", "https://dialpad.com/r/r1")

    def test_download_voicemail(self):
        client, _ = make_client(make_response(content=b"vm"))
        detail = {"id": "v1", "url": "https://dialpad.com/v/v1", "recording_type": "voicemail"}
        assert client.download_recording(detail) == b"vm"

    def test_download_admin_recording_uses_share_link(self):
        client, session = make_client(
            make_response(json_data={"id": "LINK1", "access_link": "x"}),
            make_response(content=b"admin-audio"),
        )
        detail = {
            "id": "a1",
            "url": "https://dialpad.com/admin/a1",
            "recording_type": "admincallrecording",
        }
        assert client.download_recording(detail) == b"admin-audio"
        post, get = session.request.call_args_list
        assert post.args == ("POST", "https://dialpad.com/api/v2/recordingsharelink")
        assert post.kwargs["json"]["recording_id"] == "a1"
        assert post.kwargs["json"]["recording_type"] == "admincallrecording"
        assert get.args == ("GET", "https://dialpad.com/recording/admincallrecording/LINK1")

    def test_download_admin_recording_without_share_link_fails(self):
        client, _ = make_client(make_response(status=403))
        detail = {"id": "a1", "recording_type": "admincallrecording"}
        assert client.download_recording(detail) is None

    def test_download_refuses_foreign_host(self):
        client, session = make_client()
        detail = {
            "id": "r1",
            "url": "https://evil.example.net/r/r1",
            "recording_type": "callrecording",
        }
        assert client.download_recording(detail) is None
        session.request.assert_not_called()

    @pytest.mark.parametrize(
        "url",
        [
            "https://dialpad.com.evil.net/r/r1",
            "https://evil-dialpad.com/r/r1",
            "https://evil.dialpad.com.example/r/r1",
            "http://dialpad.com/r/r1",
            "https://user@evil.net@dialpad.com/r/r1",
            "https://dialpad.com:8443/r/r1",
            "ftp://dialpad.com/r/r1",
            "//dialpad.com/r/r1",
            "https://[::1/r/r1",
        ],
    )
    def test_download_refuses_lookalike_hosts(self, url):
        client, session = make_client()
        detail = {"id": "r1", "url": url, "recording_type": "callrecording"}
        assert client.download_recording(detail) is None
        session.request.assert_not_called()

    def test_sandbox_base_still_downloads_dialpad_recordings(self):
        session = MagicMock()
        session.request.side_effect = [make_response(content=b"a"), make_response(content=b"b")]
        client = DialpadClient("k", api_base="https://sandbox.dialpad.com", session=session)
        for url in ("https://dialpad.com/r/r1", "https://sandbox.dialpad.com/r/r2"):
            detail = {"id": "r", "url": url, "recording_type": "callrecording"}
            assert client.download_recording(detail)
        assert session.request.call_count == 2

    def test_configured_http_base_is_trusted(self):
        session = MagicMock()
        session.request.side_effect = [make_response(content=b"a")]
        client = DialpadClient("k", api_base="http://localhost:9000", session=session)
        detail = {"url": "http://localhost:9000/r/r1", "recording_type": "callrecording"}
        assert client.download_recording(detail) == b"a"

    def test_call_id_cannot_traverse_api_path(self):
        client, session = make_client(make_response(json_data={}))
        client.get_call("1/../../webhooks")
        assert session.request.call_args.args[1] == (
            "https://dialpad.com/api/v2/call/1%2F..%2F..%2Fwebhooks"
        )

    def test_download_without_url_returns_none(self):
        client, _ = make_client()
        assert client.download_recording({"id": "r1", "recording_type": "callrecording"}) is None

    def test_api_key_not_in_logs(self, caplog):
        client, session = make_client()
        session.request.side_effect = requests.ConnectionError("down")
        with caplog.at_level("DEBUG"), pytest.raises(DialpadAPIError) as raised:
            client.get_call(1)
        assert "secret-key" not in caplog.text
        assert "secret-key" not in str(raised.value)
