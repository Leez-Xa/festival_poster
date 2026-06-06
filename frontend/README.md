# 朴道节日海报AI员工前端MVP

## 运行

可直接打开：

```text
D:\Codex_Projects\festival_poster\frontend\index.html
```

推荐联调用本地静态服务：

```powershell
cd D:\Codex_Projects\festival_poster\frontend
python -m http.server 5173 --bind 127.0.0.1
```

然后访问：

```text
http://127.0.0.1:5173/
```

## API

默认 API 地址为：

```text
http://127.0.0.1:8000/api/v1
```

页面右上角可以改 API 地址。前端只调用 PRD 12.13 定义的后端接口，不直接调用 GPT、image2.0 或任何中转站接口，也不保存 API Key。

核心链路：

- `GET /api/v1/marketing-nodes`
- `GET /api/v1/products`
- `GET /api/v1/assets?asset_type=logo&source=system`
- `GET /api/v1/assets?asset_type=qrcode&source=system`
- `GET /api/v1/assets?asset_type=bottom_bar&source=system`
- `POST /api/v1/assets`
- `POST /api/v1/poster-tasks`
- `GET /api/v1/poster-tasks/{task_id}`
- `POST /api/v1/compliance/check`

## 联调说明

- 创建任务后前端会轮询 `GET /api/v1/poster-tasks/{task_id}`。
- 下载按钮只在任务成功、有 `poster.jpg_url` 且合规状态为 `passed` 时启用。
- 如果后端合规失败或未返回可检查文案，下载按钮保持禁用。
- 如果响应结构或关键字段缺失，前端会在侧边栏“接口问题记录”中展示，不会私自改协议。

