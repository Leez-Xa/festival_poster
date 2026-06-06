# 阿里云 ECS 部署说明

本文档用于把 MVP 部署到阿里云 ECS Linux 服务器。示例以 Ubuntu 22.04/24.04 为准，真实域名、服务器 IP 和密钥只写在服务器本地，不提交到仓库。

## 服务器准备

1. 购买阿里云 ECS，系统选择 Ubuntu 22.04 LTS 或 Ubuntu 24.04 LTS。
2. 安全组放行：
   - `22`：SSH。
   - `80`：HTTP。
   - `443`：HTTPS，配置证书后使用。
3. 登录服务器后安装运行环境：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
```

## 项目目录

推荐把项目放在：

```bash
/opt/festival_poster
```

首次部署：

```bash
sudo mkdir -p /opt/festival_poster
sudo chown -R $USER:$USER /opt/festival_poster
cd /opt/festival_poster
git clone <your-repo-url> .
```

如果不用 Git，也可以把项目目录上传到 `/opt/festival_poster`。注意不要把本地真实 `.env` 泄露到公共仓库。

## 后端配置

```bash
cd /opt/festival_poster
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

在服务器本地创建 `.env`：

```bash
cp .env.example .env
nano .env
```

只在服务器 `.env` 写真实 Key。不要把真实 Key 写进代码、README、提交记录或日志。

SQLite 数据库默认位于：

```bash
/opt/festival_poster/storage/festival_poster.sqlite3
```

`storage/` 是持久化目录，发布新版代码时不要清空。

## systemd 服务

创建服务文件：

```bash
sudo nano /etc/systemd/system/festival-poster-api.service
```

写入：

```ini
[Unit]
Description=Festival Poster FastAPI
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/festival_poster
EnvironmentFile=/opt/festival_poster/.env
ExecStart=/opt/festival_poster/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
User=www-data
Group=www-data

[Install]
WantedBy=multi-user.target
```

授权并启动：

```bash
sudo chown -R www-data:www-data /opt/festival_poster/storage
sudo systemctl daemon-reload
sudo systemctl enable --now festival-poster-api
sudo systemctl status festival-poster-api
```

查看日志：

```bash
sudo journalctl -u festival-poster-api -f
```

日志中不要输出真实 Key。

## Nginx 前端和反向代理

创建站点配置：

```bash
sudo nano /etc/nginx/sites-available/festival-poster
```

写入，将 `your-domain.com` 替换为正式域名或服务器公网 IP：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    root /opt/festival_poster/frontend;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/v1/ {
        proxy_pass http://127.0.0.1:8000/api/v1/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
        proxy_set_header Host $host;
    }

    location /storage/ {
        proxy_pass http://127.0.0.1:8000/storage/;
        proxy_set_header Host $host;
    }
}
```

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/festival-poster /etc/nginx/sites-enabled/festival-poster
sudo nginx -t
sudo systemctl reload nginx
```

前端页面的 API 地址可以设为：

```text
http://your-domain.com/api/v1
```

同域部署时也可以后续把前端默认 API 改成 `/api/v1`。

## 生产安全项

- `.env` 只保留在服务器本地。
- `storage/` 做持久化备份。
- 阿里云安全组只开放必要端口。
- 生产环境建议把 CORS 收紧到正式域名或服务器 IP。
- 如果启用 HTTPS，使用阿里云证书或 Certbot，并把 Nginx 监听改为 `443 ssl`。
- 不要在服务器上执行清空 `storage/` 的操作，避免丢失 SQLite、上传素材和生成结果。

## 不触发生图的上线检查

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/products
curl "http://127.0.0.1:8000/api/v1/assets?asset_type=product_image&source=product_material"
```

完整闭环测试要等允许触发生图后再执行 `POST /api/v1/poster-tasks`。
