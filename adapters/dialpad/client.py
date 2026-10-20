"""Thin Dialpad REST client: call info, transcript, AI recap, recording audio.

Docs: https://developers.dialpad.com. Auth is `Authorization: Bearer <key>`.
Dialpad rate-limits to 20 requests per second per company, so HTTP 429 is
retried a small bounded number of times, honouring `Retry-After`.

The API key is never logged, and is only sent to the configured API origin or
to https on dialpad.com or sandbox.dialpad.com.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
MAX_BACKOFF_SECONDS = 30.0

# Hosts that serve Dialpad recordings over https. Both are Dialpad's, so the
# key may go to either, whichever one DIALPAD_API_BASE names. Which host a
# sandbox event's recording URL uses is unverified, so neither is assumed.
DIALPAD_RECORDING_HOSTS = frozenset({"dialpad.com", "sandbox.dialpad.com"})


class DialpadAPIError(Exception):
    """A Dialpad API read failed in a way worth retrying: a network error, a
    5xx, a 408, or 429s past the retry budget.

    Distinct from the API answering that the resource is not there (404),
    which the read methods report as None.
    """


def _is_transient(status: int) -> bool:
    return status >= 500 or status in (408, 429)


def _path_segment(value: Any) -> str:
    """An event-supplied id, made safe to place in one URL path segment."""
    return quote(str(value), safe="")


class DialpadClient:
    """Calls the Dialpad v2 API."""

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://dialpad.com",
        timeout: float = 60,
        session: requests.Session | None = None,
    ):
        self._api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def __repr__(self) -> str:
        return f"DialpadClient(api_base={self.api_base!r})"

    def _is_trusted_recording_url(self, url: str) -> bool:
        """Whether `url` may receive the API key: the configured API origin,
        or https on a Dialpad recording host with no userinfo or odd port."""
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return False
        base = urlparse(self.api_base)
        if (parsed.scheme, parsed.netloc.lower()) == (base.scheme, base.netloc.lower()):
            return True
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and port in (None, 443)
            and (parsed.hostname or "").lower() in DIALPAD_RECORDING_HOSTS
        )

    # -- HTTP plumbing ----------------------------------------------------

    def _request(
        self, method: str, url: str, *, raise_transient: bool = False, **kwargs: Any
    ) -> requests.Response | None:
        """Send an authenticated request, retrying 429s. None on any failure,
        except that with `raise_transient` a failure worth retrying raises
        DialpadAPIError instead."""
        headers = {"Authorization": f"Bearer {self._api_key}", **kwargs.pop("headers", {})}
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.session.request(
                    method, url, headers=headers, timeout=self.timeout, **kwargs
                )
            except requests.RequestException as e:
                logger.error(f"Dialpad {method} {url} failed: {e}")
                if raise_transient:
                    raise DialpadAPIError(f"Dialpad {method} {url} failed") from None
                return None

            if response.status_code == 429 and attempt < MAX_RETRIES:
                delay = self._retry_delay(response, attempt)
                logger.warning(f"Dialpad rate limited; retrying in {delay:.1f}s")
                time.sleep(delay)
                continue

            if response.status_code == 404:
                logger.info(f"Dialpad {method} {url} returned 404")
                return None
            try:
                response.raise_for_status()
            except requests.RequestException as e:
                logger.error(f"Dialpad {method} {url} failed: {e}")
                if raise_transient and _is_transient(response.status_code):
                    raise DialpadAPIError(
                        f"Dialpad {method} {url} failed with {response.status_code}"
                    ) from None
                return None
            return response
        return None

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else 2.0**attempt
        except ValueError:
            delay = 2.0**attempt
        return max(0.0, min(delay, MAX_BACKOFF_SECONDS))

    def _get_json(self, path: str, **kwargs: Any) -> dict[str, Any] | None:
        """GET a JSON object. None when absent (404, other 4xx, non-object);
        raises DialpadAPIError when the failure is worth retrying."""
        response = self._request("GET", f"{self.api_base}{path}", raise_transient=True, **kwargs)
        if response is None:
            return None
        try:
            data = response.json()
        except ValueError:
            logger.error(f"Dialpad {path} returned non-JSON")
            return None
        return data if isinstance(data, dict) else None

    # -- API calls --------------------------------------------------------

    def get_call(self, call_id: int | str) -> dict[str, Any] | None:
        """GET /api/v2/call/{call_id}: call info including recording_details.

        None when not found; raises DialpadAPIError on a transient failure.
        """
        return self._get_json(f"/api/v2/call/{_path_segment(call_id)}")

    def get_transcript(self, call_id: int | str) -> dict[str, Any] | None:
        """GET /api/v2/transcripts/{call_id}: {"call_id", "lines": [...]}.

        None when unavailable (transcripts need Dialpad AI on the call);
        raises DialpadAPIError on a transient failure.
        """
        return self._get_json(f"/api/v2/transcripts/{_path_segment(call_id)}")

    def get_ai_recap(self, call_id: int | str) -> dict[str, Any] | None:
        """GET /api/v2/call/{call_id}/ai_recap (scope `ai_recap`, 12 req/min).

        Returns Dialpad's AiRecapProto: `summary`, `disposition`,
        `action_items` and `purposes`, each an object (or list of objects)
        with a `content` string. None when unavailable; raises DialpadAPIError
        on a transient failure.
        """
        return self._get_json(f"/api/v2/call/{_path_segment(call_id)}/ai_recap")

    def create_recording_share_link(
        self, recording_id: str, recording_type: str
    ) -> dict[str, Any] | None:
        """POST /api/v2/recordingsharelink; the response `id` is the link id."""
        response = self._request(
            "POST",
            f"{self.api_base}/api/v2/recordingsharelink",
            json={
                "recording_id": str(recording_id),
                "recording_type": recording_type,
                "privacy": "owner",
            },
        )
        if response is None:
            return None
        try:
            data = response.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def download_recording(self, recording_detail: dict[str, Any]) -> bytes | None:
        """Download one `recording_details` entry as audio bytes (mp3).

        `callrecording` and `voicemail` URLs download directly with bearer
        auth (scope `recordings_export`). `admincallrecording` URLs are
        browser-only, so a share link is created and its id downloaded from
        the admin recording path instead.
        """
        recording_type = recording_detail.get("recording_type")
        recording_id = recording_detail.get("id")

        if recording_type == "admincallrecording":
            if not recording_id:
                return None
            link = self.create_recording_share_link(recording_id, recording_type)
            link_id = link.get("id") if link else None
            if not link_id:
                logger.error("Could not create a Dialpad share link for an admin recording")
                return None
            url = f"{self.api_base}/recording/admincallrecording/{_path_segment(link_id)}"
        else:
            url = recording_detail.get("url")
            if not url:
                return None
            # The event is attacker-influenced when signatures are off; never
            # hand the API key to a host other than the configured one.
            if not self._is_trusted_recording_url(url):
                logger.error("Refusing to download a recording from an unexpected host")
                return None

        response = self._request("GET", url)
        if response is None or not response.content:
            return None
        return response.content
