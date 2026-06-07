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
sudo apt install -y python3 python3-venv python3-pip nginx git apache2-utils
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

## 推荐一键安装

项目内已经提供阿里云 ECS 部署包，放在 `deploy/aliyun/`。拿到 ECS 登录信息并把项目上传或拉取到 `/opt/festival_poster` 后，优先执行：

```bash
cd /opt/festival_poster
bash deploy/aliyun/install_server.sh
```

安装脚本会创建 `.venv`、安装依赖、创建演示用 `.env`、配置 systemd、配置 Nginx 反向代理，并交互式生成 Nginx Basic Auth 共享密码。密码只存在服务器 `/etc/nginx/.festival-poster.htpasswd`，不要写入代码、README 或提交记录。

安装完成后访问：

```text
http://服务器IP或域名/
```

浏览器会先要求输入共享用户名和密码。登录后前端自动使用同源 `/api/v1`，普通用户不需要填写 API 地址。

## 手动后端配置

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
chmod 640 .env
```

只在服务器 `.env` 写真实 Key。不要把真实 Key 写进代码、README、提交记录或日志。

答辩演示优先建议：

```dotenv
APP_ENV=demo
AI_REQUIRE_IMAGE_FUSION=false
CORS_ALLOW_LOCALHOST=false
```

这会启用明确标注的 fallback 演示模式，保证无真实图片模型 Key 时仍可复现主流程。真实 AI 联调时再切换：

```dotenv
CORS_ORIGINS=http://your-domain.com,https://your-domain.com
CORS_ALLOW_LOCALHOST=false
APP_ENV=production
AI_REQUIRE_IMAGE_FUSION=true
```

云端公开演示默认使用 Nginx Basic Auth 保护整站。`API_ACCESS_TOKEN` 可作为额外后端令牌，但普通答辩演示不使用前端令牌输入。

SQLite 数据库默认位于：

```bash
/opt/festival_poster/storage/festival_poster.sqlite3
```

`storage/` 是持久化目录，发布新版代码时不要清空。

## systemd 服务

创建服务文件：

```bash
sudo nano /etc/systemd/system/festival-poster.service
```

写入：

```ini
[Unit]
Description=Festival Poster Demo FastAPI
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
sudo chown www-data:www-data /opt/festival_poster/.env
sudo chown -R www-data:www-data /opt/festival_poster/storage
sudo systemctl daemon-reload
sudo systemctl enable --now festival-poster.service
sudo systemctl status festival-poster.service
```

查看日志：

```bash
sudo journalctl -u festival-poster.service -f
```

日志中不要输出真实 Key。

## Nginx 整站共享密码和反向代理

创建站点配置：

```bash
sudo nano /etc/nginx/sites-available/festival-poster
```

写入。该配置把整站转发到 FastAPI 单入口，并启用 Basic Auth：

```nginx
server {
    listen 80;
    server_name _;

    client_max_body_size 20m;

    auth_basic "Festival Poster Demo";
    auth_basic_user_file /etc/nginx/.festival-poster.htpasswd;

    location / {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
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

后端 `/storage/` 只允许公开 `generated/`、`uploads/`、`system/` 下的 JPG/PNG/WEBP 图片。SQLite、日志、pid、JSON 合成参数等不会通过 `/storage/` 直接返回；合成参数请走 `/api/v1/poster-tasks/{task_id}/composition`。

启用站点：

```bash
sudo ln -s /etc/nginx/sites-available/festival-poster /etc/nginx/sites-enabled/festival-poster
sudo nginx -t
sudo systemctl reload nginx
```

创建共享密码：

```bash
sudo htpasswd -c /etc/nginx/.festival-poster.htpasswd demo
```

如果使用 `deploy/aliyun/install_server.sh`，这一步会由脚本交互完成。

## 生产安全项

- `.env` 只保留在服务器本地。
- `.env` 不要 world-readable，建议 `chmod 640` 并由服务用户读取。
- `storage/` 做持久化备份，尤其是 `storage/festival_poster.sqlite3`、`storage/uploads/`、`storage/generated/`。
- 阿里云安全组只开放必要端口。
- 生产环境把 CORS 收紧到正式域名或服务器 IP，并设置 `CORS_ALLOW_LOCALHOST=false`。
- 对外部署必须保留 Nginx Basic Auth 或等效访问控制，降低公开上传和生图接口被滥用的风险。
- 如果启用 HTTPS，使用阿里云证书或 Certbot，并把 Nginx 监听改为 `443 ssl`。
- 不要在服务器上执行清空 `storage/` 的操作，避免丢失 SQLite、上传素材和生成结果。

## 更新、备份和回滚

发布新版前先备份运行数据：

```bash
cd /opt/festival_poster
mkdir -p storage/backups
cp storage/festival_poster.sqlite3 storage/backups/festival_poster_$(date +%Y%m%d_%H%M%S).sqlite3
```

更新代码并重启：

```bash
git pull --ff-only
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart festival-poster.service
sudo nginx -t && sudo systemctl reload nginx
```

验证本机后端：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/products
curl -I http://127.0.0.1:8000/storage/festival_poster.sqlite3
```

最后一个命令应返回 `403` 或 `404`，不能返回数据库文件。

如需回滚，切回上一个 Git 提交并重启服务；如果数据库也需要回滚，先停止服务，再用备份 SQLite 覆盖当前数据库。

## 完整上线验证

```bash
bash deploy/aliyun/verify_server.sh http://127.0.0.1:8000
```

公网验证带 Basic Auth：

```bash
BASIC_AUTH_USER=demo BASIC_AUTH_PASS='服务器上生成的共享密码' bash deploy/aliyun/verify_server.sh http://服务器IP或域名
```

验证脚本会跑完整闭环：health、节点、产品、Logo、底部条、上传、任务生成、JPG 可访问、二维码为空时不叠加、SQLite 不公开。服务重启后，历史 `pending/processing` 任务会被标记为 failed，不会卡住。
