# =============================================================================
# AlgoEngine - Multi-stage Docker Build
# =============================================================================
#
# Stage 1: Build frontend (Node.js / TypeScript)
# Stage 2: Build Rust performance module
# Stage 3: Production runtime (Python + compiled artifacts)
#
# Usage:
#   docker build -t algoengine:latest .
#   docker run -p 8000:8000 algoengine:latest
# =============================================================================

# ---- Stage 1: TypeScript Dashboard Build ----
FROM node:22-alpine AS dashboard-builder

WORKDIR /build/dashboard

COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --only=production

COPY dashboard/tsconfig.json ./
COPY dashboard/src/ ./src/

RUN npx tsc --outDir dist

# ---- Stage 2: Rust Performance Module Build ----
FROM rust:1.85-alpine AS rust-builder

RUN apk add --no-cache musl-dev pkgconfig

WORKDIR /build/rust

COPY rust/ ./

RUN cargo build --release

# ---- Stage 3: Production Runtime ----
FROM python:3.12-slim

LABEL maintainer="AlgoEngine Team"
LABEL description="Algorithmic Trading Engine"
LABEL version="1.0.0"

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r algoengine && useradd -r -g algoengine -d /app algoengine

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Python source code
COPY src/ ./src/
COPY cli/ ./cli/
COPY config/ ./config/

# Copy compiled TypeScript dashboard
COPY --from=dashboard-builder /build/dashboard/dist/ ./dashboard/dist/
COPY --from=dashboard-builder /build/dashboard/node_modules/ ./dashboard/node_modules/
COPY dashboard/package.json ./dashboard/

# Copy compiled Rust binaries
COPY --from=rust-builder /build/rust/target/release/algoengine-data ./bin/

# Copy scripts
COPY scripts/ ./scripts/
RUN chmod +x ./scripts/*.sh

# Create data and logs directories
RUN mkdir -p data logs && \
    chown -R algoengine:algoengine /app

USER algoengine

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Expose ports
EXPOSE 8000

# Default command
CMD ["python", "-m", "cli.main", "--mode", "paper", "--config", "config/default.yaml", "--port", "8000"]