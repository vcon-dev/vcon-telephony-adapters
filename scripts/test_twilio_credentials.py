#!/usr/bin/env python3
"""Test Twilio credentials by fetching account info from the Twilio API."""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import requests

load_dotenv()

ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")


def test_twilio_credentials() -> bool:
    """Verify Twilio credentials by fetching account info."""
    if not ACCOUNT_SID or not AUTH_TOKEN:
        print("ERROR: TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set in .env")
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}.json"
    response = requests.get(url, auth=(ACCOUNT_SID, AUTH_TOKEN), timeout=10)

    if response.status_code == 200:
        data = response.json()
        print("Twilio credentials are valid.")
        print(f"  Account SID: {data.get('sid')}")
        print(f"  Friendly Name: {data.get('friendly_name')}")
        print(f"  Status: {data.get('status')}")
        return True
    else:
        print(f"ERROR: Twilio API returned status {response.status_code}")
        try:
            err = response.json()
            print(f"  Code: {err.get('code')}")
            print(f"  Message: {err.get('message')}")
        except Exception:
            print(f"  Body: {response.text[:200]}")
        return False


if __name__ == "__main__":
    success = test_twilio_credentials()
    sys.exit(0 if success else 1)
