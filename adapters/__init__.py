"""Telephony platform adapters for vCon creation.

Each adapter provides:
- Platform-specific configuration (Config class)
- Recording data parser (RecordingData class)
- vCon builder (VconBuilder class)
- FastAPI webhook receiver (create_app function)

Available adapters:
- twilio: Twilio Programmable Voice
- freeswitch: FreeSWITCH PBX
- asterisk: Asterisk PBX
- telnyx: Telnyx Voice API
- bandwidth: Bandwidth Communications
"""

# Import all adapters for convenient access
from . import twilio
from . import freeswitch
from . import asterisk
from . import telnyx
from . import bandwidth

__all__ = [
    "twilio",
    "freeswitch",
    "asterisk",
    "telnyx",
    "bandwidth",
]
