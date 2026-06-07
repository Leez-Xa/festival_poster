#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/festival_poster}"
APP_USER="${APP_USER:-www-data}"
BASIC_AUTH_USER="${BASIC_AUTH_USER:-demo}"

cd "$APP_DIR"

echo "[festival-poster] Installing OS packages..."
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx apache2-utils

echo "[festival-poster] Preparing Python environment..."
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "[festival-poster] Creating .env from .env.example..."
  cp .env.example .env
  python3 - <<'PY'
from pathlib import Path

path = Path(".env")
text = path.read_text(encoding="utf-8")
replacements = {
    "APP_ENV=production": "APP_ENV=demo",
    "AI_REQUIRE_IMAGE_FUSION=true": "AI_REQUIRE_IMAGE_FUSION=false",
    "CORS_ALLOW_LOCALHOST=true": "CORS_ALLOW_LOCALHOST=false",
}
for old, new in replacements.items():
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")
PY
  echo "[festival-poster] .env created for demo fallback. Edit .env later to enable real AI keys."
fi

echo "[festival-poster] Preparing persistent storage..."
mkdir -p storage uploads logs
sudo chown -R "$APP_USER:$APP_USER" storage logs
sudo chmod 640 .env
sudo chown "$APP_USER:$APP_USER" .env

echo "[festival-poster] Installing systemd service..."
sudo cp deploy/aliyun/systemd/festival-poster.service /etc/systemd/system/festival-poster.service
sudo systemctl daemon-reload
sudo systemctl enable festival-poster.service
sudo systemctl restart festival-poster.service

echo "[festival-poster] Configuring nginx..."
sudo cp deploy/aliyun/nginx/festival-poster.conf /etc/nginx/sites-available/festival-poster
if [ ! -f /etc/nginx/.festival-poster.htpasswd ]; then
  echo "[festival-poster] Create shared demo password for nginx Basic Auth."
  sudo htpasswd -c /etc/nginx/.festival-poster.htpasswd "$BASIC_AUTH_USER"
fi
if [ ! -L /etc/nginx/sites-enabled/festival-poster ]; then
  sudo ln -s /etc/nginx/sites-available/festival-poster /etc/nginx/sites-enabled/festival-poster
fi
sudo nginx -t
sudo systemctl reload nginx

echo "[festival-poster] Done."
echo "Open: http://SERVER_IP_OR_DOMAIN/"
echo "Health: curl http://127.0.0.1:8000/health"
