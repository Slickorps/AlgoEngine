#!/usr/bin/env bash
# =============================================================================
# AlgoEngine - GCP Deployment Script
# =============================================================================
# Deploys the full AlgoEngine stack to Google Cloud GKE or GCE.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - kubectl installed
#   - docker installed
#
# Usage:
#   ./scripts/deploy/gcp.sh [command] [options]
#
# Commands:
#   build          Build and push Docker images to GCR
#   gke            Deploy to Google Kubernetes Engine
#   gce            Deploy to Google Compute Engine via docker-compose
#   update         Update an existing deployment
#   status         Check deployment status
#   cleanup        Remove all GCP resources
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Color output ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ── Default configuration ─────────────────────────────────────────────────
GCP_PROJECT="${GCP_PROJECT:-algoengine}"
GCP_REGION="${GCP_REGION:-asia-east1}"
GCP_ZONE="${GCP_ZONE:-asia-east1-a}"
GKE_CLUSTER_NAME="${GKE_CLUSTER_NAME:-algoengine}"
GCP_NAMESPACE="${GCP_NAMESPACE:-algoengine}"

load_env() {
    if [ -f "$PROJECT_DIR/.env" ]; then
        set -a; source "$PROJECT_DIR/.env"; set +a
    fi
}

# ── Helper: get project ID ────────────────────────────────────────────────
get_project_id() {
    gcloud config get-value project 2>/dev/null || echo "$GCP_PROJECT"
}

# ── Build & Push to GCR ───────────────────────────────────────────────────
build() {
    log_info "Building and pushing Docker images to GCR..."

    local project_id
    project_id=$(get_project_id)

    # Set project
    gcloud config set project "$project_id"

    # Enable required APIs
    for api in containerregistry artifactregistry; do
        gcloud services enable "$api.googleapis.com" --quiet 2>/dev/null || true
    done

    local registry="gcr.io/$project_id"

    # Configure docker for GCR
    gcloud auth configure-docker --quiet

    # Build and push algoengine
    log_info "Building algoengine image..."
    docker build -t algoengine:latest -f "$PROJECT_DIR/Dockerfile" "$PROJECT_DIR"
    docker tag algoengine:latest "$registry/algoengine:latest"
    docker push "$registry/algoengine:latest"

    # Build and push dashboard
    if [ -f "$PROJECT_DIR/dashboard/Dockerfile" ]; then
        log_info "Building dashboard image..."
        docker build -t algoengine-dashboard:latest -f "$PROJECT_DIR/dashboard/Dockerfile" "$PROJECT_DIR/dashboard"
        docker tag algoengine-dashboard:latest "$registry/algoengine-dashboard:latest"
        docker push "$registry/algoengine-dashboard:latest"
    fi

    # Build and push monitor
    if [ -f "$PROJECT_DIR/monitor/Dockerfile" ]; then
        log_info "Building monitor image..."
        docker build -t algoengine-monitor:latest -f "$PROJECT_DIR/monitor/Dockerfile" "$PROJECT_DIR/monitor"
        docker tag algoengine-monitor:latest "$registry/algoengine-monitor:latest"
        docker push "$registry/algoengine-monitor:latest"
    fi

    log_ok "All images pushed to GCR: $registry"
}

# ── Deploy to GKE ─────────────────────────────────────────────────────────
gke() {
    log_info "Deploying to Google Kubernetes Engine..."

    local project_id
    project_id=$(get_project_id)

    # Enable GKE API if needed
    gcloud services enable container.googleapis.com --quiet 2>/dev/null || true

    # Create GKE cluster if needed
    if ! gcloud container clusters describe "$GKE_CLUSTER_NAME" --region "$GCP_REGION" &>/dev/null; then
        log_info "Creating GKE cluster: $GKE_CLUSTER_NAME (this may take 5-10 minutes)..."
        gcloud container clusters create "$GKE_CLUSTER_NAME" \
            --region "$GCP_REGION" \
            --num-nodes 2 \
            --machine-type e2-standard-2 \
            --enable-autoscaling \
            --min-nodes 1 \
            --max-nodes 4 \
            --release-channel stable \
            --quiet
        log_ok "GKE cluster created"
    else
        log_info "Using existing GKE cluster: $GKE_CLUSTER_NAME"
    fi

    # Get credentials
    gcloud container clusters get-credentials "$GKE_CLUSTER_NAME" --region "$GCP_REGION"

    # Update image tags in kustomization
    local registry="gcr.io/$project_id"

    cd "$PROJECT_DIR/k8s"
    kustomize edit set image algoengine="$registry/algoengine:latest"
    kustomize edit set image algoengine-dashboard="$registry/algoengine-dashboard:latest"
    kustomize edit set image algoengine-monitor="$registry/algoengine-monitor:latest"

    # Apply with kustomize
    log_info "Applying Kubernetes manifests..."
    kubectl apply -k .

    # Wait for deployments
    log_info "Waiting for deployments to be ready..."
    for deploy in algoengine algoengine-dashboard algoengine-monitor algoengine-redis; do
        kubectl wait --for=condition=Available --timeout=300s deployment/"$deploy" -n "$GCP_NAMESPACE" 2>/dev/null || \
            log_warn "Deployment $deploy not ready within timeout"
    done

    # Get ingress / services
    log_info "Services:"
    kubectl get svc -n "$GCP_NAMESPACE"
    log_info "Ingress:"
    kubectl get ingress -n "$GCP_NAMESPACE" 2>/dev/null || echo "No ingress configured"

    log_ok "GKE deployment complete"
}

# ── Deploy to GCE ─────────────────────────────────────────────────────────
gce() {
    log_info "Deploying to Google Compute Engine..."

    local instance_name="${1:-algoengine-vm}"
    local machine_type="${2:-e2-standard-2}"

    # Create firewall rules for the application ports
    gcloud compute firewall-rules create "$instance_name-allow-trading" \
        --allow tcp:8000,tcp:3000,tcp:9090 \
        --source-ranges 0.0.0.0/0 \
        --target-tags "$instance_name" 2>/dev/null || true

    # Create VM if needed
    if ! gcloud compute instances describe "$instance_name" --zone "$GCP_ZONE" &>/dev/null; then
        log_info "Creating GCE instance: $instance_name..."

        # Create startup script that installs Docker
        local startup_script=$(mktemp)
        cat > "$startup_script" << 'SCRIPT'
#!/bin/bash
apt-get update
apt-get install -y docker.io docker-compose
systemctl enable docker
systemctl start docker
usermod -aG docker "$USER"
SCRIPT

        gcloud compute instances create "$instance_name" \
            --zone "$GCP_ZONE" \
            --machine-type "$machine_type" \
            --image-family ubuntu-2204-lts \
            --image-project ubuntu-os-cloud \
            --tags "$instance_name" \
            --metadata-from-file startup-script="$startup_script" \
            --quiet

        rm -f "$startup_script"
        log_ok "GCE instance created: $instance_name"

        # Wait for instance to initialize
        log_info "Waiting for instance to initialize (60s)..."
        sleep 60
    else
        log_info "Using existing GCE instance: $instance_name"
    fi

    # Get instance external IP
    local external_ip
    external_ip=$(gcloud compute instances describe "$instance_name" \
        --zone "$GCP_ZONE" \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

    log_info "Deploying to GCE instance: $external_ip"

    # Transfer docker-compose files via gcloud scp
    local deploy_dir
    deploy_dir=$(mktemp -d)
    trap "rm -rf $deploy_dir" EXIT

    cp "$PROJECT_DIR/docker-compose.yml" "$deploy_dir/"
    [ -f "$PROJECT_DIR/.env" ] && cp "$PROJECT_DIR/.env" "$deploy_dir/"

    gcloud compute scp --zone "$GCP_ZONE" --recurse "$deploy_dir/"* \
        "$instance_name:~/algoengine/" 2>/dev/null || {
        gcloud compute ssh "$instance_name" --zone "$GCP_ZONE" --command "mkdir -p ~/algoengine"
        gcloud compute scp --zone "$GCP_ZONE" --recurse "$deploy_dir/"* \
            "$instance_name:~/algoengine/"
    }

    # SSH and start services
    gcloud compute ssh "$instance_name" --zone "$GCP_ZONE" --command \
        "cd ~/algoengine && sudo docker compose up -d"

    log_ok "GCE deployment complete. Dashboard: http://$external_ip:3000"
}

# ── Update ────────────────────────────────────────────────────────────────
update() {
    log_info "Updating GKE deployment..."
    kubectl rollout restart deployment -n "$GCP_NAMESPACE" --all
    kubectl rollout status deployment -n "$GCP_NAMESPACE" --timeout=300s
    log_ok "Deployment updated"
}

# ── Status ────────────────────────────────────────────────────────────────
status() {
    log_info "Checking deployment status..."

    echo ""
    echo "═══ GKE Status ════════════════════════════════════"
    kubectl get all -n "$GCP_NAMESPACE" 2>/dev/null || echo "Not connected to GKE"

    echo ""
    echo "═══ GCP Resources ═════════════════════════════════"
    local project_id
    project_id=$(get_project_id)
    gcloud container clusters list --format="table(name, status, location, currentNodeCount)" 2>/dev/null || true
}

# ── Cleanup ───────────────────────────────────────────────────────────────
cleanup() {
    log_warn "This will delete all GCP resources for AlgoEngine!"
    read -rp "Are you sure? (y/N): " confirm
    [ "$confirm" != "y" ] && [ "$confirm" != "Y" ] && { log_info "Cancelled"; exit 0; }

    log_info "Cleaning up GCP resources..."

    # Delete GKE cluster
    if gcloud container clusters describe "$GKE_CLUSTER_NAME" --region "$GCP_REGION" &>/dev/null; then
        log_info "Deleting GKE cluster: $GKE_CLUSTER_NAME..."
        gcloud container clusters delete "$GKE_CLUSTER_NAME" --region "$GCP_REGION" --quiet
    fi

    # Delete GCR images
    local project_id
    project_id=$(get_project_id)
    local registry="gcr.io/$project_id"
    for image in algoengine algoengine-dashboard algoengine-monitor; do
        gcloud container images delete "$registry/$image:latest" --quiet 2>/dev/null || true
    done

    log_ok "Cleanup complete"
}

# ── Main ──────────────────────────────────────────────────────────────────
main() {
    load_env
    local command="${1:-help}"
    shift 2>/dev/null || true

    case "$command" in
        build)     build ;;
        gke)       gke ;;
        gce)       gce "$@" ;;
        update)    update ;;
        status)    status ;;
        cleanup)   cleanup ;;
        help|--help|-h)
            echo "AlgoEngine GCP Deployment Script"
            echo ""
            echo "Usage:"
            echo "  ./scripts/deploy/gcp.sh build          Build & push images to GCR"
            echo "  ./scripts/deploy/gcp.sh gke            Deploy to GKE"
            echo "  ./scripts/deploy/gcp.sh gce [name]     Deploy to GCE VM"
            echo "  ./scripts/deploy/gcp.sh update         Update GKE deployment"
            echo "  ./scripts/deploy/gcp.sh status         Check status"
            echo "  ./scripts/deploy/gcp.sh cleanup        Remove all resources"
            ;;
        *)
            log_error "Unknown command: $command"
            exit 1
            ;;
    esac
}

main "$@"