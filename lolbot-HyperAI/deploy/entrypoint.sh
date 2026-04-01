#!/usr/bin/env bash
###############################################################################
# OperatorRL lolbot-HyperAI · Container Entrypoint
# 自部署 自环境反馈 自演化
#
# Responsibilities:
#   1. Pre-flight checks (Python version, data dirs, dependencies)
#   2. Recovery detection (was last shutdown clean?)
#   3. Mode selection (full / test / dashboard / shell)
#   4. Signal forwarding for graceful shutdown
#
# Usage (inside container):
#   ./entrypoint.sh --mode full        # Normal operation
#   ./entrypoint.sh --mode test        # Run test suite
#   ./entrypoint.sh --mode dashboard   # Dashboard only (no game capture)
#   ./entrypoint.sh --mode shell       # Interactive shell for debugging
###############################################################################

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_DIR="/app/lolbot-HyperAI"
DATA_DIR="${LOLBOT_DATA_DIR:-/data}"
LOG_DIR="${LOLBOT_LOG_DIR:-/data/logs}"
CHECKPOINT_DIR="${LOLBOT_CHECKPOINT_DIR:-/data/checkpoints}"
LOG_LEVEL="${LOLBOT_LOG_LEVEL:-INFO}"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }
log_step()  { echo -e "${BLUE}[STEP]${NC}  $(date '+%H:%M:%S') $*"; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight_checks() {
    log_step "Running pre-flight checks..."

    # Python version
    PYTHON_VER=$(python3 --version 2>&1)
    log_info "Python: $PYTHON_VER"

    # Check required directories
    for dir in "$DATA_DIR" "$LOG_DIR" "$CHECKPOINT_DIR"; do
        if [ ! -d "$dir" ]; then
            log_warn "Creating directory: $dir"
            mkdir -p "$dir"
        fi
    done

    # Check core modules are importable
    python3 -c "
import sys
sys.path.insert(0, '$APP_DIR')
errors = []
for mod in [
    'runtime.process_manager',
    'runtime.health_monitor',
    'runtime.error_recovery',
    'integration.event_dispatcher',
    'integration.pipeline_builder',
]:
    try:
        __import__(mod)
    except ImportError as e:
        errors.append(f'{mod}: {e}')

if errors:
    print('Import errors:')
    for e in errors:
        print(f'  - {e}')
    sys.exit(1)
else:
    print('All core modules importable')
" || {
        log_error "Core module import check failed"
        exit 1
    }

    log_info "Pre-flight checks passed"
}

# ---------------------------------------------------------------------------
# Recovery detection
# ---------------------------------------------------------------------------
check_recovery() {
    log_step "Checking for dirty shutdown..."

    CHECKPOINT_FILE="$CHECKPOINT_DIR/.lolbot_checkpoint.json"
    if [ -f "$CHECKPOINT_FILE" ]; then
        CLEAN=$(python3 -c "
import json
with open('$CHECKPOINT_FILE') as f:
    data = json.load(f)
print(data.get('clean', True))
" 2>/dev/null || echo "True")

        if [ "$CLEAN" = "False" ]; then
            log_warn "DIRTY SHUTDOWN DETECTED — recovery will be triggered on startup"
        else
            log_info "Last shutdown was clean"
        fi
    else
        log_info "No checkpoint file found — first run"
    fi
}

# ---------------------------------------------------------------------------
# Mode: full — run the main game assistant
# ---------------------------------------------------------------------------
run_full() {
    log_step "Starting lolbot-HyperAI in FULL mode..."
    log_info "Base tick: ${LOLBOT_BASE_TICK_MS:-10}ms"
    log_info "TTS backend: ${LOLBOT_TTS_BACKEND:-edge}"
    log_info "Log level: $LOG_LEVEL"

    cd "$APP_DIR"

    # Check if main_loop.py exists (Claude #1's file)
    if [ -f "core/main_loop.py" ]; then
        exec python3 -u core/main_loop.py \
            --log-level "$LOG_LEVEL" \
            --checkpoint-dir "$CHECKPOINT_DIR" \
            --data-dir "$DATA_DIR"
    elif [ -f "main.py" ]; then
        exec python3 -u main.py
    else
        # Fallback: run a minimal startup that at least validates the system
        log_warn "No main_loop.py found — running system validation..."
        exec python3 -u -c "
import asyncio
import sys
sys.path.insert(0, '.')
from runtime.process_manager import ProcessManager, _DummyComponent

async def main():
    print('lolbot-HyperAI runtime validation')
    pm = ProcessManager(base_tick_ms=100, enable_signal_handling=True)
    pm.register(_DummyComponent('validation.heartbeat'), interval_ms=1000, priority=0)
    print('System initialized — waiting for game client connection...')
    print('(All Claude #1/#2 modules will be loaded when available)')
    await pm.start()

asyncio.run(main())
"
    fi
}

# ---------------------------------------------------------------------------
# Mode: test — run test suite
# ---------------------------------------------------------------------------
run_test() {
    log_step "Running test suite..."
    cd "$APP_DIR"

    python3 -m pytest tests/ \
        -v \
        --tb=short \
        --color=yes \
        -x \
        2>&1 | tee "$LOG_DIR/test_results_$(date +%Y%m%d_%H%M%S).log"
}

# ---------------------------------------------------------------------------
# Mode: dashboard — metrics/health dashboard only
# ---------------------------------------------------------------------------
run_dashboard() {
    log_step "Starting dashboard-only mode..."
    log_warn "Dashboard mode not yet implemented — starting minimal health server"

    python3 -c "
import http.server
import json

class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'overall': 'healthy', 'mode': 'dashboard'}).encode())
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass

server = http.server.HTTPServer(('0.0.0.0', 8080), HealthHandler)
print('Health endpoint listening on :8080')
server.serve_forever()
"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo "============================================"
    echo "  lolbot-HyperAI · OperatorRL"
    echo "  自部署 · 自环境反馈 · 自演化"
    echo "============================================"

    # Parse arguments
    MODE="full"
    while [[ $# -gt 0 ]]; do
        case $1 in
            --mode)
                MODE="$2"
                shift 2
                ;;
            *)
                shift
                ;;
        esac
    done

    preflight_checks
    check_recovery

    case "$MODE" in
        full)
            run_full
            ;;
        test)
            run_test
            ;;
        dashboard)
            run_dashboard
            ;;
        shell)
            log_step "Starting interactive shell..."
            exec /bin/bash
            ;;
        *)
            log_error "Unknown mode: $MODE"
            echo "Usage: entrypoint.sh --mode [full|test|dashboard|shell]"
            exit 1
            ;;
    esac
}

# Trap signals for graceful shutdown
trap 'log_info "Received SIGTERM"; kill -TERM $! 2>/dev/null; wait' SIGTERM
trap 'log_info "Received SIGINT"; kill -INT $! 2>/dev/null; wait' SIGINT

main "$@"
