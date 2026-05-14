#!/usr/bin/env bash
#
# setup_env.sh - Set up the AlgoEngine development environment
#
# Usage: ./scripts/setup_env.sh [options]
#
# Options:
#   --full        Install all dependencies (Python + Node.js + system tools)
#   --python      Install Python dependencies only
#   --node        Install Node.js dashboard dependencies only
#   --dev         Install development dependencies (test, lint, etc.)
#   --help        Show this help message
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage() {
    grep '^#' "$0" | grep -v '#!/usr/bin/env' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

MODE=""

if [[ $# -eq 0 ]]; then
    MODE="full"
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full)   MODE="full"; shift ;;
        --python) MODE="python"; shift ;;
        --node)   MODE="node"; shift ;;
        --dev)    MODE="dev"; shift ;;
        --help|-h) usage ;;
        *)        echo "Unknown option: $1"; usage ;;
    esac
done

echo "[AlgoEngine] Setting up environment..."
echo "  Mode: ${MODE:-full}"
echo "  Project: $PROJECT_DIR"

check_command() {
    if ! command -v "$1" &>/dev/null; then
        echo "[WARNING] $1 is not installed. Please install it first."
        return 1
    fi
    echo "[OK] $1 found: $($1 --version 2>&1 | head -1)"
    return 0
}

# Check system requirements
echo ""
echo "--- System Requirements ---"
check_command python3 || check_command python
check_command pip3 || check_command pip
check_command node
check_command npm

PYTHON_CMD="python3"
if ! command -v python3 &>/dev/null; then
    PYTHON_CMD="python"
fi

PIP_CMD="pip3"
if ! command -v pip3 &>/dev/null; then
    PIP_CMD="pip"
fi

# --- Python setup ---
if [[ "$MODE" == "full" ]] || [[ "$MODE" == "python" ]] || [[ "$MODE" == "dev" ]]; then
    echo ""
    echo "--- Python Dependencies ---"

    cd "$PROJECT_DIR"

    if [[ -f "requirements.txt" ]]; then
        echo "Installing production dependencies..."
        $PIP_CMD install -r requirements.txt
    fi

    if [[ -f "requirements-dev.txt" ]] && [[ "$MODE" == "dev" || "$MODE" == "full" ]]; then
        echo "Installing development dependencies..."
        $PIP_CMD install -r requirements-dev.txt
    fi

    # Install package in editable mode for development
    if [[ "$MODE" == "dev" ]]; then
        echo "Installing package in editable mode..."
        $PIP_CMD install -e .
    fi
    
    echo "[OK] Python dependencies installed."
fi

# --- Node.js / Dashboard setup ---
if [[ "$MODE" == "full" ]] || [[ "$MODE" == "node" ]]; then
    echo ""
    echo "--- Dashboard Dependencies ---"

    if [[ -d "$PROJECT_DIR/dashboard" ]]; then
        cd "$PROJECT_DIR/dashboard"
        npm install
        echo "[OK] Dashboard dependencies installed."
    fi

    cd "$PROJECT_DIR"
fi

echo ""
echo "[AlgoEngine] Environment setup complete."
echo ""
echo "Quick start:"
echo "  ./scripts/start_live.sh --mode paper    # Start paper trading engine"
echo "  cd dashboard && npm run dev              # Start dashboard in dev mode"
echo "  python -m pytest tests/                  # Run tests"