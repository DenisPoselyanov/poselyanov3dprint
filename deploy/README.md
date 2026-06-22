# Deploy on VPS TheHost.ua (Cloud-1)

## Prerequisites

- Ubuntu 22.04/24.04
- Domain `api.yourdomain.ua` → VPS IP
- Python 3.11, nginx, certbot

## Setup

```bash
sudo adduser poselyanov
sudo mkdir -p /opt/poselyanov3dprint /var/lib/poselyanov3dprint /etc/poselyanov3dprint
sudo chown poselyanov:poselyanov /opt/poselyanov3dprint /var/lib/poselyanov3dprint

cd /opt/poselyanov3dprint
git clone https://github.com/DenisPoselyanov/poselyanov3dprint.git .
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo cp deploy/poselyanov3dprint.service /etc/systemd/system/
sudo cp .env.example /etc/poselyanov3dprint/.env
# Edit /etc/poselyanov3dprint/.env with real secrets
sudo chmod 600 /etc/poselyanov3dprint/.env

sudo cp deploy/nginx-api.conf /etc/nginx/sites-available/poselyanov3dprint
sudo ln -sf /etc/nginx/sites-available/poselyanov3dprint /etc/nginx/sites-enabled/
sudo certbot --nginx -d api.yourdomain.ua

sudo ufw allow 22,80,443/tcp
sudo ufw enable

sudo systemctl daemon-reload
sudo systemctl enable --now poselyanov3dprint
```

## GitHub Pages + VPS split

- Storefront: `index.html` on GitHub Pages
- Set `window.__API_BASE__ = 'https://api.yourdomain.ua'` in `index.html` or inject before app scripts
- Admin: `ADMIN_WEBAPP_URL=https://api.yourdomain.ua/admin/panel`

## Supabase catalog (optional)

1. Run `scripts/supabase_schema.sql` in Supabase SQL Editor
2. `python scripts/migrate_catalog_to_supabase.py --database-url "postgresql://..."`
3. Set `DB_BACKEND=postgres`, `CATALOG_BACKEND=postgres`, `DATABASE_URL=...`

## Backup cron

```bash
sudo cp deploy/backup.sh /usr/local/bin/poselyanov-backup
sudo chmod +x /usr/local/bin/poselyanov-backup
echo "0 3 * * * root /usr/local/bin/poselyanov-backup" | sudo tee /etc/cron.d/poselyanov-backup
```

## Health check

`GET https://api.yourdomain.ua/health`
