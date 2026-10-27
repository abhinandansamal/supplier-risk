# Context service: the vendored Meterless World Model behind a small HTTP API.
# node:24-alpine (Node 24.22.1), pinned by index digest.
ARG NODE_IMAGE=node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53

FROM ${NODE_IMAGE} AS build
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
COPY vendor ./vendor
RUN npm run build

FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    CONTEXT_SERVICE_HOST=0.0.0.0 \
    CONTEXT_SERVICE_PORT=7070 \
    CONTEXT_SERVICE_DATA_DIR=/data
WORKDIR /app
COPY package.json package-lock.json ./
# Security: SEC-086. The runtime needs node only; npm, npx, yarn and corepack are removed.
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force \
 && rm -rf /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/corepack /opt/yarn* \
      /usr/local/bin/npm /usr/local/bin/npx /usr/local/bin/corepack /usr/local/bin/yarn /usr/local/bin/yarnpkg /root/.npm
COPY --from=build /build/dist ./dist
RUN mkdir -p /data && chown node:node /data
USER node
EXPOSE 7070
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=5 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:7070/health').then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"]
CMD ["node", "--max-old-space-size=160", "dist/server.js"]
