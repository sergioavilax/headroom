# Headroom's console. Four stages, because `make ui-check` and CI need the toolchain and
# the shipped image must not carry it.
#
#   deps    node_modules (dev included) — the layer everything else caches on
#   check   eslint + tsc + the unit tests           (`docker build --target check`)
#   e2e     the Playwright smoke against a stub gateway (`--target e2e`)
#   runner  `next build --output standalone` + nothing else
#
# Nothing here reaches a gateway, a database, or a provider. `next build` is a pure
# function of this directory, which is what lets CI build it with no services and no keys
# (BUILD_PLAN §0.2 invariant 4).

FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS source
COPY . .

# --- lint, typecheck, unit tests -----------------------------------------------------
FROM source AS check
CMD ["npm", "run", "check"]

# --- the browser smoke ----------------------------------------------------------------
# The image Playwright publishes, because Chromium needs system libraries Alpine does not
# carry and chasing them by hand is a maintenance job nobody signed up for. Chromium only:
# three engines would triple a job that exists to prove the console renders at all.
FROM mcr.microsoft.com/playwright:v1.62.1-noble AS e2e
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
CMD ["npm", "run", "e2e"]

# --- what actually ships ---------------------------------------------------------------
FROM source AS builder
RUN npm run build

FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Not root. The console holds an operator's admin session; a container that runs as root
# for no reason is a habit worth not having by the time Phase 10 puts this on a cluster.
RUN addgroup -g 1001 -S nodejs && adduser -S -u 1001 -G nodejs nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
