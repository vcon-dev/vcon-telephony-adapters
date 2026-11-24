"""Log lines must carry neither full phone numbers nor signed-URL query strings."""

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from adapters.telnyx.builder import TelnyxRecordingData, TelnyxVconBuilder
from core.log_safe import mask_number, redact_text, redact_url

FROM_NUMBER = "+15555550142"
TO_NUMBER = "+15555550177"
SIGNED_URL = (
    "https://recordings.example.test/bucket/rec-1.wav"
    "?X-Amz-Signature=deadbeef0123&X-Amz-Credential=AKIAFAKE"
)
SECRETS = ("deadbeef0123", "AKIAFAKE", "X-Amz-Signature", "?X-Amz")


CALL_CONTROL_ID = "v3:AbCdEf0123456789_-AbCdEf0123456789"


def _recording(url: str = SIGNED_URL, **extra) -> TelnyxRecordingData:
    return TelnyxRecordingData(
        {
            "data": {
                "event_type": "call.recording.saved",
                "payload": {
                    **extra,
                    "recording_id": "rec-1",
                    "from": FROM_NUMBER,
                    "to": TO_NUMBER,
                    "direction": "incoming",
                    "duration_millis": 30000,
                    "recording_urls": {"wav": url},
                    "start_time": "2024-01-15T10:29:30Z",
                },
            }
        }
    )


def _own_log(caplog) -> str:
    """Log text from this repo's loggers; the third-party `vcon` library logs under `vcon.*`."""
    return "\n".join(
        r.getMessage() for r in caplog.records if r.name.split(".")[0] in {"adapters", "core"}
    )


def _assert_clean(text: str) -> None:
    for number in (FROM_NUMBER, TO_NUMBER, FROM_NUMBER[1:], TO_NUMBER[1:]):
        assert number not in text
    for secret in SECRETS:
        assert secret not in text


class TestHelpers:
    def test_mask_number_keeps_last_four(self):
        assert mask_number(FROM_NUMBER) == "***0142"
        assert mask_number("+1 (555) 555-0177") == "***0177"

    def test_mask_number_edge_cases(self):
        assert mask_number(None) == "-"
        assert mask_number("") == "-"
        assert mask_number("12") == "***"

    def test_redact_url_drops_query_fragment_and_userinfo(self):
        assert redact_url(SIGNED_URL) == "https://recordings.example.test/bucket/rec-1.wav"
        assert redact_url("https://user:pw@host.test/a#frag") == "https://host.test/a"

    def test_redact_text_cleans_urls_in_exception_text(self):
        err = requests.ConnectionError(
            f"HTTPSConnectionPool: Max retries with url: {SIGNED_URL} (x)"
        )
        cleaned = redact_text(err)
        assert "deadbeef0123" not in cleaned
        assert "https://recordings.example.test/bucket/rec-1.wav" in cleaned


class TestBuildLog:
    def test_created_vcon_line_masks_numbers_and_keeps_prefix(self, caplog):
        builder = TelnyxVconBuilder(download_recordings=False)
        with caplog.at_level(logging.DEBUG):
            vcon = builder.build(_recording())
        assert vcon is not None
        assert f"Created vCon {vcon.uuid}" in _own_log(caplog)
        assert "***0142" in _own_log(caplog)
        assert "***0177" in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    def test_base_builder_created_line_masks_numbers(self, caplog):
        from core.base_builder import BaseVconBuilder

        class Stub(BaseVconBuilder):
            ADAPTER_SOURCE = "stub"

            def _download_recording(self, recording_data):
                return None

        data = _recording()
        builder = Stub(download_recordings=False)
        with caplog.at_level(logging.DEBUG):
            vcon = builder.build(data)
        assert vcon is not None
        assert f"Created vCon {vcon.uuid}" in _own_log(caplog)
        assert "(from: ***0142, to: ***0177)" in _own_log(caplog)
        _assert_clean(_own_log(caplog))


class TestCreatedLineCallId:
    """The Created vCon line ends with the call control id, so a log search joins the call by id."""

    def test_telnyx_line_carries_the_call_control_id_after_the_masked_numbers(self, caplog):
        builder = TelnyxVconBuilder(download_recordings=False)
        with caplog.at_level(logging.DEBUG):
            vcon = builder.build(_recording(call_control_id=CALL_CONTROL_ID))
        assert (
            f"Created vCon {vcon.uuid} from recording rec-1 "
            f"(from: ***0142, to: ***0177) call={CALL_CONTROL_ID}"
        ) in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    def test_base_builder_line_carries_the_call_control_id(self, caplog):
        from core.base_builder import BaseVconBuilder

        class Stub(BaseVconBuilder):
            ADAPTER_SOURCE = "stub"

            def _download_recording(self, recording_data):
                return None

        with caplog.at_level(logging.DEBUG):
            vcon = Stub(download_recordings=False).build(
                _recording(call_control_id=CALL_CONTROL_ID)
            )
        assert f"(from: ***0142, to: ***0177) call={CALL_CONTROL_ID}" in _own_log(caplog)
        assert vcon is not None

    def test_no_call_control_id_leaves_the_line_as_it_was(self, caplog):
        with caplog.at_level(logging.DEBUG):
            TelnyxVconBuilder(download_recordings=False).build(_recording())
        line = [m for m in _own_log(caplog).splitlines() if m.startswith("Created vCon")][0]
        assert line.endswith("(from: ***0142, to: ***0177)")

    @pytest.mark.parametrize(
        "bad",
        [
            "12025550123",  # a bare number
            "JaneDoe",  # a name
            "tel:12025550123",
            "+12025550123",  # a leading plus
            "v3:AbCdEf0123456789\nINFO forged line",  # a newline forges a second line
            "v3:AbCdEf0123456789 AbCd",  # a space
            "v3:AbCdEf0123456789=AbCd",  # =
            "v3:AbCdEf0123456789@AbCd",  # @
            "v3:AbCdEf0123456789.AbCd",  # .
            "v3:AbCdEf0123456789+AbCd",  # +
            "v3:" + "a" * 15,  # one short of the minimum
            "v3:" + "a" * 129,  # one over the maximum
            "5e1f7a52-8c3b-4d0e-9a41-0123456789a",  # a short uuid
            "5e1f7a52-8c3b-4d0e-9a41-0123456789ab\n",
        ],
    )
    def test_only_a_v3_id_or_a_uuid_is_printed(self, bad, caplog):
        with caplog.at_level(logging.DEBUG):
            TelnyxVconBuilder(download_recordings=False).build(_recording(call_control_id=bad))
        line = [m for m in _own_log(caplog).splitlines() if m.startswith("Created vCon")][0]
        assert "call=" not in line and "forged" not in _own_log(caplog)
        assert line.endswith("(from: ***0142, to: ***0177)")

    @pytest.mark.parametrize(
        "good", ["v3:" + "a" * 16, "v3:" + "A_-9" * 32, "5e1f7a52-8c3b-4d0e-9a41-0123456789ab"]
    )
    def test_the_accepted_forms_are_printed(self, good, caplog):
        with caplog.at_level(logging.DEBUG):
            TelnyxVconBuilder(download_recordings=False).build(_recording(call_control_id=good))
        assert f"(from: ***0142, to: ***0177) call={good}" in _own_log(caplog)

    def test_the_id_is_the_tag_value_the_vcon_already_holds(self):
        vcon = TelnyxVconBuilder(download_recordings=False).build(
            _recording(call_control_id=CALL_CONTROL_ID)
        )
        assert CALL_CONTROL_ID in str(vcon.vcon_dict["attachments"])


class TestTelnyxDownloadLog:
    @patch("adapters.telnyx.builder.requests.get")
    def test_debug_line_has_no_query_string(self, mock_get, caplog):
        response = MagicMock()
        response.content = b"audio"
        mock_get.return_value = response
        builder = TelnyxVconBuilder(api_key="test-api-key")
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(_recording()) == b"audio"
        assert "https://recordings.example.test/bucket/rec-1.wav" in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    @patch("adapters.telnyx.builder.requests.get")
    def test_failed_download_does_not_log_signed_url(self, mock_get, caplog):
        mock_get.side_effect = requests.ConnectionError(
            f"Max retries exceeded with url: {SIGNED_URL}"
        )
        builder = TelnyxVconBuilder(api_key="test-api-key")
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(_recording()) is None
        assert "Failed to download Telnyx recording rec-1" in _own_log(caplog)
        _assert_clean(_own_log(caplog))


def test_twilio_failed_download_does_not_log_query(caplog):
    module = "adapters.twilio.builder"
    from adapters.twilio.builder import TwilioVconBuilder

    builder = TwilioVconBuilder(twilio_auth=("ACfake", "fake"))
    data = MagicMock()
    data.recording_url = SIGNED_URL.split("?")[0] + "?sig=deadbeef0123"
    with patch(f"{module}.requests.get", side_effect=requests.ConnectionError(SIGNED_URL)):
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(data) is None
    assert "Error downloading recording" in _own_log(caplog)
    assert "deadbeef0123" not in _own_log(caplog)
    assert "AKIAFAKE" not in _own_log(caplog)


# --- review follow-ups ------------------------------------------------------

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestRealNetworkFailure:
    """urllib3 prints `with url: /path?query` with no scheme on connection failures."""

    def test_connection_refused_does_not_log_signature(self, caplog):
        url = (
            f"http://127.0.0.1:{_closed_port()}/bucket/rec-1.wav"
            "?X-Amz-Credential=AKIAFAKE&X-Amz-Signature=deadbeef0123"
        )
        # Confirm the premise: the real error text carries the query with no scheme.
        try:
            requests.get(url, timeout=2)
        except requests.RequestException as exc:
            assert "deadbeef0123" in str(exc)
            assert "http://" not in str(exc)

        builder = TelnyxVconBuilder(api_key="test-api-key")
        with caplog.at_level(logging.DEBUG):
            assert builder._download_recording(_recording(url)) is None
        assert "Failed to download Telnyx recording" in _own_log(caplog)
        _assert_clean(_own_log(caplog))

    def test_redact_text_scheme_less_path_forms(self):
        for text in (
            "Max retries exceeded with url: /b/rec-1.wav?X-Amz-Signature=deadbeef0123 (Caused by X)",
            "failed for '/b/rec-1.wav?X-Amz-Signature=deadbeef0123'",
            'GET "/b/rec-1.wav?sig=deadbeef0123" failed',
            "(wss://host.test/stream?token=deadbeef0123)",
            "s3://bucket/key?versionId=deadbeef0123",
        ):
            assert "deadbeef0123" not in redact_text(text), text
        assert "/b/rec-1.wav" in redact_text("with url: /b/rec-1.wav?sig=x")


class TestAccessLogQuery:
    def test_access_log_has_no_query_string(self, capfd):
        import uvicorn
        from fastapi import FastAPI

        from core.log_safe import uvicorn_log_config

        app = FastAPI()

        @app.post("/webhook/texml-recording")
        def hook():
            return {"ok": True}

        port = _closed_port()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="info",
                log_config=uvicorn_log_config(),
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            r = requests.post(
                f"http://127.0.0.1:{port}/webhook/texml-recording?token=secret", timeout=5
            )
            assert r.status_code == 200
        finally:
            server.should_exit = True
            thread.join(5)
        out = capfd.readouterr()
        text = out.out + out.err
        assert "/webhook/texml-recording" in text
        assert "POST" in text and "200" in text
        assert "secret" not in text
        assert "token=" not in text


class TestHelperEdges:
    def test_mask_number_sip_uri_masks_user_part(self):
        assert mask_number("sip:+15555550142@203.0.113.7:5060") == "***0142"
        assert mask_number("sip:alice@203.0.113.7:5060") == "***"
        assert mask_number("tel:+15555550177") == "***0177"

    def test_redact_url_raw_at_in_password_and_other_schemes(self):
        assert redact_url("https://user:p@ss@host.test/a") == "https://host.test/a"
        assert redact_url("s3://bucket/key?versionId=1") == "s3://bucket/key"
        assert redact_url("wss://u:p@h.test/s?token=1") == "wss://h.test/s"


class TestPosterBody:
    def test_error_body_not_logged(self, caplog):
        from core.poster import HttpPoster

        resp = MagicMock(status_code=422)
        resp.text = f"input echoed: {FROM_NUMBER} {SIGNED_URL}"
        vcon = MagicMock(uuid="u-1")
        vcon.to_json.return_value = "{}"
        poster = HttpPoster("http://conserver.example.test/vcon", {})
        with patch("core.poster.requests.post", return_value=resp), caplog.at_level(logging.DEBUG):
            assert poster.post(vcon) is False
        assert "status: 422" in _own_log(caplog)
        _assert_clean(_own_log(caplog))


class TestRedactTextReviewFixes:
    def test_ipv6_host_with_port_loses_query(self):
        url = "https://[2001:db8::1]:8443/rec.wav?X-Amz-Signature=deadbeef0123"
        assert redact_url(url) == "https://[2001:db8::1]:8443/rec.wav"
        out = redact_text(f"failed to fetch {url} (timeout)")
        assert "deadbeef0123" not in out
        assert "https://[2001:db8::1]:8443/rec.wav" in out

    def test_parenthesis_inside_path_keeps_redacting(self):
        out = redact_text("fetch https://host.test/a(1).wav?X-Amz-Signature=deadbeef0123 failed")
        assert "deadbeef0123" not in out
        assert "https://host.test/a(1).wav" in out
        assert redact_text("url: /a(1).wav?sig=deadbeef0123 x") == "url: /a(1).wav x"

    def test_closing_parenthesis_ends_a_parenthesized_url(self):
        out = redact_text("(see https://host.test/a.wav?sig=deadbeef0123) next")
        assert out == "(see https://host.test/a.wav) next"

    def test_adversarial_input_is_fast(self):
        for text in ("(/" * 32000, "/" * 64000, "(" * 64000, "https://" + "(a" * 32000):
            start = time.perf_counter()
            redact_text(text)
            assert time.perf_counter() - start < 0.5


def test_every_uvicorn_run_in_main_passes_the_redacting_log_config():
    import ast
    import pathlib

    tree = ast.parse((pathlib.Path(__file__).parent.parent / "main.py").read_text())

    def starts_server(n: ast.AST) -> bool:
        if not isinstance(n, ast.Call):
            return False
        f = n.func
        if isinstance(f, ast.Name) and f.id == "serve":
            return True  # main.serve wraps uvicorn.run with tracing
        return (
            isinstance(f, ast.Attribute)
            and f.attr == "run"
            and isinstance(f.value, ast.Name)
            and f.value.id == "uvicorn"
        )

    # serve() forwards its keywords to uvicorn.run with `**`; the callers are what is checked.
    calls = [
        n for n in ast.walk(tree) if starts_server(n) and not any(k.arg is None for k in n.keywords)
    ]
    started = [n for n in ast.walk(tree) if starts_server(n)]
    assert any(isinstance(n.func, ast.Attribute) for n in started), "no uvicorn.run call found"
    assert any(isinstance(n.func, ast.Name) for n in started), "no serve() call found"
    assert calls, "no server start with explicit keywords to check"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        value = kw.get("log_config")
        assert (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "uvicorn_log_config"
        ), f"uvicorn.run at line {call.lineno} lacks log_config=uvicorn_log_config()"


class TestFinalReviewNits:
    @pytest.mark.parametrize("alphabet", ["a", "aB", "a1", "aB1+.-"])
    def test_long_scheme_like_runs_are_fast(self, alphabet):
        text = (alphabet * 70000)[:65536]
        start = time.perf_counter()
        redact_text(text)
        assert time.perf_counter() - start < 0.1

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("url=/b?sig=deadbeef0123", "url=/b"),
            ("url=//host.test/b?sig=deadbeef0123", "url=//host.test/b"),
            ("(/b?sig=deadbeef0123)", "(/b)"),
            (
                "see (/b?sig=deadbeef0123) and url=/c?sig=deadbeef0123 end",
                "see (/b) and url=/c end",
            ),
            (
                "with url: /b/rec-1.wav?sig=deadbeef0123 (Caused by X)",
                "with url: /b/rec-1.wav (Caused by X)",
            ),
            ("an ordinary question?", "an ordinary question?"),
        ],
    )
    def test_glued_and_parenthesized_bare_paths(self, text, expected):
        assert redact_text(text) == expected


class TestRedactTextNumbers:
    def test_masks_numbers_in_error_text(self):
        out = redact_text("boom for +15550100001 at http://host.test/r.mp3?sig=deadbeef0123")
        assert "5550100001" not in out
        assert "***0001" in out
        assert "deadbeef0123" not in out
        assert "http://host.test/r.mp3" in out

    def test_masks_with_and_without_plus_and_lengths(self):
        for number in (
            "+15550100001",
            "15550100001",
            "5550100001",
            "+442071838750",
            "123456789012345",
        ):
            out = redact_text(f"failed for {number}.")
            assert number.lstrip("+") not in out, number
            assert f"***{number[-4:]}." in out, number

    def test_masks_numbers_in_sip_and_tel_uris(self):
        for text in (
            "INVITE sip:+15550100001@gw.test;user=phone",
            "to tel:+15550100001",
            "sips:15550100001@h.test",
        ):
            out = redact_text(text)
            assert "5550100001" not in out, text
            assert "***0001" in out, text
        assert "gw.test" in redact_text("sip:+15550100001@gw.test")

    def test_leaves_ids_timestamps_and_short_numbers(self):
        keep = (
            "uuid 01a20963-e260-8a14-9dd8-dd37220d739c",
            "uuid 12345678-1234-1234-1234-123456789012",
            "at 2026-11-23T17:11:10.689132+00:00",
            "call v3:12345678901234 and v3:abc1234567890",
            "ip 10.0.0.1 port 5060 status 502 length 1234567",
        )
        for text in keep:
            assert redact_text(text) == text, text


class TestRedactTextNumberForms:
    def test_masks_separated_and_encoded_forms(self):
        for number in (
            "+1-555-010-0001",
            "(555) 010-0001",
            "+1 555 010 0001",
            "555.010.0001",
            "555-010-0001",
            "%2B15550100001",
        ):
            out = redact_text(f"failed for {number} now")
            assert out == "failed for ***0001 now", (number, out)

    def test_masks_numbers_glued_to_separators_and_names(self):
        for text, expected in (
            ("out-15550100001-1.wav", "out-***0001-1.wav"),
            ("15550100001_inbound.wav", "***0001_inbound.wav"),
            ("/rec/RE15550100001/a.wav", "/rec/RE***0001/a.wav"),
            ("from:+15550100001", "from:***0001"),
            ("sip:+15550100001@gw.test", "sip:***0001@gw.test"),
        ):
            assert redact_text(text) == expected, text

    def test_keeps_ids_ports_and_short_numbers(self):
        for text in (
            "uuid 12345678-1234-1234-1234-123456789012",
            "at 2026-11-23T17:11:10.689132+00:00",
            "call v3:12345678901234 and v3:abc1234567890",
            "ip 10.0.0.1:5060 status 502 length 1234567 id RE12345678",
        ):
            assert redact_text(text) == text, text
