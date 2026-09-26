#!/bin/bash
# Simulate a FreeSWITCH recording webhook to the telephony adapter
set -e

ADAPTER_URL="${ADAPTER_URL:-http://localhost:8080}"
UUID="${1:-$(uuidgen | tr '[:upper:]' '[:lower:]')}"
RECORDING_FILE="${2:-/opt/homebrew/var/lib/freeswitch/recordings/test_recording.wav}"

echo "=== FreeSWITCH Webhook Test ==="
echo "Adapter URL: $ADAPTER_URL"
echo "Call UUID: $UUID"
echo "Recording file: $RECORDING_FILE"
echo ""

# Check adapter health
echo "--- Checking adapter health ---"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$ADAPTER_URL/health" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "Adapter is healthy"
    curl -s "$ADAPTER_URL/health" | python3 -m json.tool 2>/dev/null || true
else
    echo "WARNING: Adapter health check returned HTTP $HTTP_CODE"
    echo "Make sure the telephony adapter is running: cd ~/Documents/GitHub/vcon-telephony-adapters && python main.py freeswitch"
fi
echo ""

# Send recording webhook
echo "--- Sending recording webhook ---"
TIMESTAMP=$(date +%s)

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$ADAPTER_URL/webhook/recording" \
    -H "Content-Type: application/json" \
    -d "{
        \"uuid\": \"$UUID\",
        \"call_uuid\": \"$UUID\",
        \"caller_id_number\": \"+15551234567\",
        \"caller_id_name\": \"Test Caller\",
        \"destination_number\": \"+15559876543\",
        \"direction\": \"inbound\",
        \"duration\": \"30\",
        \"record_seconds\": \"28\",
        \"start_epoch\": \"$TIMESTAMP\",
        \"recording_file\": \"$RECORDING_FILE\",
        \"context\": \"default\",
        \"accountcode\": \"test\",
        \"sip_user_agent\": \"FreeSWITCH-Test\"
    }")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

echo "HTTP Status: $HTTP_CODE"
echo "Response: $BODY"
echo ""

# Check recording status
echo "--- Checking recording status ---"
STATUS_RESPONSE=$(curl -s -w "\n%{http_code}" "$ADAPTER_URL/status/$UUID" 2>/dev/null)
STATUS_CODE=$(echo "$STATUS_RESPONSE" | tail -1)
STATUS_BODY=$(echo "$STATUS_RESPONSE" | head -n -1)

if [ "$STATUS_CODE" = "200" ]; then
    echo "Status: $STATUS_BODY" | python3 -m json.tool 2>/dev/null || echo "$STATUS_BODY"
else
    echo "Status endpoint returned HTTP $STATUS_CODE"
fi

echo ""
echo "=== Webhook Test Complete ==="
