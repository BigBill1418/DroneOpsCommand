# Build stage
FROM node:20-alpine AS build
WORKDIR /app

# Phase-5 observability build args — Vite inlines every `VITE_*` that's
# set at build time into the bundled JS. Compose (prod + demo override)
# passes these through; unset = frontend Sentry init no-ops.
ARG VITE_SENTRY_DSN=""
ARG VITE_SENTRY_ENVIRONMENT="production"
ARG VITE_APP_VERSION="dev"
ENV VITE_SENTRY_DSN=${VITE_SENTRY_DSN} \
    VITE_SENTRY_ENVIRONMENT=${VITE_SENTRY_ENVIRONMENT} \
    VITE_APP_VERSION=${VITE_APP_VERSION}

COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine

# Install curl for Docker healthcheck
RUN apk add --no-cache curl

# Security headers and config
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Run nginx as non-root
RUN chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx /var/log/nginx \
    && touch /var/run/nginx.pid && chown nginx:nginx /var/run/nginx.pid

USER nginx

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
