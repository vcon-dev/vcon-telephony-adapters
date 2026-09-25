#!/bin/bash
# Setup script for a local FreeSWITCH + vCon test environment (macOS/Homebrew).
# Run this on whichever machine hosts the local FreeSWITCH instance.
set -e

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

GITHUB_DIR="$HOME/Documents/GitHub"
FS_CONFIG="/opt/homebrew/etc/freeswitch"
FS_BIN="/opt/homebrew/opt/freeswitch/bin"

# ============================================
# Step 1: Verify FreeSWITCH is installed
# ============================================
log_info "=== Step 1: Verify FreeSWITCH ==="
if brew list freeswitch > /dev/null 2>&1; then
    log_info "FreeSWITCH is installed"
    $FS_BIN/freeswitch -version 2>/dev/null || log_warn "Could not get version"
else
    log_error "FreeSWITCH not installed. Run: brew install freeswitch"
    exit 1
fi

# ============================================
# Step 2: Docker Desktop + Redis
# ============================================
log_info "=== Step 2: Docker + Redis ==="
if docker info > /dev/null 2>&1; then
    log_info "Docker is running"
else
    log_warn "Docker is not running. Attempting to start Docker Desktop..."
    open -a Docker
    log_info "Waiting for Docker to start (up to 60s)..."
    for i in $(seq 1 60); do
        if docker info > /dev/null 2>&1; then
            log_info "Docker started after ${i}s"
            break
        fi
        sleep 1
    done
    if ! docker info > /dev/null 2>&1; then
        log_error "Docker failed to start. Please start Docker Desktop manually."
        exit 1
    fi
fi

# Start Redis
if docker ps --format '{{.Names}}' | grep -q '^redis$'; then
    log_info "Redis container already running"
elif docker ps -a --format '{{.Names}}' | grep -q '^redis$'; then
    log_info "Starting existing Redis container..."
    docker start redis
else
    log_info "Creating and starting Redis container..."
    docker run -d --name redis -p 6379:6379 redis:7-alpine
fi

# Verify Redis
sleep 2
if docker exec redis redis-cli ping 2>/dev/null | grep -q PONG; then
    log_info "Redis is responding"
else
    log_error "Redis is not responding"
    exit 1
fi

# ============================================
# Step 3: Clone repos
# ============================================
log_info "=== Step 3: Clone repos ==="
mkdir -p "$GITHUB_DIR"
cd "$GITHUB_DIR"

if [ -d "vcon-telephony-adapters" ]; then
    log_info "vcon-telephony-adapters already exists, pulling latest..."
    cd vcon-telephony-adapters && git pull && cd ..
else
    log_info "Cloning vcon-telephony-adapters..."
    gh repo clone vcon-dev/vcon-telephony-adapters
fi

if [ -d "vcon-real-time" ]; then
    log_info "vcon-real-time already exists, pulling latest..."
    cd vcon-real-time && git pull && cd ..
else
    log_info "Cloning vcon-real-time..."
    gh repo clone VCONIC/vcon-real-time
fi

if [ -d "vcon-freeswitch-tester" ]; then
    log_info "vcon-freeswitch-tester already exists, pulling latest..."
    cd vcon-freeswitch-tester && git pull && cd ..
else
    log_info "Cloning vcon-freeswitch-tester..."
    gh repo clone VCONIC/vcon-freeswitch-tester 2>/dev/null || {
        log_warn "Could not clone vcon-freeswitch-tester from GitHub, copying from local..."
    }
fi

# ============================================
# Step 4: Set up Python environments
# ============================================
log_info "=== Step 4: Python environments ==="

# vcon-telephony-adapters
log_info "Setting up vcon-telephony-adapters..."
cd "$GITHUB_DIR/vcon-telephony-adapters"
if [ ! -d ".venv" ]; then
    uv venv --python 3.12
fi
# Install from requirements.txt if it exists, else pyproject.toml
if [ -f "requirements.txt" ]; then
    uv pip install -r requirements.txt
elif [ -f "pyproject.toml" ]; then
    uv pip install -e "." 2>/dev/null || uv pip install -e ".[dev]" 2>/dev/null || {
        log_warn "Could not install from pyproject.toml, trying requirements..."
    }
fi

# vcon-real-time (skip GPU deps, use CPU torch)
log_info "Setting up vcon-real-time..."
cd "$GITHUB_DIR/vcon-real-time"
if [ ! -d ".venv" ]; then
    uv venv --python 3.12
fi
# Install CPU-only torch to save space, then install the package
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu 2>/dev/null || {
    log_warn "CPU-only torch not available for this platform, installing standard torch..."
    uv pip install torch torchaudio
}
uv pip install -e "."

# ============================================
# Step 5: Configure FreeSWITCH
# ============================================
log_info "=== Step 5: Configure FreeSWITCH ==="

# Ensure recordings directory exists
RECORDINGS_DIR="/opt/homebrew/var/lib/freeswitch/recordings"
mkdir -p "$RECORDINGS_DIR"
log_info "Recordings directory: $RECORDINGS_DIR"

# Ensure scripts directory exists
SCRIPTS_DIR="$FS_CONFIG/scripts"
mkdir -p "$SCRIPTS_DIR"

# Copy Lua webhook script
if [ -f "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/recording_webhook.lua" ]; then
    cp "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/recording_webhook.lua" "$SCRIPTS_DIR/"
    log_info "Installed recording_webhook.lua"
fi

# Backup and update event_socket config
if [ -f "$FS_CONFIG/autoload_configs/event_socket.conf.xml" ]; then
    cp "$FS_CONFIG/autoload_configs/event_socket.conf.xml" "$FS_CONFIG/autoload_configs/event_socket.conf.xml.bak"
fi
if [ -f "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/event_socket.conf.xml" ]; then
    cp "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/event_socket.conf.xml" "$FS_CONFIG/autoload_configs/"
    log_info "Updated event_socket.conf.xml"
fi

# Add test extensions to dialplan
DIALPLAN="$FS_CONFIG/dialplan/default.xml"
if [ -f "$DIALPLAN" ]; then
    if grep -q "test_record_1000" "$DIALPLAN"; then
        log_info "Test extensions already in dialplan"
    else
        log_info "Adding test extensions to dialplan..."
        cp "$DIALPLAN" "${DIALPLAN}.bak"
        # Insert test extensions before </context>
        if [ -f "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/test_extensions.xml" ]; then
            # Remove XML comments from test_extensions.xml and insert before </context>
            EXTENSIONS=$(grep -v '^\s*<!--' "$GITHUB_DIR/vcon-freeswitch-tester/freeswitch_configs/test_extensions.xml" | grep -v '^\s*-->')
            # Use python to do the insertion safely
            python3 -c "
import sys
with open('$DIALPLAN', 'r') as f:
    content = f.read()
extensions = '''$EXTENSIONS'''
# Insert before the last </context>
content = content.replace('</context>', extensions + '\n</context>', 1)
with open('$DIALPLAN', 'w') as f:
    f.write(content)
print('Test extensions added to dialplan')
"
        fi
    fi
else
    log_warn "Dialplan not found at $DIALPLAN"
fi

# ============================================
# Step 6: Create .env files
# ============================================
log_info "=== Step 6: Create .env files ==="

# Telephony adapter .env
cat > "$GITHUB_DIR/vcon-telephony-adapters/.env" << 'ENVEOF'
CONSERVER_URL=http://localhost:8000
PORT=8080
HOST=0.0.0.0
FREESWITCH_HOST=localhost
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon
FREESWITCH_RECORDINGS_PATH=/opt/homebrew/var/lib/freeswitch/recordings
DOWNLOAD_RECORDINGS=true
RECORDING_FORMAT=wav
VALIDATE_FREESWITCH_WEBHOOK=false
LOG_LEVEL=DEBUG
ENVEOF
log_info "Created vcon-telephony-adapters/.env"

# vcon-real-time .env
cat > "$GITHUB_DIR/vcon-real-time/.env" << 'ENVEOF'
STT_ENGINE=mock
WS_PORT=9090
HEALTH_PORT=8081
DASH_PORT=8091
REDIS_URL=redis://localhost:6379
ESL_HOST=localhost
ESL_PORT=8021
ESL_PASSWORD=ClueCon
CONSERVER_URL=http://localhost:8000
LOG_LEVEL=DEBUG
ENVEOF
log_info "Created vcon-real-time/.env"

# ============================================
# Step 7: Start FreeSWITCH
# ============================================
log_info "=== Step 7: Start FreeSWITCH ==="
if $FS_BIN/fs_cli -x "status" > /dev/null 2>&1; then
    log_info "FreeSWITCH already running, reloading config..."
    $FS_BIN/fs_cli -x "reloadxml"
else
    log_info "Starting FreeSWITCH..."
    brew services start freeswitch
    sleep 3
    if $FS_BIN/fs_cli -x "status" > /dev/null 2>&1; then
        log_info "FreeSWITCH started successfully"
    else
        log_error "FreeSWITCH failed to start. Check: brew services list"
    fi
fi

# ============================================
# Summary
# ============================================
echo ""
echo "============================================"
echo "  Setup Complete!"
echo "============================================"
echo ""
echo "Services:"
echo "  - Redis:      docker exec redis redis-cli ping"
echo "  - FreeSWITCH: $FS_BIN/fs_cli -x 'status'"
echo ""
echo "To run the test pipeline:"
echo "  1. cd $GITHUB_DIR/vcon-freeswitch-tester"
echo "  2. pip install -r requirements.txt  (for mock conserver)"
echo "  3. python mock_conserver.py &"
echo "  4. cd $GITHUB_DIR/vcon-telephony-adapters && .venv/bin/python main.py freeswitch &"
echo "  5. cd $GITHUB_DIR/vcon-real-time && STT_ENGINE=mock .venv/bin/vcon-realtime-server &"
echo "  6. cd $GITHUB_DIR/vcon-real-time && .venv/bin/vcon-realtime-bridge &"
echo "  7. $FS_BIN/fs_cli -x 'originate loopback/9196 &echo'"
echo ""
echo "Test scripts:"
echo "  - test_call.sh      - Make test calls via fs_cli"
echo "  - test_webhook.sh   - Simulate recording webhook"
echo "  - test_ws_client.py - WebSocket audio stream test"
echo "  - test_e2e.sh       - Full pipeline test"
echo ""
