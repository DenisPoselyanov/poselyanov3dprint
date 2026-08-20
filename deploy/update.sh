#!/bin/bash
# Оновлення коду на VPS після git push.
# Запуск: bash deploy/update.sh  (з /opt/poselyanov3dprint)
set -euo pipefail

cd "$(dirname "$0")/.."

echo "→ git pull"
git pull

echo "→ pip install"
.venv/bin/pip install -q -r requirements.txt

echo "→ update nginx config (best effort, потрібні sudo-права)"
if sudo -n cp deploy/nginx-api.conf /etc/nginx/sites-available/poselyanov3dprint 2>/dev/null \
   && sudo -n nginx -t >/dev/null 2>&1 \
   && sudo -n systemctl reload nginx 2>/dev/null; then
  echo "✓ nginx конфіг оновлено й перезавантажено"
else
  echo "⚠ Немає прав sudo (без пароля) на nginx — застосуйте deploy/nginx-api.conf вручну"
fi

echo "→ restart poselyanov3dprint"
sudo systemctl restart poselyanov3dprint

sleep 2
if systemctl is-active --quiet poselyanov3dprint; then
  echo "✓ Сервіс працює"
else
  echo "✗ Сервіс не запустився — див. journalctl -u poselyanov3dprint -n 30"
  exit 1
fi
