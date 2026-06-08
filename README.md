# 朴道节日/节气海报宣传 AI 员工

这是一个用于生成节日、节气、活动营销海报的本地/云端 Web 应用。项目当前包含两条入口：

- 一句话一键生图：输入自然语言指令，系统自动解析节日活动、产品、风格、画面元素、文案方向，并创建生成任务。
- 手动配置生成：保留原有节点选择、素材选择、生成配置、预览下载流程，适合精细控制素材和文案。

后端使用 FastAPI，直接托管静态前端页面；本地数据、上传文件和生成结果默认写入项目内 `storage/`。素材库默认只读索引，不移动、不覆盖原始素材。

## 当前能力

- 节日/节气/自定义活动海报生成
- 一句话意图解析与自动素材匹配
- 产品图、Logo、二维码、底部宣传条素材选择
- AI 文案、场景提示词和图片融合调用
- 任务创建、后台生成、前端轮询、JPG 预览下载
- 文案合规检查和预览页二次渲染
- demo fallback，用于无真实 AI Key 的本地演示
- 一键 smoke test，覆盖手动链路和一键链路

## 技术栈

```text
Backend   FastAPI / Python
Frontend  Static HTML / CSS / JavaScript
Database  SQLite
Storage   local storage/
Deploy    Windows local / Aliyun ECS / Vercel auxiliary config
```

## 目录结构

```text
app/                         后端接口、任务、素材、AI 调用与合成逻辑
config/                      节点、产品、规则等 seed 数据
frontend/                    前端静态页面和交互脚本
scripts/                     本地检查、冒烟测试脚本
docs/                        测试计划与部署文档
deploy/                      阿里云部署参考配置
storage/                     本地数据库、上传文件、生成结果，默认不提交
素材/                        产品、Logo、二维码、参考海报等原始素材，只读使用
```

## 快速启动

Windows 本地推荐直接双击：

```text
start.bat
```

脚本会自动：

- 创建或复用 `.venv`
- 安装 `requirements.txt`
- 选择可用本地端口
- 启动 FastAPI
- 打开浏览器访问应用

默认访问地址：

```text
http://127.0.0.1:8000/
```

如果 `8000` 被占用，脚本会尝试 `18085`、`18086`、`18087`。

## Demo 模式

如果没有真实 AI Key，或者只是演示完整流程，可以双击：

```text
start_demo.bat
```

它会调用同一个启动入口，并设置：

```text
APP_ENV=demo
AI_REQUIRE_IMAGE_FUSION=false
API_ACCESS_TOKEN=
```

demo 模式允许使用本地 fallback 生成演示结果，不会假装已经完成真实 AI 图片融合。

## 手动启动

首次准备：

```cmd
cd /d D:\Codex_Projects\festival_poster
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果 `python` 命令不可用，可以改用：

```cmd
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动服务：

```cmd
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```cmd
curl http://127.0.0.1:8000/health
```

## 使用流程

一键生图：

1. 打开首页，默认进入“一键生成”。
2. 输入一句话需求，例如节日、产品、风格、画面元素、文案方向。
3. 点击“先解析”查看系统理解结果，或直接点击“一键生成”。
4. 系统创建任务并轮询进度。
5. 任务完成后预览 JPG，确认无误后下载。

手动配置：

1. 切换到“手动配置”。
2. 选择营销节点或自定义活动节点。
3. 选择系统产品图，或上传产品图/整张场景图。
4. 选择 Logo、二维码、底部宣传条等品牌素材。
5. 填写风格、文案方向、补充需求。
6. 创建任务并等待生成。
7. 在预览页微调主标题/副标题，通过合规检查后下载 JPG。

## AI 配置

前端不直接访问 AI 中转站，所有 AI 调用都封装在后端：

```text
app/services/ai_provider.py
```

真实配置写入本地 `.env`，不要提交真实 Key。可以参考 `.env.example`：

```dotenv
AI_BASE_URL=https://your-ai-gateway.example.com
APP_ENV=production

CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
CORS_ALLOW_LOCALHOST=true
CORS_ALLOW_ORIGIN_REGEX=
API_ACCESS_TOKEN=your_optional_mvp_api_token_here

GPT_TEXT_API_KEY=your_text_generation_key_here
GPT_TEXT_MODEL=your_text_model_name
GPT_SCENE_PROMPT_MODEL=your_scene_prompt_model_name

GPT_IMAGE_API_KEY=your_image_generation_key_here
GPT_IMAGE_MODEL=your_image_model_name
AI_IMAGE_GENERATION_ENDPOINT=/images/generations
AI_IMAGE_FUSION_ENDPOINT=/images/edits
AI_IMAGE_FUSION_REQUEST_MODE=multipart
AI_IMAGE_FILE_FIELD=image[]
AI_IMAGE_OUTPUT_SIZE=1024x1792
AI_IMAGE_QUALITY=auto
AI_IMAGE_OUTPUT_FORMAT=png
AI_IMAGE_RESPONSE_FORMAT=b64_json

AI_TEXT_TIMEOUT_SECONDS=30
AI_IMAGE_TIMEOUT_SECONDS=120
AI_REQUIRE_IMAGE_FUSION=true
```

正式联调建议保持：

```text
AI_REQUIRE_IMAGE_FUSION=true
```

这样图片融合失败时任务会明确报错，不会静默降级成本地合成，便于区分真实 AI 生图和演示 fallback。

## 接口概览

```text
GET  /health
GET  /api/v1/app-config
GET  /api/v1/marketing-nodes
GET  /api/v1/products
GET  /api/v1/assets
GET  /api/v1/assets/{asset_id}/file
POST /api/v1/assets
POST /api/v1/one-click-intent
POST /api/v1/one-click-poster-tasks
POST /api/v1/poster-tasks
GET  /api/v1/poster-tasks/{task_id}
GET  /api/v1/poster-tasks/{task_id}/composition
POST /api/v1/poster-tasks/{task_id}/rerender
POST /api/v1/compliance/check
```

`/storage/` 只公开 `generated/`、`uploads/`、`system/` 下的 JPG、PNG、WEBP 图片。SQLite、日志、pid、composition JSON 不通过静态文件直链公开。

## 本地检查

Python 编译检查：

```cmd
.\.venv\Scripts\python.exe -m compileall -q app config scripts
```

前端语法检查：

```cmd
node --check frontend\app.js
node --check frontend\one-click.js
```

一键入口守卫测试：

```cmd
.\.venv\Scripts\python.exe scripts\one_click_guardrails_test.py
```

一键链路 smoke test：

```cmd
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

完整演示检查：

```cmd
.\.venv\Scripts\python.exe scripts\demo_check.py
```

如果端口被占用，可临时指定端口：

```cmd
set ONE_CLICK_SMOKE_PORT=18087
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

如果服务已经启动，可直接检查现有服务：

```cmd
set ONE_CLICK_SMOKE_BASE_URL=http://127.0.0.1:8000
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

## 部署说明

阿里云 ECS 部署参考：

```text
docs/aliyun_deploy.md
```

推荐部署方式：

- Ubuntu 22.04 或 24.04
- Python venv
- systemd 托管 FastAPI
- Nginx 反向代理到后端
- `storage/` 作为持久化目录
- `.env` 只放服务器本地
- 配置 HTTPS 后再正式对外使用

## 安全注意

- 不要提交 `.env`。
- 不要把真实 API Key 写入代码、README、日志或提交记录。
- 不要删除、移动、覆盖 `素材/`、PRD、备份 zip 和备份目录。
- 素材目录只读使用，生成结果写入 `storage/`。
- 生产环境建议启用 `API_ACCESS_TOKEN`，并收紧 CORS。

## 当前分支

当前开发分支：

```text
codex/ai-brand-fusion
```

GitHub 地址：

```text
https://github.com/Leez-Xa/festival_poster
```
