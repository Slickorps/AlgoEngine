#!/usr/bin/env bash
#
# deploy.sh - Deploy AlgoEngine to production
#
# Usage: ./scripts/deploy.sh [options]
#
# Options:
#   --env <name>         Deployment environment (staging|production) (default: staging)
#   --tag <tag>          Docker image tag (default: latest)
#   --compose-file <f>   Docker compose file (default: docker-compose.yml)
#   --no-cache           Build Docker images without cache
#   --help               Show this help message
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ENVIRONMENT="staging"
TAG="latest"
COMPOSE_FILE="docker-compose.yml"
NO_CACHE=""

usage() {
    grep '^#' "$0" | grep -v '#!/usr/bin/env' | sed 's/^# //' | sed 's/^#//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)          ENVIRONMENT="$2"; shift 2 ;;
        --tag)          TAG="$2"; shift 2 ;;
        --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
        --no-cache)     NO_CACHE="--no-cache"; shift ;;
        --help|-h)      usage ;;
        *)              echo "Unknown option: $1"; usage ;;
    esac
done

echo "[Deploy] Deploying AlgoEngine..."
echo "  Environment: $ENVIRONMENT"
echo "  Tag:         $TAG"
echo "  Compose:     $COMPOSE_FILE"

cd "$PROJECT_DIR"

# Validate environment
if [[ "$ENVIRONMENT" != "staging" && "$ENVIRONMENT" != "production" ]]; then
    echo "[ERROR] Invalid environment: $ENVIRONMENT. Use 'staging' or 'production'."
    exit 1
fi

# Check Docker
if ! command -v docker &>/dev/null; then
    echo "[ERROR] Docker is not installed."
    exit 1
fi

# Create .env file if missing
if [[ ! -f ".env" ]]; then
    echo "[Deploy] Creating .env file from template..."
    cat > .env << EOF
ALGOENGINE_ENV=$ENVIRONMENT
ALGOENGINE_PORT=8000
ALGOENGINE_MODE=paper
TZ=UTC
EOF
    echo "[Deploy] .env file created. Please review and update sensitive values."
fi

# Build Docker images
echo "[Deploy] Building Docker images..."
docker compose -f "$COMPOSE_FILE" build $NO_CACHE

# Pull latest base images
echo "[Deploy] Pulling base images..."
docker compose -f "$COMPOSE_FILE" pull

# Start services
echo "[Deploy] Starting services..."
docker compose -f "$COMPOSE_FILE" up -d

# Check health
echo "[Deploy] Checking service health..."
sleep 5
docker compose -f "$COMPOSE_FILE" ps

echo ""
echo "[Deploy] Deployment complete!"
echo "  Environment: $ENVIRONMENT"
echo "  Tag:         $TAG"
echo ""
echo "  To check logs:   docker compose -f $COMPOSE_FILE logs -f"
echo "  To stop:         docker compose -f $COMPOSE_FILE down"