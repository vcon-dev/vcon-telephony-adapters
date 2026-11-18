"""The health route's `X-Purposes-Sha12`: a fingerprint of the purposes this deployment grants.

The receiver, the media host and this adapter must hold the same purposes, or one side would
grant what another refuses. Each exposes the same header and a deploy check compares them.
"""

import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from adapters.telnyx.config import TelnyxConfig
from adapters.telnyx.webhook import create_app
from core.lawful_basis import PURPOSES_HEADER, parse_purposes, purposes_sha12

from .tenant_cases import adapter_env

# The shared vector: the same lines are in the other two repos' tests, so one parse and one
# fingerprint hold in all three.
VECTOR = [
    ("recording,transcription,analysis", ["recording", "transcription", "analysis"]),
    (" recording , analysis ,, ", ["recording", "analysis"]),
    ("recording", ["recording"]),
    ("", []),
    (" , ,", []),
]


@pytest.mark.parametrize(("value", "expected"), VECTOR)
def test_the_purposes_parse(value, expected):
    assert parse_purposes(value) == expected


def test_the_fingerprint_is_the_first_12_hex_of_sha256_of_the_comma_joined_list():
    expected = hashlib.sha256(b"recording,transcription,analysis").hexdigest()[:12]
    assert purposes_sha12(["recording", "transcription", "analysis"]) == expected
    assert purposes_sha12([]) == hashlib.sha256(b"").hexdigest()[:12]


def test_a_different_list_gives_a_different_fingerprint():
    assert purposes_sha12(["recording"]) != purposes_sha12(["recording", "analysis"])


def _health(tmp_path, **env):
    with patch.dict("os.environ", adapter_env(tmp_path, **env), clear=True):
        return TestClient(create_app(TelnyxConfig())).get("/health")


@pytest.mark.parametrize(
    "value", [None, "recording,transcription,analysis", "recording", "", " recording , analysis,"]
)
def test_the_health_route_carries_the_fingerprint_of_the_configured_purposes(tmp_path, value):
    response = _health(tmp_path, LAWFUL_BASIS_PURPOSES=value, LAWFUL_BASIS=None)

    purposes = ["recording"] if value is None else parse_purposes(value)
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "vcon-telnyx-adapter"}
    assert response.headers[PURPOSES_HEADER] == purposes_sha12(purposes)


def test_the_header_name_is_the_one_the_deploy_check_reads(tmp_path):
    assert PURPOSES_HEADER == "X-Purposes-Sha12"
    assert "x-purposes-sha12" in _health(tmp_path).headers
