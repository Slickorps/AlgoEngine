#!/usr/bin/env bash
# =============================================================================
# AlgoEngine - Azure Deployment Script
# =============================================================================
# Deploys the full AlgoEngine stack to Azure AKS or VM.
#
# Prerequisites:
#   - az CLI installed and logged in
#   - kubectl installed
#   - docker installed
#
# Usage:
#   ./scripts/deploy/azure.sh [command] [options]
#
# Commands:
#   build          Build and push Docker images to ACR
#   aks            Deploy to Azure Kubernetes Service
#   vm             Deploy to Azure VM via docker-compose
#   update         Update an existing deployment
#   status         Check deployment status
#   cleanup        Remove all Azure resources
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
AZURE_LOCATION="${AZURE_LOCATION:-eastasia}"
AZURE_RG="${AZURE_RG:-algoengine-rg}"
AZURE_AKS_NAME="${AZURE_AKS_NAME:-algoengine-aks}"
AZURE_ACR_NAME="${AZURE_ACR_NAME:-algoengineacr}"
AZURE_NAMESPACE="${AZURE_NAMESPACE:-algoengine}"

load_env() {
    if [ -f "$PROJECT_DIR/.env" ]; then
        set -a; source "$PROJECT_DIR/.env"; set +a
    fi
}

# ── Build & Push to ACR ──────────────────────────────────────────────────
build() {
    log_info "Building and pushing Docker images to ACR..."

    # Create resource group if needed
    az group create --name "$AZURE_RG" --location "$AZURE_LOCATION" --output none

    # Create ACR if needed
    if ! az acr show --name "$AZURE_ACR_NAME" --resource-group "$AZURE_RG" &>/dev/null; then
        log_info "Creating ACR: $AZURE_ACR_NAME..."
        az acr create --name "$AZURE_ACR_NAME" --resource-group "$AZURE_RG" \
            --sku Standard --admin-enabled true --output none
    fi

    local registry="$AZURE_ACR_NAME.azurecr.io"

    # Login to ACR
    az acr login --name "$AZURE_ACR_NAME"

    # Build images
    log_info "Building algoengine image..."
    az acr build --registry "$AZURE_ACR_NAME" --image algoengine:latest \
        --file "$PROJECT_DIR/Dockerfile" "$PROJECT_DIR"

    if [ -f "$PROJECT_DIR/dashboard/Dockerfile" ]; then
        log_info "Building dashboard image..."
        az acr build --registry "$AZURE_ACR_NAME" --image algoengine-dashboard:latest \
            --file "$PROJECT_DIR/dashboard/Dockerfile" "$PROJECT_DIR/dashboard"
    fi

    if [ -f "$PROJECT_DIR/monitor/Dockerfile" ]; then
        log_info "Building monitor image..."
        az acr build --registry "$AZURE_ACR_NAME" --image algoengine-monitor:latest \
            --file "$PROJECT_DIR/monitor/Dockerfile" "$PROJECT_DIR/monitor"
    fi

    log_ok "All images pushed to ACR: $registry"
}

# ── Deploy to AKS ─────────────────────────────────────────────────────────
aks() {
    log_info "Deploying to Azure Kubernetes Service..."

    # Create AKS cluster if needed
    if ! az aks show --name "$AZURE_AKS_NAME" --resource-group "$AZURE_RG" &>/dev/null; then
        log_info "Creating AKS cluster: $AZURE_AKS_NAME..."
        az aks create \
            --name "$AZURE_AKS_NAME" \
            --resource-group "$AZURE_RG" \
            --node-count 2 \
            --node-vm-size Standard_B2s \
            --enable-managed-identity \
            --generate-ssh-keys \
            --output none
        log_ok "AKS cluster created"

        # Attach ACR
        az aks update \
            --name "$AZURE_AKS_NAME" \
            --resource-group "$AZURE_RG" \
            --attach-acr "$AZURE_ACR_NAME" \
            --output none
    else
        log_info "Using existing AKS cluster: $AZURE_AKS_NAME"
    fi

    # Get credentials
    az aks get-credentials --name "$AZURE_AKS_NAME" --resource-group "$AZURE_RG" --overwrite-existing

    # Update image names for Azure
    local registry="$AZURE_ACR_NAME.azurecr.io"

    cd "$PROJECT_DIR/k8s"
    kustomize edit set image algoengine="$registry/algoengine:latest"
    kustomize edit set image algoengine-dashboard="$registry/algoengine-dashboard:latest"
    kustomize edit set image algoengine-monitor="$registry/algoengine-monitor:latest"

    # Apply manifests
    log_info "Applying Kubernetes manifests..."
    kubectl apply -k .

    # Wait for deployments
    for deploy in algoengine algoengine-dashboard algoengine-monitor algoengine-redis; do
        kubectl wait --for=condition=Available --timeout=300s deployment/"$deploy" -n "$AZURE_NAMESPACE" 2>/dev/null || \
            log_warn "Deployment $deploy not ready within timeout"
    done

    # Get service IPs
    log_info "Services:"
    kubectl get svc -n "$AZURE_NAMESPACE"

    log_ok "AKS deployment complete"
}

# ── Deploy to Azure VM ────────────────────────────────────────────────────
vm() {
    log_info "Deploying to Azure VM..."

    local vm_name="${1:-algoengine-vm}"
    local vm_size="${2:-Standard_B2s}"

    # Create VM if needed
    if ! az vm show --name "$vm_name" --resource-group "$AZURE_RG" &>/dev/null; then
        log_info "Creating VM: $vm_name..."
        az vm create \
            --name "$vm_name" \
            --resource-group "$AZURE_RG" \
            --image Ubuntu2204 \
            --size "$vm_size" \
            --admin-username azureuser \
            --generate-ssh-keys \
            --custom-data <(cat <<-EOF
#!/bin/bash
apt-get update
apt-get install -y docker.io docker-compose
systemctl enable docker
systemctl start docker
usermod -aG docker azureuser
EOF
            ) \
            --output none

        # Open ports
        az vm open-port --name "$vm_name" --resource-group "$AZURE_RG" \
            --port 8000,3000,9090 --priority 100 --output none
    fi

    local public_ip
    public_ip=$(az vm show --name "$vm_name" --resource-group "$AZURE_RG" \
        --show-details --query publicIps --output tsv)

    log_info "Deploying to VM: $public_ip"

    # Copy files and deploy
    local deploy_dir
    deploy_dir=$(mktemp -d)
    trap "rm -rf $deploy_dir" EXIT

    cp "$PROJECT_DIR/docker-compose.yml" "$deploy_dir/"
    [ -f "$PROJECT_DIR/.env" ] && cp "$PROJECT_DIR/.env" "$deploy_dir/"

    scp -o StrictHostKeyChecking=no -r "$deploy_dir/"* "azureuser@$public_ip:~/algoengine/" 2>/dev/null || {
        # First time may need to create the directory
        ssh -o StrictHostKeyChecking=no "azureuser@$public_ip" "mkdir -p ~/algoengine"
        scp -o StrictHostKeyChecking=no -r "$deploy_dir/"* "azureuser@$public_ip:~/algoengine/"
    }

    ssh -o StrictHostKeyChecking=no "azureuser@$public_ip" \
        "cd ~/algoengine && sudo docker compose up -d"

    log_ok "VM deployment complete. Dashboard: http://$public_ip:3000"
}

# ── Update ────────────────────────────────────────────────────────────────
update() {
    log_info "Updating AKS deployment..."
    kubectl rollout restart deployment -n "$AZURE_NAMESPACE" --all
    kubectl rollout status deployment -n "$AZURE_NAMESPACE" --timeout=300s
    log_ok "Deployment updated"
}

# ── Status ────────────────────────────────────────────────────────────────
status() {
    log_info "Checking deployment status..."

    echo ""
    echo "═══ AKS Status ═══════════════════════════════════"
    kubectl get all -n "$AZURE_NAMESPACE" 2>/dev/null || echo "Not connected to AKS"

    echo ""
    echo "═══ Azure Resources ══════════════════════════════"
    az aks show --name "$AZURE_AKS_NAME" --resource-group "$AZURE_RG" \
        --query "{Name:name, Status:provisioningState, K8sVersion:kubernetesVersion}" \
        -o tsv 2>/dev/null || echo "AKS not found"
}

# ── Cleanup ───────────────────────────────────────────────────────────────
cleanup() {
    log_warn "This will delete all Azure resources for AlgoEngine!"
    read -rp "Are you sure? (y/N): " confirm
    [ "$confirm" != "y" ] && [ "$confirm" != "Y" ] && { log_info "Cancelled"; exit 0; }

    log_info "Deleting resource group: $AZURE_RG..."
    az group delete --name "$AZURE_RG" --yes --no-wait
    log_ok "Cleanup initiated"
}

# ── Main ──────────────────────────────────────────────────────────────────
main() {
    load_env
    local command="${1:-help}"
    shift 2>/dev/null || true

    case "$command" in
        build)     build ;;
        aks)       aks ;;
        vm)        vm "$@" ;;
        update)    update ;;
        status)    status ;;
        cleanup)   cleanup ;;
        help|--help|-h)
            echo "AlgoEngine Azure Deployment Script"
            echo ""
            echo "Usage:"
            echo "  ./scripts/deploy/azure.sh build          Build & push images to ACR"
            echo "  ./scripts/deploy/azure.sh aks            Deploy to AKS"
            echo "  ./scripts/deploy/azure.sh vm [name]      Deploy to Azure VM"
            echo "  ./scripts/deploy/azure.sh update         Update AKS deployment"
            echo "  ./scripts/deploy/azure.sh status         Check status"
            echo "  ./scripts/deploy/azure.sh cleanup        Remove all resources"
            ;;
        *)
            log_error "Unknown command: $command"
            exit 1
            ;;
    esac
}

main "$@"