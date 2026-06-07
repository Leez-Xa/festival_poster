# Aliyun ECS demo deployment

This package prepares a reproducible public demo for the festival poster generator.

## Recommended ECS trial choice

- Region: choose the region closest to the presentation audience.
- OS: Ubuntu 22.04 LTS or Ubuntu 24.04 LTS.
- Instance: 2 vCPU / 2 GiB is enough for the fallback demo; 2 vCPU / 4 GiB is safer for real AI API calls and image processing.
- Disk: 40 GiB ESSD Entry is enough for the defense demo.
- Security group: open `22` for SSH and `80` for HTTP. Open `443` only after HTTPS is configured.

## Upload project

Place the project at:

```bash
/opt/festival_poster
```

If using Git:

```bash
sudo mkdir -p /opt/festival_poster
sudo chown -R "$USER:$USER" /opt/festival_poster
cd /opt/festival_poster
git clone <your-repo-url> .
```

If uploading manually, upload the whole project directory except local-only files such as `.env`, `.venv`, caches, and generated temp outputs.

## Install

```bash
cd /opt/festival_poster
bash deploy/aliyun/install_server.sh
```

The installer:

- creates/reuses `.venv`
- installs Python dependencies
- creates `.env` from `.env.example` if missing
- starts FastAPI through systemd
- configures Nginx as a single public entry
- creates a Nginx Basic Auth password file

The shared demo password is created interactively by `htpasswd`. Do not commit or print that password.

## Demo URL

Open:

```text
http://SERVER_IP_OR_DOMAIN/
```

The browser will ask for the shared demo username/password. After login, the app uses the same origin API path:

```text
/api/v1
```

No user needs to fill an API address.

## Demo fallback vs real AI

The installer creates demo fallback mode by default:

```dotenv
APP_ENV=demo
AI_REQUIRE_IMAGE_FUSION=false
```

This keeps the defense flow reproducible even if the image model key or gateway is unavailable. The UI marks this as demo fallback.

To switch to real AI generation, edit `/opt/festival_poster/.env` on the server:

```dotenv
APP_ENV=production
AI_BASE_URL=https://your-ai-gateway.example.com
GPT_TEXT_API_KEY=your_text_generation_key_here
GPT_IMAGE_API_KEY=your_image_generation_key_here
AI_REQUIRE_IMAGE_FUSION=true
```

Then restart:

```bash
sudo systemctl restart festival-poster.service
```

Never commit real keys.

## Verify

Local service check on the ECS:

```bash
bash deploy/aliyun/verify_server.sh http://127.0.0.1:8000
```

Public Nginx check with Basic Auth:

```bash
BASIC_AUTH_USER=demo BASIC_AUTH_PASS='your-password' bash deploy/aliyun/verify_server.sh http://SERVER_IP_OR_DOMAIN
```

Expected:

- `/health` works
- `/api/v1/marketing-nodes` works
- `/api/v1/products` works
- system logo and bottom bar assets are available
- uploading a product image works
- creating and polling a poster task succeeds
- the returned JPG is publicly reachable after login
- qrcode remains absent when `qrcode_asset_id` is null
- `/storage/festival_poster.sqlite3` returns `403` or `404`

## Operations

View backend status:

```bash
sudo systemctl status festival-poster.service
```

View logs:

```bash
sudo journalctl -u festival-poster.service -f
```

Restart after code or `.env` changes:

```bash
sudo systemctl restart festival-poster.service
sudo nginx -t && sudo systemctl reload nginx
```

Keep `storage/` persistent. It contains SQLite metadata, uploaded assets, and generated posters.
