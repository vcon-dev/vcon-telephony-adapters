#!/usr/bin/env python3
"""Main entry point for vCon telephony adapters.

Supports running individual adapters via command line argument:
    python main.py twilio      # Run Twilio adapter
    python main.py freeswitch  # Run FreeSWITCH adapter
    python main.py asterisk    # Run Asterisk adapter
    python main.py telnyx      # Run Telnyx adapter
    python main.py bandwidth   # Run Bandwidth adapter
    python main.py             # Default: Run Twilio adapter
"""

import sys
import logging
import uvicorn


def setup_logging(level: str = "INFO"):
    """Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def run_twilio_adapter():
    """Run the Twilio adapter."""
    from adapters.twilio import TwilioConfig, create_app

    # Load configuration
    config = TwilioConfig()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting vCon Twilio Adapter...")
    logger.info(f"Conserver URL: {config.conserver_url}")
    logger.info(f"Twilio signature validation: {config.validate_twilio_signature}")
    logger.info(f"Download recordings: {config.download_recordings}")
    logger.info(f"Recording format: {config.recording_format}")

    # Create FastAPI app
    app = create_app(config)

    # Run server
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower()
    )


def run_freeswitch_adapter():
    """Run the FreeSWITCH adapter."""
    from adapters.freeswitch import FreeSwitchConfig, create_app

    # Load configuration
    config = FreeSwitchConfig()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting vCon FreeSWITCH Adapter...")
    logger.info(f"Conserver URL: {config.conserver_url}")
    logger.info(f"Recordings path: {config.recordings_path}")
    logger.info(f"Webhook validation: {config.validate_webhook}")
    logger.info(f"Download recordings: {config.download_recordings}")
    logger.info(f"Recording format: {config.recording_format}")

    # Create FastAPI app
    app = create_app(config)

    # Run server
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower()
    )


def run_asterisk_adapter():
    """Run the Asterisk adapter."""
    from adapters.asterisk import AsteriskConfig, create_app

    # Load configuration
    config = AsteriskConfig()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting vCon Asterisk Adapter...")
    logger.info(f"Conserver URL: {config.conserver_url}")
    logger.info(f"ARI URL: {config.asterisk_ari_url}")
    logger.info(f"Recordings path: {config.recordings_path}")
    logger.info(f"Webhook validation: {config.validate_webhook}")
    logger.info(f"Download recordings: {config.download_recordings}")
    logger.info(f"Recording format: {config.recording_format}")

    # Create FastAPI app
    app = create_app(config)

    # Run server
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower()
    )


def run_telnyx_adapter():
    """Run the Telnyx adapter."""
    from adapters.telnyx import TelnyxConfig, create_app

    # Load configuration
    config = TelnyxConfig()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting vCon Telnyx Adapter...")
    logger.info(f"Conserver URL: {config.conserver_url}")
    logger.info(f"Telnyx API URL: {config.telnyx_api_url}")
    logger.info(f"Webhook validation: {config.validate_webhook}")
    logger.info(f"Download recordings: {config.download_recordings}")
    logger.info(f"Recording format: {config.recording_format}")

    # Create FastAPI app
    app = create_app(config)

    # Run server
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower()
    )


def run_bandwidth_adapter():
    """Run the Bandwidth adapter."""
    from adapters.bandwidth import BandwidthConfig, create_app

    # Load configuration
    config = BandwidthConfig()

    # Setup logging
    setup_logging(config.log_level)
    logger = logging.getLogger(__name__)

    logger.info("Starting vCon Bandwidth Adapter...")
    logger.info(f"Conserver URL: {config.conserver_url}")
    logger.info(f"Bandwidth Voice API: {config.bandwidth_voice_api_url}")
    logger.info(f"Webhook validation: {config.validate_webhook}")
    logger.info(f"Download recordings: {config.download_recordings}")
    logger.info(f"Recording format: {config.recording_format}")

    # Create FastAPI app
    app = create_app(config)

    # Run server
    logger.info(f"Starting server on {config.host}:{config.port}")
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower()
    )


# Registry of available adapters
ADAPTERS = {
    "twilio": run_twilio_adapter,
    "freeswitch": run_freeswitch_adapter,
    "asterisk": run_asterisk_adapter,
    "telnyx": run_telnyx_adapter,
    "bandwidth": run_bandwidth_adapter,
}


def main():
    """Main entry point."""
    try:
        # Get adapter name from command line (default to twilio)
        adapter_name = sys.argv[1] if len(sys.argv) > 1 else "twilio"

        if adapter_name in ("--help", "-h"):
            print("Usage: vcon-adapter [ADAPTER_NAME]")
            print(f"\nAvailable adapters: {', '.join(ADAPTERS.keys())}")
            print("\nDefault: twilio")
            print("\nEnvironment variables:")
            print("  CONSERVER_URL          URL of the vCon conserver (required)")
            print("  PORT                   Server port (default: 8080)")
            print("  HOST                   Server host (default: 0.0.0.0)")
            print("  LOG_LEVEL              Logging level (default: INFO)")
            print("  DOWNLOAD_RECORDINGS    Download recordings (default: true)")
            print("  RECORDING_FORMAT       Recording format: wav or mp3 (default: wav)")
            print("\nAdapter-specific variables vary by platform.")
            sys.exit(0)

        if adapter_name not in ADAPTERS:
            print(f"Unknown adapter: {adapter_name}", file=sys.stderr)
            print(f"Available adapters: {', '.join(ADAPTERS.keys())}", file=sys.stderr)
            sys.exit(1)

        # Run the selected adapter
        ADAPTERS[adapter_name]()

    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
