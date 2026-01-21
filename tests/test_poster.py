"""Comprehensive tests for HTTP poster module."""

from unittest.mock import patch, MagicMock

import pytest
from vcon import Vcon

from twilio_adapter.poster import HttpPoster


class TestHttpPosterInit:
    """Tests for HttpPoster initialization."""

    def test_basic_init(self):
        """Basic initialization with required args."""
        poster = HttpPoster(
            url="https://example.com/vcons",
            headers={"Content-Type": "application/json"}
        )

        assert poster.url == "https://example.com/vcons"
        assert poster.headers == {"Content-Type": "application/json"}
        assert poster.ingress_lists == []

    def test_init_with_ingress_lists(self):
        """Initialization with ingress lists."""
        poster = HttpPoster(
            url="https://example.com/vcons",
            headers={},
            ingress_lists=["list1", "list2"]
        )

        assert poster.ingress_lists == ["list1", "list2"]

    def test_init_with_none_ingress_lists(self):
        """None ingress_lists becomes empty list."""
        poster = HttpPoster(
            url="https://example.com/vcons",
            headers={},
            ingress_lists=None
        )

        assert poster.ingress_lists == []


class TestHttpPosterPost:
    """Tests for HttpPoster.post method."""

    def test_post_success_200(self, basic_poster):
        """Successful POST returns True for 200 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is True
            mock_post.assert_called_once()

    def test_post_success_201(self, basic_poster):
        """Successful POST returns True for 201 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is True

    def test_post_success_204(self, basic_poster):
        """Successful POST returns True for 204 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 204
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is True

    def test_post_failure_400(self, basic_poster):
        """Failed POST returns False for 400 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "Bad Request"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False

    def test_post_failure_401(self, basic_poster):
        """Failed POST returns False for 401 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False

    def test_post_failure_500(self, basic_poster):
        """Failed POST returns False for 500 status."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False

    def test_post_exception_returns_false(self, basic_poster):
        """Network exception returns False."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            result = basic_poster.post(vcon)

            assert result is False

    def test_post_timeout_exception(self, basic_poster):
        """Timeout exception returns False."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            from requests.exceptions import Timeout
            mock_post.side_effect = Timeout("Request timed out")

            result = basic_poster.post(vcon)

            assert result is False


class TestHttpPosterRequestFormat:
    """Tests for request format and parameters."""

    def test_posts_to_correct_url(self, basic_poster):
        """POST goes to the configured URL."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon)

            call_args = mock_post.call_args
            assert call_args[0][0] == "https://example.com/vcons"

    def test_posts_with_correct_headers(self, poster_with_auth):
        """POST includes configured headers."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            poster_with_auth.post(vcon)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['headers'] == {
                "Content-Type": "application/json",
                "x-conserver-api-token": "test_token"
            }

    def test_posts_vcon_as_json(self, basic_poster):
        """POST body contains vCon JSON."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon)

            call_kwargs = mock_post.call_args[1]
            # Data should be JSON string
            assert 'data' in call_kwargs
            assert isinstance(call_kwargs['data'], str)
            # Should contain the vcon UUID
            assert vcon.uuid in call_kwargs['data']

    def test_posts_with_timeout(self, basic_poster):
        """POST includes 30 second timeout."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['timeout'] == 30


class TestHttpPosterIngressLists:
    """Tests for ingress lists functionality."""

    def test_no_ingress_lists_no_params(self, basic_poster):
        """No ingress lists means no query params."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['params'] == {}

    def test_single_ingress_list(self):
        """Single ingress list is passed as param."""
        poster = HttpPoster(
            url="https://example.com/vcons",
            headers={},
            ingress_lists=["transcription"]
        )
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            poster.post(vcon)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['params'] == {'ingress_lists': 'transcription'}

    def test_multiple_ingress_lists(self, poster_with_ingress):
        """Multiple ingress lists are joined with comma."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            poster_with_ingress.post(vcon)

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs['params'] == {
                'ingress_lists': 'transcription,analysis,storage'
            }


class TestHttpPosterStatusCodes:
    """Tests for various HTTP status codes."""

    @pytest.mark.parametrize("status_code", [200, 201, 202, 204])
    def test_success_status_codes(self, basic_poster, status_code):
        """2xx status codes return True."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is True

    @pytest.mark.parametrize("status_code", [300, 301, 302, 307, 308])
    def test_redirect_status_codes(self, basic_poster, status_code):
        """3xx status codes return False."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.text = "Redirect"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False

    @pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422, 429])
    def test_client_error_status_codes(self, basic_poster, status_code):
        """4xx status codes return False."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.text = "Client Error"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False

    @pytest.mark.parametrize("status_code", [500, 502, 503, 504])
    def test_server_error_status_codes(self, basic_poster, status_code):
        """5xx status codes return False."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.text = "Server Error"
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            assert result is False


class TestHttpPosterErrorHandling:
    """Tests for error handling scenarios."""

    def test_handles_connection_error(self, basic_poster):
        """Handles connection errors gracefully."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            from requests.exceptions import ConnectionError
            mock_post.side_effect = ConnectionError("Connection refused")

            result = basic_poster.post(vcon)

            assert result is False

    def test_handles_ssl_error(self, basic_poster):
        """Handles SSL errors gracefully."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            from requests.exceptions import SSLError
            mock_post.side_effect = SSLError("SSL certificate error")

            result = basic_poster.post(vcon)

            assert result is False

    def test_handles_long_response_text(self, basic_poster):
        """Handles long error response text (truncated in logs)."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 400
            mock_response.text = "x" * 1000  # Very long error message
            mock_post.return_value = mock_response

            result = basic_poster.post(vcon)

            # Should not raise, just return False
            assert result is False


class TestHttpPosterVconSerialization:
    """Tests for vCon serialization."""

    def test_vcon_to_json_called(self, basic_poster):
        """vCon.to_json() is called for serialization."""
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            # Spy on to_json
            original_to_json = vcon.to_json
            with patch.object(vcon, 'to_json', wraps=original_to_json) as mock_to_json:
                basic_poster.post(vcon)

                mock_to_json.assert_called_once()

    def test_posts_valid_json(self, basic_poster):
        """Posted data is valid JSON."""
        import json
        vcon = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon)

            call_kwargs = mock_post.call_args[1]
            # Should be parseable JSON
            parsed = json.loads(call_kwargs['data'])
            assert isinstance(parsed, dict)
            assert 'uuid' in parsed


class TestHttpPosterMultiplePosts:
    """Tests for multiple POST operations."""

    def test_multiple_successful_posts(self, basic_poster):
        """Multiple successful posts all return True."""
        vcons = [Vcon.build_new() for _ in range(5)]

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            results = [basic_poster.post(vcon) for vcon in vcons]

            assert all(results)
            assert mock_post.call_count == 5

    def test_mixed_success_failure(self, basic_poster):
        """Mixed success and failure posts return correct results."""
        vcons = [Vcon.build_new() for _ in range(3)]

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_post.side_effect = [
                MagicMock(status_code=200),
                MagicMock(status_code=500, text="Error"),
                MagicMock(status_code=201),
            ]

            results = [basic_poster.post(vcon) for vcon in vcons]

            assert results == [True, False, True]

    def test_posts_different_vcons(self, basic_poster):
        """Each POST contains the correct vCon."""
        vcon1 = Vcon.build_new()
        vcon2 = Vcon.build_new()

        with patch('twilio_adapter.poster.requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_post.return_value = mock_response

            basic_poster.post(vcon1)
            basic_poster.post(vcon2)

            # First call should have vcon1 UUID
            first_call_data = mock_post.call_args_list[0][1]['data']
            assert vcon1.uuid in first_call_data

            # Second call should have vcon2 UUID
            second_call_data = mock_post.call_args_list[1][1]['data']
            assert vcon2.uuid in second_call_data
