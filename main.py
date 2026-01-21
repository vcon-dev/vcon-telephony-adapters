#!/usr/bin/env python3
"""Main entry point for Twilio recording vCon adapter."""

import sys
import logging
import uvicorn
from twilio_adapter.config import Config
from twilio_adapter.webhook import create_app


def setup_logging(level: str = "INFO"):
    """Configure logging for the application.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def main():
    """Main entry point."""
    try:
        # Load configuration
        config = Config()

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

    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
