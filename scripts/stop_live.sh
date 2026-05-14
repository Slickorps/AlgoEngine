#!/usr/bin/env bash
#
# stop_live.sh - Stop the AlgoEngine live trading system
#
# Usage: ./scripts/stop_live.sh [options]
#
# Options:
#   --force    Force kill (SIGKILL) instead of graceful shutdown
#   --help     Show this help message
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

FORCE=false

usage() {
    grep '^#' "$0" | grep -v '#!/usr/bin/env' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)  FORCE=true; shift ;;
        --help|-h) usage ;;
        *)        echo "Unknown option: $1"; usage ;;
    esac
done

SIGNAL="SIGTERM"
SIGNAL_NUM=15
if [[ "$FORCE" == true ]]; then
    SIGNAL="SIGKILL"
    SIGNAL_NUM=9
fi

echo "[AlgoEngine] Stopping live trading system..."
echo "  Signal: $SIGNAL"

# Stop dashboard if running
if [[ -f "$PROJECT_DIR/.dashboard.pid" ]]; then
    DASHBOARD_PID=$(cat "$PROJECT_DIR/.dashboard.pid")
    if kill -0 $DASHBOARD_PID 2>/dev/null; then
        echo "[AlgoEngine] Stopping dashboard (PID: $DASHBOARD_PID)..."
        kill -$SIGNAL_NUM $DASHBOARD_PID 2>/dev/null || true
        wait $DASHBOARD_PID 2>/dev/null || true
        echo "[AlgoEngine] Dashboard stopped."
    fi
    rm -f "$PROJECT_DIR/.dashboard.pid"
fi

# Stop engine if running
if [[ -f "$PROJECT_DIR/.engine.pid" ]]; then
    ENGINE_PID=$(cat "$PROJECT_DIR/.engine.pid")
    if kill -0 $ENGINE_PID 2>/dev/null; then
        echo "[AlgoEngine] Stopping engine (PID: $ENGINE_PID)..."
        if [[ "$FORCE" != true ]]; then
            # Graceful shutdown: send SIGTERM and wait
            kill -SIGTERM $ENGINE_PID 2>/dev/null || true
            for i in $(seq 1 10); do
                if ! kill -0 $ENGINE_PID 2>/dev/null; then
                    break
                fi
                sleep 1
            done
            # Force kill if still running after timeout
            if kill -0 $ENGINE_PID 2>/dev/null; then
                echo "[AlgoEngine] Engine did not stop gracefully, forcing..."
                kill -9 $ENGINE_PID 2>/dev/null || true
            fi
        else
            kill -9 $ENGINE_PID 2>/dev/null || true
        fi
        echo "[AlgoEngine] Engine stopped."
    fi
    rm -f "$PROJECT_DIR/.engine.pid"
fi

echo "[AlgoEngine] Live trading system stopped."