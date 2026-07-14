# syntax=docker/dockerfile:1
FROM node:20-alpine
WORKDIR /app

# .npmrc first: it decides which registry the install below talks to.
COPY .npmrc package.json package-lock.json* ./

# The cache mount survives across builds, so a rebuild after changing one
# dependency re-downloads one dependency, not all of them.
# `npm ci` when there's a lockfile (deterministic, and faster than install).
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .
EXPOSE 3000
CMD ["npm", "run", "dev"]
