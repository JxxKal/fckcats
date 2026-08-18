## ── Build ────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

## ── Auslieferung ─────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.sh /docker-entrypoint-fckcats.sh
RUN chmod +x /docker-entrypoint-fckcats.sh

EXPOSE 80 443
ENTRYPOINT ["/docker-entrypoint-fckcats.sh"]
