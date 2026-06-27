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
- Copy `api-config.example.js` → `api-config.js`, set `window.__API_BASE__ = 'https://api.yourdomain.ua'`
- Commit `api-config.js` **only on the GitHub Pages branch** (repo root for Pages), not in the main backend repo (файл у `.gitignore` тут)
- `API_PUBLIC_URL` у `/etc/poselyanov3dprint/.env` має збігатися з `window.__API_BASE__`
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

## Автодеплой (push → VPS)

Після `git push` у `main` GitHub Actions запускає тести, потім SSH на VPS і `deploy/update.sh`.

### Одноразове налаштування

**1. SSH-ключ для GitHub Actions** (на VPS під `poselyanov`):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_actions_deploy -N ""
cat ~/.ssh/github_actions_deploy.pub >> ~/.ssh/authorized_keys
cat ~/.ssh/github_actions_deploy   # скопіювати приватний ключ у секрет VPS_SSH_KEY
```

**2. Секрети в GitHub** — репозиторій → Settings → Secrets and variables → Actions:

| Секрет | Значення |
|--------|----------|
| `VPS_SSH_HOST` | IP або домен VPS |
| `VPS_SSH_USER` | `poselyanov` |
| `VPS_SSH_KEY` | весь вміст `~/.ssh/github_actions_deploy` (приватний ключ) |

**3. `git pull` без пароля** — репозиторій на VPS має тягнутися без інтерактиву. Найпростіше:

```bash
cd /opt/poselyanov3dprint
git remote -v   # переконайтесь, що origin — публічний або з deploy key / token
git pull        # має пройти без запиту логіна
```

**4. sudo без пароля** для рестарту:

```bash
sudo visudo
# додати:
# poselyanov ALL=(ALL) NOPASSWD: /bin/systemctl restart poselyanov3dprint
```

**5. Перший раз підтягніть `deploy/update.sh`:**

```bash
cd /opt/poselyanov3dprint && git pull && chmod +x deploy/update.sh
```

Після цього достатньо **`git push`** — VPS оновиться сам.

### Ручне оновлення (запасний варіант)

```bash
ssh poselyanov@<IP-VPS> "cd /opt/poselyanov3dprint && bash deploy/update.sh"
```

## Health check

`GET https://api.yourdomain.ua/health`
