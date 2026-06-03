#!/usr/bin/env bash
# =============================================================================
# AlgoEngine - AWS Deployment Script
# =============================================================================
# Deploys the full AlgoEngine stack to AWS EKS or EC2.
#
# Prerequisites:
#   - aws CLI installed and configured
#   - kubectl installed
#   - eksctl installed (for EKS deployment)
#   - docker installed
#
# Usage:
#   ./scripts/deploy/aws.sh [command] [options]
#
# Commands:
#   build          Build and push Docker images to ECR
#   eks            Deploy to Amazon EKS
#   ec2            Deploy to EC2 via docker-compose
#   update         Update an existing deployment
#   status         Check deployment status
#   cleanup        Remove all AWS resources
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
AWS_REGION="${AWS_REGION:-ap-southeast-1}"
AWS_CLUSTER_NAME="${AWS_CLUSTER_NAME:-algoengine}"
AWS_ECR_REPO="${AWS_ECR_REPO:-algoengine}"
AWS_NAMESPACE="${AWS_NAMESPACE:-algoengine}"

# ── Helper: load .env ─────────────────────────────────────────────────────
load_env() {
    if [ -f "$PROJECT_DIR/.env" ]; then
        set -a
        source "$PROJECT_DIR/.env"
        set +a
    fi
}

# ── Helper: get AWS account ID ────────────────────────────────────────────
get_aws_account_id() {
    aws sts get-caller-identity --query Account --output text
}

# ── Build & Push Docker images to ECR ─────────────────────────────────────
build() {
    log_info "Building and pushing Docker images to ECR..."

    local account_id
    account_id=$(get_aws_account_id)
    local registry="$account_id.dkr.ecr.$AWS_REGION.amazonaws.com"

    # Login to ECR
    log_info "Logging in to ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$registry"

    # Create ECR repositories if they don't exist
    for repo in algoengine algoengine-dashboard algoengine-monitor; do
        if ! aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" &>/dev/null; then
            log_info "Creating ECR repository: $repo"
            aws ecr create-repository --repository-name "$repo" --region "$AWS_REGION"
        fi
    done

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

    log_ok "All images pushed to ECR successfully"
}

# ── Deploy to EKS ─────────────────────────────────────────────────────────
eks() {
    log_info "Deploying to Amazon EKS..."

    # Check if cluster exists
    if ! eksctl get cluster --name "$AWS_CLUSTER_NAME" --region "$AWS_REGION" &>/dev/null; then
        log_info "Creating EKS cluster: $AWS_CLUSTER_NAME..."
        eksctl create cluster \
            --name "$AWS_CLUSTER_NAME" \
            --region "$AWS_REGION" \
            --nodegroup-name standard \
            --node-type t3.medium \
            --nodes 2 \
            --nodes-min 1 \
            --nodes-max 4 \
            --managed
        log_ok "EKS cluster created"
    else
        log_info "Using existing EKS cluster: $AWS_CLUSTER_NAME"
    fi

    # Update kubeconfig
    aws eks update-kubeconfig --name "$AWS_CLUSTER_NAME" --region "$AWS_REGION"

    # Update image tags in kustomization
    local account_id
    account_id=$(get_aws_account_id)
    local registry="$account_id.dkr.ecr.$AWS_REGION.amazonaws.com"

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
        kubectl wait --for=condition=Available --timeout=300s deployment/"$deploy" -n "$AWS_NAMESPACE" 2>/dev/null || \
            log_warn "Deployment $deploy not ready within timeout"
    done

    # Get ingress info
    log_info "Ingress endpoints:"
    kubectl get ingress -n "$AWS_NAMESPACE"

    log_ok "EKS deployment complete"
}

# ── Deploy to EC2 ─────────────────────────────────────────────────────────
ec2() {
    log_info "Deploying to EC2 via docker-compose..."

    local instance_id="$1"
    if [ -z "$instance_id" ]; then
        log_error "Usage: $0 ec2 <instance-id>"
        log_error "Provide the EC2 instance ID to deploy to"
        exit 1
    fi

    # Get instance public IP
    local public_ip
    public_ip=$(aws ec2 describe-instances \
        --instance-ids "$instance_id" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' \
        --output text)

    if [ -z "$public_ip" ] || [ "$public_ip" == "None" ]; then
        log_error "Instance $instance_id does not have a public IP"
        exit 1
    fi

    log_info "Deploying to EC2 instance: $instance_id ($public_ip)"

    # Create temporary deploy directory
    local deploy_dir=$(mktemp -d)
    trap "rm -rf $deploy_dir" EXIT

    # Copy required files
    cp -r "$PROJECT_DIR/docker-compose.yml" "$deploy_dir/"
    cp -r "$PROJECT_DIR/.env" "$deploy_dir/" 2>/dev/null || true

    # Copy Dockerfiles and source (or just use pre-built images)
    if [ -n "${DOCKER_HOST:-}" ]; then
        # Remote docker
        export DOCKER_HOST="$DOCKER_HOST"
    else
        # Copy files via SCP
        log_info "Uploading files to EC2..."
        scp -o StrictHostKeyChecking=no -r \
            "$deploy_dir/"* \
            "ec2-user@$public_ip:~/algoengine/"
    fi

    # SSH and run docker-compose
    log_info "Starting services on EC2..."
    ssh -o StrictHostKeyChecking=no "ec2-user@$public_ip" \
        "cd ~/algoengine && \
         docker compose pull && \
         docker compose up -d"

    log_ok "EC2 deployment complete. Dashboard: http://$public_ip:3000"
}

# ── Update existing deployment ────────────────────────────────────────────
update() {
    log_info "Updating existing deployment..."

    # Pull latest images
    docker-compose pull

    # Recreate services with new images
    docker-compose up -d --force-recreate

    log_ok "Deployment updated"
}

# ── Status ────────────────────────────────────────────────────────────────
status() {
    log_info "Checking deployment status..."

    echo ""
    echo "═══ K8s Status ═══════════════════════════════════"
    kubectl get all -n "$AWS_NAMESPACE" 2>/dev/null || echo "Not connected to Kubernetes cluster"

    echo ""
    echo "═══ Docker Status ═════════════════════════════════"
    docker-compose ps 2>/dev/null || echo "Docker Compose not running"

    echo ""
    echo "═══ AWS Status ════════════════════════════════════"
    aws ecs describe-clusters --cluster "$AWS_CLUSTER_NAME" 2>/dev/null | jq -r '.clusters[] | "Cluster: \(.clusterName) - Status: \(.status)"' 2>/dev/null || true
}

# ── Cleanup ───────────────────────────────────────────────────────────────
cleanup() {
    log_warn "This will delete all AWS resources for AlgoEngine!"
    read -rp "Are you sure? (y/N): " confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        log_info "Cleanup cancelled"
        exit 0
    fi

    log_info "Cleaning up AWS resources..."

    # Delete EKS cluster
    if eksctl get cluster --name "$AWS_CLUSTER_NAME" --region "$AWS_REGION" &>/dev/null; then
        log_info "Deleting EKS cluster: $AWS_CLUSTER_NAME..."
        eksctl delete cluster --name "$AWS_CLUSTER_NAME" --region "$AWS_REGION" --wait
    fi

    # Delete ECR repositories
    for repo in algoengine algoengine-dashboard algoengine-monitor; do
        if aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" &>/dev/null; then
            log_info "Deleting ECR repository: $repo"
            aws ecr delete-repository --repository-name "$repo" --region "$AWS_REGION" --force
        fi
    done

    log_ok "Cleanup complete"
}

# ── Main ──────────────────────────────────────────────────────────────────
main() {
    load_env

    local command="${1:-help}"
    shift 2>/dev/null || true

    case "$command" in
        build)
            build
            ;;
        eks)
            eks
            ;;
        ec2)
            ec2 "$@"
            ;;
        update)
            update
            ;;
        status)
            status
            ;;
        cleanup)
            cleanup
            ;;
        help|--help|-h)
            echo "AlgoEngine AWS Deployment Script"
            echo ""
            echo "Usage:"
            echo "  ./scripts/deploy/aws.sh build          Build & push images to ECR"
            echo "  ./scripts/deploy/aws.sh eks            Deploy to EKS"
            echo "  ./scripts/deploy/aws.sh ec2 <id>       Deploy to EC2 instance"
            echo "  ./scripts/deploy/aws.sh update         Update deployment"
            echo "  ./scripts/deploy/aws.sh status         Check status"
            echo "  ./scripts/deploy/aws.sh cleanup        Remove all resources"
            ;;
        *)
            log_error "Unknown command: $command"
            echo "Usage: $0 {build|eks|ec2|update|status|cleanup}"
            exit 1
            ;;
    esac
}

main "$@"