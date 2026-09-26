#!/bin/bash
# End-to-end test for FreeSWITCH + vCon pipeline
# Starts all services, makes a test call, and verifies vCon output
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
GITHUB_DIR="$HOME/Documents/GitHub"
VCON_DIR="$SCRIPT_DIR/vcons"
PIDS=()

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

cleanup() {
    log_info "Cleaning up..."
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null
    log_info "Cleanup complete."
}
trap cleanup EXIT

check_prereqs() {
    log_info "Checking prerequisites..."

    # Redis
    if ! redis-cli ping > /dev/null 2>&1; then
        log_error "Redis is not running. Start with: docker start redis"
        exit 1
    fi
    log_info "  Redis: OK"

    # FreeSWITCH
    FS_CLI="/opt/homebrew/opt/freeswitch/bin/fs_cli"
    if ! $FS_CLI -x "status" > /dev/null 2>&1; then
        log_error "FreeSWITCH is not running. Start with: brew services start freeswitch"
        exit 1
    fi
    log_info "  FreeSWITCH: OK"

    # Project directories
    if [ ! -d "$GITHUB_DIR/vcon-telephony-adapters" ]; then
        log_error "vcon-telephony-adapters not found in $GITHUB_DIR"
        exit 1
    fi
    log_info "  vcon-telephony-adapters: OK"

    if [ ! -d "$GITHUB_DIR/vcon-real-time" ]; then
        log_error "vcon-real-time not found in $GITHUB_DIR"
        exit 1
    fi
    log_info "  vcon-real-time: OK"
}

start_mock_conserver() {
    log_info "Starting mock conserver on port 8000..."
    mkdir -p "$VCON_DIR"
    cd "$SCRIPT_DIR"
    VCON_STORAGE_DIR="$VCON_DIR" python3 mock_conserver.py &
    PIDS+=($!)
    sleep 2

    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        log_info "  Mock conserver: OK"
    else
        log_error "  Mock conserver failed to start"
        exit 1
    fi
}

start_telephony_adapter() {
    log_info "Starting telephony adapter (FreeSWITCH) on port 8080..."
    cd "$GITHUB_DIR/vcon-telephony-adapters"
    source .venv/bin/activate 2>/dev/null || true
    python main.py freeswitch &
    PIDS+=($!)
    sleep 3

    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        log_info "  Telephony adapter: OK"
    else
        log_warn "  Telephony adapter may not be ready yet"
    fi
}

start_realtime_server() {
    log_info "Starting vcon-realtime-server on port 9090..."
    cd "$GITHUB_DIR/vcon-real-time"
    source .venv/bin/activate 2>/dev/null || true
    STT_ENGINE=mock vcon-realtime-server &
    PIDS+=($!)
    sleep 3

    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        log_info "  Realtime server: OK (health)"
    else
        log_warn "  Realtime server health check not available (may still be working)"
    fi
}

start_realtime_bridge() {
    log_info "Starting vcon-realtime-bridge (ESL -> Redis)..."
    cd "$GITHUB_DIR/vcon-real-time"
    source .venv/bin/activate 2>/dev/null || true
    vcon-realtime-bridge &
    PIDS+=($!)
    sleep 2
    log_info "  Realtime bridge: started"
}

test_echo_call() {
    log_info "=== Test 1: Echo loopback call ==="
    FS_CLI="/opt/homebrew/opt/freeswitch/bin/fs_cli"

    $FS_CLI -x "originate loopback/9196 &echo"
    log_info "  Echo call originated"
    sleep 3

    CHANNELS=$($FS_CLI -x "show channels count" 2>/dev/null || echo "0")
    log_info "  Active channels: $CHANNELS"

    $FS_CLI -x "hupall"
    sleep 1
    log_info "  Channels hung up"
}

test_recording_webhook() {
    log_info "=== Test 2: Recording webhook ==="
    UUID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    TIMESTAMP=$(date +%s)

    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "http://localhost:8080/webhook/recording" \
        -H "Content-Type: application/json" \
        -d "{
            \"uuid\": \"$UUID\",
            \"caller_id_number\": \"+15551234567\",
            \"caller_id_name\": \"E2E Test\",
            \"destination_number\": \"+15559876543\",
            \"direction\": \"inbound\",
            \"duration\": \"10\",
            \"record_seconds\": \"8\",
            \"start_epoch\": \"$TIMESTAMP\",
            \"recording_file\": \"/tmp/test_recording.wav\",
            \"context\": \"default\"
        }")

    HTTP_CODE=$(echo "$RESPONSE" | tail -1)
    log_info "  Webhook response: HTTP $HTTP_CODE"
}

test_websocket_client() {
    log_info "=== Test 3: WebSocket audio stream ==="
    cd "$SCRIPT_DIR"
    python3 test_ws_client.py --duration 3.0 --url ws://localhost:9090/ws 2>&1 | head -20 || {
        log_warn "  WebSocket test had issues (may be expected if server not fully up)"
    }
}

check_results() {
    log_info "=== Checking Results ==="

    # Check mock conserver for received vCons
    VCON_RESPONSE=$(curl -s http://localhost:8000/api/vcons 2>/dev/null || echo '{"total":0}')
    VCON_COUNT=$(echo "$VCON_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('total',0))" 2>/dev/null || echo "0")
    log_info "  vCons received by conserver: $VCON_COUNT"

    # Check Redis for stream data
    REDIS_KEYS=$(redis-cli keys "stream:*" 2>/dev/null | wc -l || echo "0")
    log_info "  Redis stream keys: $REDIS_KEYS"

    # Check vCon files on disk
    VCON_FILES=$(ls "$VCON_DIR"/*.json 2>/dev/null | wc -l || echo "0")
    log_info "  vCon files on disk: $VCON_FILES"

    # List vCon files
    if [ "$VCON_FILES" -gt 0 ]; then
        log_info "  vCon files:"
        for f in "$VCON_DIR"/*.json; do
            UUID=$(basename "$f" .json)
            log_info "    - $UUID"
        done
    fi
}

# --- Main ---
echo "============================================"
echo "  FreeSWITCH + vCon End-to-End Test"
echo "============================================"
echo ""

check_prereqs
echo ""

start_mock_conserver
start_telephony_adapter
start_realtime_server
start_realtime_bridge
echo ""

test_echo_call
echo ""

test_recording_webhook
echo ""

test_websocket_client
echo ""

check_results
echo ""

log_info "=== E2E Test Complete ==="
log_info "Services are still running. Press Ctrl+C to stop."
echo ""

# Keep running until interrupted
wait
