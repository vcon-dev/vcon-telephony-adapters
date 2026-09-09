"""Base configuration management for telephony adapters."""

import os

from dotenv import load_dotenv


class BaseConfig:
    """Base configuration class with common settings for all adapters.

    Subclasses should extend this to add platform-specific settings.
    """

    def __init__(self, env_file: str | None = None):
        """Load configuration from .env file.

        Args:
            env_file: Optional path to .env file
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        # Server settings
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8080"))

        # Conserver settings (required)
        self.conserver_url = os.getenv("CONSERVER_URL")
        if not self.conserver_url:
            raise ValueError("CONSERVER_URL environment variable is required")

        # Optional conserver authentication
        self.conserver_api_token = os.getenv("CONSERVER_API_TOKEN")
        self.conserver_header_name = os.getenv("CONSERVER_HEADER_NAME", "x-conserver-api-token")

        # State tracking
        self.state_file = os.getenv("STATE_FILE", ".adapter_state.json")

        # Ingress lists for vCon routing
        ingress_lists_str = os.getenv("INGRESS_LISTS", "")
        self.ingress_lists: list[str] = [
            item.strip() for item in ingress_lists_str.split(",") if item.strip()
        ]

        # --- Media handling -------------------------------------------
        # Where call audio goes. "embed" inlines base64 into the vCon, which
        # is the historical default and makes a vCon roughly 1.3x the size of
        # the audio: a few minutes of WAV produces a multi-megabyte object.
        # "s3" or "filesystem" re-host the audio and put a `url` plus a
        # `content_hash` in the dialog instead, which is ~800x smaller and is
        # what any real deployment should use.
        #
        # Re-hosting is not optional for some platforms: Telnyx hands back a
        # pre-signed URL that expires in 600s, so referencing it directly
        # yields a vCon whose audio link is dead within ten minutes.
        self.media_backend = os.getenv("MEDIA_BACKEND", "embed").strip().lower()
        self.media_base_url = os.getenv("MEDIA_BASE_URL")
        self.media_filesystem_path = os.getenv("MEDIA_FILESYSTEM_PATH")
        self.media_s3_bucket = os.getenv("MEDIA_S3_BUCKET")
        self.media_s3_region = os.getenv("MEDIA_S3_REGION")
        self.media_s3_prefix = os.getenv("MEDIA_S3_PREFIX", "")
        # Set for any S3-compatible store (DigitalOcean Spaces, MinIO, Telnyx
        # Cloud Storage). Leave unset for AWS.
        self.media_s3_endpoint_url = os.getenv("MEDIA_S3_ENDPOINT_URL")

        # Recording download settings
        self.download_recordings = os.getenv("DOWNLOAD_RECORDINGS", "true").lower() in (
            "true",
            "1",
            "yes",
        )

        # Recording format preference (wav or mp3)
        self.recording_format = os.getenv("RECORDING_FORMAT", "wav").lower()
        if self.recording_format not in ("wav", "mp3"):
            raise ValueError(
                f"RECORDING_FORMAT must be 'wav' or 'mp3', got: {self.recording_format}"
            )

        # Logging level
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    def get_headers(self) -> dict[str, str]:
        """Get HTTP headers for conserver requests."""
        headers = {"Content-Type": "application/json"}
        if self.conserver_api_token:
            headers[self.conserver_header_name] = self.conserver_api_token
        return headers

    def build_publisher(self):
        """Construct the configured `AudioPublisher`, or None to embed.

        Raises rather than silently falling back to embedding: a deployment
        that asked for S3 and quietly got multi-megabyte inline vCons instead
        is worse than one that refuses to start.
        """
        from .media_publisher import FilesystemPublisher, S3Publisher

        backend = self.media_backend
        if backend in ("", "embed", "inline", "none"):
            return None

        if backend == "filesystem":
            if not self.media_filesystem_path:
                raise ValueError("MEDIA_BACKEND=filesystem requires MEDIA_FILESYSTEM_PATH")
            return FilesystemPublisher(
                destination=self.media_filesystem_path,
                base_url=self.media_base_url,
            )

        if backend == "s3":
            if not self.media_s3_bucket:
                raise ValueError("MEDIA_BACKEND=s3 requires MEDIA_S3_BUCKET")
            return S3Publisher(
                bucket=self.media_s3_bucket,
                region=self.media_s3_region,
                prefix=self.media_s3_prefix,
                endpoint_url=self.media_s3_endpoint_url,
                base_url=self.media_base_url,
            )

        raise ValueError(f"Unknown MEDIA_BACKEND {backend!r}; use embed, filesystem or s3")
