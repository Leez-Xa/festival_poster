# Festival Poster

朴道节日/节气海报宣传 AI 员工，一个用于生成节日、节气、活动营销海报的 Web 应用。

项目当前同时提供两条生成链路：

- 一句话一键生成：输入自然语言指令，系统自动解析节日活动、产品、风格、画面元素和文案方向，并创建生成任务。
- 手动配置生成：保留节点选择、素材选择、生成配置、预览下载流程，适合精细控制素材和文案。

后端使用 FastAPI，前端为静态 HTML/CSS/JavaScript，并由同一个应用直接托管。上传文件、本地数据库和生成结果默认写入 `storage/`；原始素材目录默认按只读资产使用，不移动、不覆盖。

## Project Status

项目处于持续迭代开发中，当前重点方向包括：

- 一句话一键生图
- 自动素材匹配
- 提示词优化
- 品牌素材融合
- 二维码保真
- 手动配置与一键生成双入口并行保留

## Features

- 节日、节气、自定义活动海报生成
- 一句话意图解析与自动素材匹配
- 产品图、Logo、二维码、底部宣传条素材选择
- AI 文案、场景提示词与图片融合调用
- 任务创建、后台生成、前端轮询、JPG 预览下载
- 文案合规检查与预览页二次渲染
- Demo fallback，支持无真实 AI Key 的本地演示
- 本地检查与 smoke test，覆盖一键链路和手动链路

## Tech Stack

| Layer | Stack |
| --- | --- |
| Backend | FastAPI / Python |
| Frontend | Static HTML / CSS / JavaScript |
| Database | SQLite |
| Storage | Local `storage/` |
| Deployment | Local Windows startup |

## Quick Start

### Option 1: Windows one-click startup

这是当前最省事的启动方式：

```powershell
git clone https://github.com/Leez-Xa/festival_poster.git
cd festival_poster
.\start.bat
```

启动脚本会自动：

- 创建或复用 `.venv`
- 安装 `requirements.txt`
- 选择可用本地端口
- 启动 FastAPI
- 打开浏览器访问应用

默认访问地址：

```text
http://127.0.0.1:8000/
```

如果 `8000` 被占用，脚本会自动尝试 `18085`、`18086`、`18087`。

### Option 2: Demo mode

如果你还没有真实 AI 配置，只想先跑通完整流程：

```powershell
.\start_demo.bat
```

Demo 模式会自动启用：

```text
APP_ENV=demo
AI_REQUIRE_IMAGE_FUSION=false
API_ACCESS_TOKEN=
```

这个模式会使用本地 fallback 生成演示结果，适合联调界面、流程和任务状态，不等同于真实 AI 图片融合效果。

### Option 3: Manual startup

如果你希望手动启动服务：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```powershell
curl http://127.0.0.1:8000/health
```

## Configuration

真实 AI 配置通过本地 `.env` 提供，完整字段请参考 [`.env.example`](.env.example)。

常用配置项包括：

- `AI_BASE_URL`
- `GPT_TEXT_API_KEY`
- `GPT_TEXT_MODEL`
- `GPT_IMAGE_API_KEY`
- `GPT_IMAGE_MODEL`
- `API_ACCESS_TOKEN`
- `AI_REQUIRE_IMAGE_FUSION`

正式联调建议保持：

```text
AI_REQUIRE_IMAGE_FUSION=true
```

这样在图片融合失败时，任务会明确报错，而不是静默降级为本地演示结果，更方便区分真实 AI 输出和 demo fallback。

## Usage

### 一键生成

1. 打开首页，默认进入“一键生成”。
2. 输入一句话需求，例如节日、产品、风格、画面元素、文案方向。
3. 点击“先解析”查看系统理解结果，或直接点击“一键生成”。
4. 系统创建任务并轮询进度。
5. 任务完成后预览 JPG，确认无误后下载。

### 手动配置生成

1. 切换到“手动配置”。
2. 选择营销节点或自定义活动节点。
3. 选择系统产品图，或上传产品图/整张场景图。
4. 选择 Logo、二维码、底部宣传条等品牌素材。
5. 填写风格、文案方向、补充需求。
6. 创建任务并等待生成。
7. 在预览页微调主标题/副标题，通过合规检查后下载 JPG。

## API Overview

核心接口如下：

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

说明：

- 前端不直接访问 AI 中转站，所有 AI 调用统一封装在 [`app/services/ai_provider.py`](app/services/ai_provider.py)。
- `/storage/` 只公开 `generated/`、`uploads/`、`system/` 下的 JPG、PNG、WEBP 图片。
- SQLite、日志、PID 和 composition JSON 不通过静态文件直链公开。

## Project Structure

```text
app/                         后端接口、任务、素材、AI 调用与合成逻辑
config/                      节点、产品、规则等 seed 数据
frontend/                    前端静态页面和交互脚本
scripts/                     本地检查与测试脚本
docs/                        本地测试与验收文档
storage/                     本地数据库、上传文件、生成结果，默认不提交
素材/                        原始素材目录，只读使用
```

## Local Validation

Python 编译检查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q app config scripts
```

前端语法检查：

```powershell
node --check frontend\app.js
node --check frontend\one-click.js
```

一键入口守卫测试：

```powershell
.\.venv\Scripts\python.exe scripts\one_click_guardrails_test.py
```

一键链路 smoke test：

```powershell
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

完整演示检查：

```powershell
.\.venv\Scripts\python.exe scripts\demo_check.py
```

如果端口被占用，可临时指定：

```powershell
$env:ONE_CLICK_SMOKE_PORT=18087
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

如果服务已经启动，可直接检查现有服务：

```powershell
$env:ONE_CLICK_SMOKE_BASE_URL="http://127.0.0.1:8000"
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

## Related Docs

- [docs/one_click_test_plan.md](docs/one_click_test_plan.md)
- [frontend/README.md](frontend/README.md)

## Safety Notes

- 不要提交 `.env`。
- 不要把真实 API Key 写入代码、README、日志或提交记录。
- 不要删除、移动、覆盖 `素材/`、PRD、备份 zip 和备份目录。
- 原始素材目录按只读资产使用，生成结果写入 `storage/`。
- 生产环境建议启用 `API_ACCESS_TOKEN` 并收紧 CORS。

## Public Repo Scope

公开仓库默认只保留“本地运行和联调”所需内容，不包含：

- 私有 `.env`
- `素材/` 原始业务素材
- `storage/`、`tmp/`、`outputs/` 等本地运行产物
- 云服务器部署脚本或云平台专用配置
