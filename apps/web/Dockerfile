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
# The web app depends on the workspace client, so its manifest has to be in the
# image before `pnpm install` can resolve the link.
COPY packages/api-client/package.json ./packages/api-client/
RUN pnpm install --frozen-lockfile --filter web...

FROM deps AS builder
# Copy the two workspace directories the build reads, wholesale. A per-file list
# would invalidate this layer on every documentation or workflow change and would
# need an edit each time apps/web gains a folder, while a full `COPY . .` would
# pull the whole repository in. The blanket copy is bounded to apps/web and
# packages/api-client; the host node_modules and .next never reach the build
# context (see .dockerignore), and the dependencies installed in the previous
# stage are kept because COPY merges into the directories that are already there.
COPY apps/web ./apps/web
COPY packages/api-client ./packages/api-client
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
