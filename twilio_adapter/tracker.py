"""State tracker to prevent reprocessing of recordings."""

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timezone


logger = logging.getLogger(__name__)


class StateTracker:
    """Tracks processed recordings to avoid duplicates."""

    def __init__(self, state_file: str):
        """Initialize state tracker.

        Args:
            state_file: Path to JSON file storing state
        """
        self.state_file = Path(state_file)
        self.state: Dict[str, Dict] = {}
        self._load()

    def _load(self):
        """Load state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    self.state = json.load(f)
                logger.info(f"Loaded state for {len(self.state)} processed recordings")
            except Exception as e:
                logger.error(f"Error loading state file: {e}")
                self.state = {}
        else:
            self.state = {}

    def _save(self):
        """Save state to file."""
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving state file: {e}")

    def is_processed(self, recording_sid: str) -> bool:
        """Check if recording has been processed.

        Args:
            recording_sid: Twilio RecordingSid

        Returns:
            True if recording has been processed
        """
        return recording_sid in self.state

    def mark_processed(
        self,
        recording_sid: str,
        vcon_uuid: str,
        status: str = "success",
        call_sid: Optional[str] = None,
        from_number: Optional[str] = None,
        to_number: Optional[str] = None
    ):
        """Mark recording as processed.

        Args:
            recording_sid: Twilio RecordingSid
            vcon_uuid: UUID of the created vCon
            status: Processing status (success, failed, etc.)
            call_sid: Optional Twilio CallSid
            from_number: Optional caller phone number
            to_number: Optional callee phone number
        """
        entry = {
            "vcon_uuid": vcon_uuid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": status
        }

        if call_sid:
            entry["call_sid"] = call_sid
        if from_number:
            entry["from"] = from_number
        if to_number:
            entry["to"] = to_number

        self.state[recording_sid] = entry
        self._save()
        logger.debug(f"Marked recording {recording_sid} as processed (status: {status})")

    def get_vcon_uuid(self, recording_sid: str) -> Optional[str]:
        """Get vCon UUID for a processed recording.

        Args:
            recording_sid: Twilio RecordingSid

        Returns:
            vCon UUID or None if not processed
        """
        entry = self.state.get(recording_sid)
        return entry.get("vcon_uuid") if entry else None

    def get_processing_status(self, recording_sid: str) -> Optional[str]:
        """Get processing status for a recording.

        Args:
            recording_sid: Twilio RecordingSid

        Returns:
            Processing status or None if not processed
        """
        entry = self.state.get(recording_sid)
        return entry.get("status") if entry else None
