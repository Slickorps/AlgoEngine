# =============================================================================
# AlgoEngine Monitor - Docker Build (Go)
# =============================================================================
#
# Multi-stage build:
#   Stage 1: Compile Go binary
#   Stage 2: Minimal runtime image
# =============================================================================

FROM golang:1.22-alpine AS builder

RUN apk add --no-cache git ca-certificates

WORKDIR /build

COPY go.mod ./
RUN go mod download

COPY main.go ./

RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o algoengine-monitor main.go

FROM alpine:3.19

RUN apk add --no-cache ca-certificates tzdata && \
    adduser -D -h /app monitor

WORKDIR /app

COPY --from=builder /build/algoengine-monitor .

USER monitor

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:9090/health || exit 1

ENTRYPOINT ["/app/algoengine-monitor"]