#!/bin/bash
# Test call via FreeSWITCH fs_cli
# Makes a test call to the echo extension (9196) to verify FreeSWITCH is working
set -e

FS_CLI="/opt/homebrew/opt/freeswitch/bin/fs_cli"

echo "=== FreeSWITCH Test Call ==="

# Check FreeSWITCH is running
if ! $FS_CLI -x "status" > /dev/null 2>&1; then
    echo "ERROR: FreeSWITCH is not running. Start with: brew services start freeswitch"
    exit 1
fi

echo "FreeSWITCH is running."
echo ""

# Show current channels
echo "Current channels:"
$FS_CLI -x "show channels"
echo ""

# Test 1: Echo test (loopback call to echo extension)
echo "--- Test 1: Echo loopback call ---"
$FS_CLI -x "originate loopback/9196 &echo"
echo "Echo call originated. Waiting 3 seconds..."
sleep 3

# Show active channels
echo "Active channels:"
$FS_CLI -x "show channels"
echo ""

# Hang up all channels
echo "Hanging up all channels..."
$FS_CLI -x "hupall"
sleep 1

# Test 2: Record test call (calls extension that records)
echo "--- Test 2: Record test call ---"
RECORDING_FILE="/opt/homebrew/var/lib/freeswitch/recordings/test_$(date +%s).wav"
$FS_CLI -x "originate loopback/9196 &record($RECORDING_FILE 5 16000)"
echo "Recording call originated. Recording for 5 seconds..."
sleep 6

# Hang up
$FS_CLI -x "hupall"
sleep 1

# Check if recording was created
if [ -f "$RECORDING_FILE" ]; then
    echo "Recording created: $RECORDING_FILE"
    ls -la "$RECORDING_FILE"
else
    echo "WARNING: Recording file not found at $RECORDING_FILE"
    echo "Checking recordings directory:"
    ls -la /opt/homebrew/var/lib/freeswitch/recordings/ 2>/dev/null || echo "  Directory does not exist"
fi

echo ""
echo "=== Test Complete ==="
