FROM node:22-alpine AS builder

WORKDIR /app

ENV NEXT_TELEMETRY_DISABLED=1

# pnpm 11 (what pnpm-lock.yaml and pnpm-workspace.yaml's allowBuilds need)
# requires Node >= 22.13.
RUN npm install -g pnpm@11.25.0 --no-audit --no-fund

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

# NEXT_PUBLIC_* vars must be declared as ARG+ENV here so they are baked
# into the bundle at build time (Next.js resolves them at compile, not runtime).
ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_SITE_URL
ARG NEXT_PUBLIC_AVAILABLE_MODELS
ARG NEXT_PUBLIC_DEFAULT_MODEL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_AVAILABLE_MODELS=$NEXT_PUBLIC_AVAILABLE_MODELS
ENV NEXT_PUBLIC_DEFAULT_MODEL=$NEXT_PUBLIC_DEFAULT_MODEL

RUN pnpm run build

# ── Runtime image ─────────────────────────────────────────────────────────────
FROM node:22-alpine

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# Required for Next.js standalone in Docker — otherwise it binds to the
# container's auto-set HOSTNAME (the container ID) and that hostname leaks
# into request.url-derived URLs in Server Components / Route Handlers.
ENV HOSTNAME=0.0.0.0

ARG NEXT_PUBLIC_API_URL
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_SITE_URL
ARG NEXT_PUBLIC_AVAILABLE_MODELS
ARG NEXT_PUBLIC_DEFAULT_MODEL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL
ENV NEXT_PUBLIC_SUPABASE_ANON_KEY=$NEXT_PUBLIC_SUPABASE_ANON_KEY
ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL
ENV NEXT_PUBLIC_AVAILABLE_MODELS=$NEXT_PUBLIC_AVAILABLE_MODELS
ENV NEXT_PUBLIC_DEFAULT_MODEL=$NEXT_PUBLIC_DEFAULT_MODEL

# standalone output bundles the minimal server + dependencies
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
