#!/usr/bin/env bash
#
# start_live.sh - Start the AlgoEngine live trading system
#
# Usage: ./scripts/start_live.sh [options]
#
# Options:
#   --mode paper|live    Trading mode (default: paper)
#   --config <path>      Config file path (default: config/default.yaml)
#   --port <port>        Engine API port (default: 8000)
#   --dashboard          Also start the dashboard server
#   --help               Show this help message
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

MODE="paper"
CONFIG="config/default.yaml"
PORT=8000
START_DASHBOARD=false

usage() {
    grep '^#' "$0" | grep -v '#!/usr/bin/env' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)      MODE="$2"; shift 2 ;;
        --config)    CONFIG="$2"; shift 2 ;;
        --port)      PORT="$2"; shift 2 ;;
        --dashboard) START_DASHBOARD=true; shift ;;
        --help|-h)   usage ;;
        *)           echo "Unknown option: $1"; usage ;;
    esac
done

echo "[AlgoEngine] Starting live trading engine..."
echo "  Mode:   $MODE"
echo "  Config: $CONFIG"
echo "  Port:   $PORT"

cd "$PROJECT_DIR"

# Check if config file exists
if [[ ! -f "$CONFIG" ]]; then
    echo "[ERROR] Config file not found: $CONFIG"
    exit 1
fi

# Set environment variables
export ALGOENGINE_MODE="$MODE"
export ALGOENGINE_CONFIG="$CONFIG"
export ALGOENGINE_PORT="$PORT"

# Create logs directory
mkdir -p logs

# Start the engine
python -m cli.main --mode "$MODE" --config "$CONFIG" --port "$PORT" &
ENGINE_PID=$!
echo "[AlgoEngine] Engine PID: $ENGINE_PID"
echo $ENGINE_PID > "$PROJECT_DIR/.engine.pid"

# Wait for engine to start
sleep 2

if kill -0 $ENGINE_PID 2>/dev/null; then
    echo "[AlgoEngine] Engine started successfully on port $PORT"
else
    echo "[ERROR] Engine failed to start"
    exit 1
fi

# Optionally start dashboard
if [[ "$START_DASHBOARD" == true ]]; then
    echo "[AlgoEngine] Starting dashboard server..."
    cd "$PROJECT_DIR/dashboard"
    if [[ -f "node_modules/.bin/ts-node" ]]; then
        npx ts-node src/dashboard.ts &
        DASHBOARD_PID=$!
        echo "[AlgoEngine] Dashboard PID: $DASHBOARD_PID"
        echo $DASHBOARD_PID > "$PROJECT_DIR/.dashboard.pid"
    else
        echo "[WARNING] Dashboard dependencies not installed. Run: cd dashboard && npm install"
    fi
    cd "$PROJECT_DIR"
fi

echo ""
echo "[AlgoEngine] Live trading system is running."
echo "  Engine:  http://127.0.0.1:$PORT"
echo "  Dashboard: http://localhost:3000"
echo ""
echo "  To stop: ./scripts/stop_live.sh"