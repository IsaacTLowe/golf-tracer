# syntax=docker/dockerfile:1.7
# Build from the repo root: docker build -f apps/web/Dockerfile .
FROM node:24.18.0-slim AS build
RUN npm install -g pnpm@12.14.0
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json ./
COPY apps/web/package.json apps/web/
COPY packages/tracer-spec/package.json packages/tracer-spec/
COPY packages/api-client/package.json packages/api-client/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile --filter "@golf-tracer/web..."
COPY packages/tracer-spec packages/tracer-spec
COPY packages/api-client packages/api-client
COPY apps/web apps/web
RUN pnpm --filter @golf-tracer/web build

FROM node:24.18.0-slim
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
WORKDIR /app
COPY --from=build --chown=node:node /repo/apps/web/.next/standalone ./
COPY --from=build --chown=node:node /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=node:node /repo/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
