# Festival Poster

一个基于 FastAPI + 静态前端的节日海报生成项目，支持一句话生成和手动配置两种模式。

这个公开仓库的目标很简单：让别人 fork 下来后，能在本地直接跑起来、看流程、做联调。

## Install

建议环境：

- Windows
- Python 3.11+

克隆项目：

```powershell
git clone https://github.com/Leez-Xa/festival_poster.git
cd festival_poster
```

如果你想手动安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start

最简单的启动方式：

```powershell
.\start.bat
```

启动脚本会自动：

- 创建或复用 `.venv`
- 安装 `requirements.txt`
- 选择可用本地端口
- 启动 FastAPI
- 打开浏览器

默认访问地址：

```text
http://127.0.0.1:8000/
```

如果 `8000` 被占用，脚本会自动尝试 `18085`、`18086`、`18087`。

如果你更想手动启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```powershell
curl http://127.0.0.1:8000/health
```

## Demo

如果你没有真实 AI Key，只想先把项目流程跑通，用 Demo 模式：

```powershell
.\start_demo.bat
```

这个模式会使用本地 fallback 结果，适合：

- 看页面是否正常打开
- 测试一键生成和手动生成流程
- 联调任务状态和结果展示

这个模式不代表真实 AI 出图效果。

## Real Mode

如果你要接真实 AI，请在项目根目录创建本地 `.env`，字段参考 [`.env.example`](.env.example)。

常用配置项：

- `AI_BASE_URL`
- `GPT_TEXT_API_KEY`
- `GPT_TEXT_MODEL`
- `GPT_IMAGE_API_KEY`
- `GPT_IMAGE_MODEL`
- `API_ACCESS_TOKEN`
- `AI_REQUIRE_IMAGE_FUSION`

建议开启：

```text
AI_REQUIRE_IMAGE_FUSION=true
```

这样当真实图片融合失败时，任务会直接报错，不会静默回退成 Demo 结果。

## Notes

- 公开仓库不包含 `.env`
- 公开仓库不包含 `素材/`、`storage/`、`tmp/`、`outputs/`
- 仓库额外提供了一个 `素材.zip` 压缩包，便于需要体验素材联调的使用者按需下载
- 没有私有素材时，项目仍可本地启动和跑 Demo 流程
- 真实业务效果依赖你自己的素材和 AI 配置
