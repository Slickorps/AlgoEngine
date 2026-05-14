# =============================================================================
# AlgoEngine Dashboard - Docker Build
# =============================================================================
#
# Stage 1: Build TypeScript source
# Stage 2: Serve with Node.js
# =============================================================================

FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY tsconfig.json ./
COPY src/ ./src/

RUN npx tsc --outDir dist

FROM node:22-alpine AS runner

WORKDIR /app

RUN apk add --no-cache curl

COPY package.json package-lock.json ./
RUN npm ci --only=production

COPY --from=builder /app/dist ./dist

USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/health || exit 1

CMD ["node", "dist/server.js"]