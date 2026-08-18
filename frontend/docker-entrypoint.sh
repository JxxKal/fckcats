#!/bin/sh
# Erzeugt die nginx-Konfiguration aus vorhandenem Zertifikat und Hostname.
# Beides wird von der API in das gemeinsame Volume /certs geschrieben.
set -e

CERT=/certs/cert.pem
KEY=/certs/key.pem
HOSTNAME_FILE=/certs/.hostname
CONF=/etc/nginx/conf.d/default.conf

# Der nach aussen sichtbare HTTPS-Port. nginx lauscht im Container immer auf
# 443, das Port-Mapping liegt ausserhalb -- ohne diesen Hinweis wuerde der
# Redirect von 80 auf einen Port zeigen, auf dem nichts horcht.
PUBLIC_HTTPS_PORT="${PUBLIC_HTTPS_PORT:-443}"
if [ "$PUBLIC_HTTPS_PORT" = "443" ]; then
  REDIRECT_HOST='$host'
else
  REDIRECT_HOST="\$host:$PUBLIC_HTTPS_PORT"
fi

# Ohne gesetzten Hostnamen faengt nginx alle Namen ab.
SERVER_NAME="_"
if [ -f "$HOSTNAME_FILE" ]; then
  HN=$(tr -d '[:space:]' < "$HOSTNAME_FILE")
  if [ -n "$HN" ]; then
    SERVER_NAME="$HN"
    echo "[nginx] Hostname: $SERVER_NAME"
  fi
fi

# Gemeinsame Bloecke, damit HTTP- und HTTPS-Variante nicht auseinanderlaufen.
common_locations() {
  cat <<'INNER'
    client_max_body_size 32m;

    root /usr/share/nginx/html;
    index index.html;
    resolver 127.0.0.11 valid=10s ipv6=off;

    # index.html nie cachen, sonst laedt der Browser nach einem Update noch das
    # alte Bundle. Die gehashten Assets duerfen dagegen unveraenderlich sein.
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, must-revalidate" always;
        # Das JWT liegt in localStorage, deshalb script-src 'self' gegen XSS.
        # style-src braucht 'unsafe-inline' fuer Tailwind.
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "same-origin" always;
    }

    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable" always;
    }

    location /api/ {
        set $upstream http://api:8000;
        proxy_pass $upstream;
        proxy_set_header Host            $host;
        proxy_set_header X-Real-IP       $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout    120s;
        proxy_send_timeout    120s;
        proxy_connect_timeout 10s;
    }

    location /health { return 200 "ok\n"; add_header Content-Type text/plain; }
INNER
}

if [ -f "$CERT" ] && [ -f "$KEY" ]; then
  echo "[nginx] Zertifikat gefunden - Port 443 mit Weiterleitung von 80"
  {
    echo "server {"
    echo "    listen 80;"
    echo "    server_name $SERVER_NAME;"
    echo "    return 301 https://${REDIRECT_HOST}\$request_uri;"
    echo "}"
    echo ""
    echo "server {"
    echo "    listen 443 ssl;"
    echo "    http2 on;"
    echo "    server_name $SERVER_NAME;"
    echo "    ssl_certificate     $CERT;"
    echo "    ssl_certificate_key $KEY;"
    echo "    ssl_protocols       TLSv1.2 TLSv1.3;"
    echo "    ssl_ciphers         HIGH:!aNULL:!MD5;"
    echo ""
    common_locations
    # Nur sinnvoll, wenn die Verbindung wirklich TLS ist.
    echo '    proxy_set_header X-Forwarded-Proto https;'
    echo "}"
  } > "$CONF"
else
  echo "[nginx] Kein Zertifikat - Port 80 ohne TLS"
  {
    echo "server {"
    echo "    listen 80;"
    echo "    server_name $SERVER_NAME;"
    echo ""
    common_locations
    echo "}"
  } > "$CONF"
fi

nginx -t
exec nginx -g "daemon off;"
