# syntax=docker/dockerfile:1
# Frontend image — Next.js standalone. Build context is this frontend/ directory.

# ---- deps: install node_modules from the lockfile ----
FROM node:20-alpine AS deps
RUN apk add --no-cache libc6-compat
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---- builder: produce the standalone server bundle ----
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* are inlined at BUILD time, so they must be set now. Railway
# passes service variables as build args; an empty client id ships a no-op
# sign-in button, so set both before building.
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_GOOGLE_CLIENT_ID
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_PUBLIC_GOOGLE_CLIENT_ID=${NEXT_PUBLIC_GOOGLE_CLIENT_ID} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---- runner: minimal image, just the standalone server ----
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0

# output:"standalone" bundles a trimmed node_modules + server.js; static assets
# and public/ must be copied alongside it.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public

USER node
EXPOSE 3000
# server.js reads $PORT (Railway injects it; default 3000) and $HOSTNAME.
CMD ["node", "server.js"]
