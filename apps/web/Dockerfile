# syntax=docker/dockerfile:1
# Build context is the repository root, not apps/web.

FROM node:24.22.3-slim AS base
ENV PNPM_HOME=/pnpm
ENV PATH=$PNPM_HOME:$PATH
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && corepack prepare pnpm@12.18.0 --activate
WORKDIR /repo

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json ./apps/web/
RUN pnpm install --frozen-lockfile --filter web...

FROM deps AS builder
# Copy only the sources the build needs. The build context has no
# .dockerignore, so a blanket `COPY . .` would also overwrite the installed
# node_modules with host artifacts.
COPY apps/web/next.config.ts apps/web/tsconfig.json apps/web/eslint.config.mjs ./apps/web/
COPY apps/web/app ./apps/web/app
COPY apps/web/lib ./apps/web/lib
COPY apps/web/public ./apps/web/public
RUN pnpm --filter web build

FROM node:24.22.3-slim AS runner
WORKDIR /repo
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

COPY --from=builder --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=node:node /repo/apps/web/public ./apps/web/public

USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
