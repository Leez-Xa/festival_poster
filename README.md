# 朴道节日/节气海报宣传 AI 员工 MVP

本项目用于生成节日/节气营销海报，当前 MVP 已跑通：

```text
选择营销节点 -> 选择系统产品图/上传产品图/上传整张场景图 -> 创建任务 -> 查询任务 -> 返回 JPG -> 前端预览/下载
```

后端使用 FastAPI，前端是静态页面，数据库使用项目内 SQLite：

```text
storage/festival_poster.sqlite3
```

产品资料素材只做只读索引，不移动、不覆盖原素材文件。

## 你在 Windows 上能不能运行

可以。你的电脑没有 Linux 也没关系，本地开发和演示直接在 Windows PowerShell 里启动即可。

阿里云 ECS 部署时服务器一般用 Ubuntu Linux，但那是服务器上的环境，和你本地电脑是不是 Linux 没冲突。

如果你用的是 CMD，进入 D 盘项目目录要带 `/d`：

```cmd
cd /d D:\Codex_Projects\festival_poster
```

PowerShell 是 Windows 自带组件，一般不需要另外下载。开始菜单搜索 `PowerShell` 或 `终端` 就能打开；如果你暂时只会 CMD，也完全可以按下面 CMD 命令启动项目。

## 首次准备

打开 CMD，进入项目目录：

```cmd
cd /d D:\Codex_Projects\festival_poster
```

创建虚拟环境并安装依赖：

```cmd
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果你的电脑上 `python` 命令不可用，试试：

```cmd
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

不需要手动创建数据库。后端启动时会自动初始化 `storage/`、SQLite 表和素材索引。

## 启动后端

开第一个 CMD 窗口：

```cmd
cd /d D:\Codex_Projects\festival_poster
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

看到类似下面内容就表示后端启动成功：

```text
Uvicorn running on http://127.0.0.1:8000
```

检查后端健康状态：

```cmd
curl http://127.0.0.1:8000/health
```

接口前缀：

```text
http://127.0.0.1:8000/api/v1
```

## 启动前端

开第二个 CMD 窗口：

```cmd
cd /d D:\Codex_Projects\festival_poster
.\.venv\Scripts\python.exe -m http.server 5173 --bind 127.0.0.1 --directory frontend
```

然后浏览器打开：

```text
http://127.0.0.1:5173
```

页面右上角 `API地址` 应该是：

```text
http://127.0.0.1:8000/api/v1
```

如果不是，点 `恢复默认`，或手动填入上面的地址。

## 正常使用流程

1. 打开前端页面。
2. 选择一个营销节点，例如春节。
3. 进入素材页。
4. 选择产品来源：
   - 系统产品图：使用 `素材/产品资料` 中已索引的产品图。
   - 上传产品图：上传一张产品图。
   - 上传整张场景图：直接上传一张完整场景图。
5. 确认 Logo、二维码、底部条已选择。
6. 进入生成配置页，填写补充需求。
7. 选择文案模式：
   - AI 生成文案：标题输入框作为偏好，可留空，后端会调用文本模型生成主标题/副标题。
   - 手动填写文案：主标题和副标题必填，后端不会调用文本模型改写你的文案。
8. 点击生成海报。
9. 页面会立刻拿到任务 ID 并开始轮询，AI 文案、提示词和图片融合都在后台执行。
10. 进入预览页后，可微调主标题/副标题。
11. 文案通过合规检查后，系统会重新合成 JPG。
12. 点击下载 JPG。

## 端口被占用怎么办

如果后端 `8000` 被占用，可以换端口，例如：

```cmd
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18083 --reload
```

前端页面右上角 API 地址也要改成：

```text
http://127.0.0.1:18083/api/v1
```

如果前端 `5173` 被占用，可以换成 `5174`：

```cmd
.\.venv\Scripts\python.exe -m http.server 5174 --bind 127.0.0.1 --directory frontend
```

然后打开：

```text
http://127.0.0.1:5174
```

## 常用检查命令

Python 编译检查：

```cmd
.\.venv\Scripts\python.exe -m compileall -q app config scripts
```

前端 JS 语法检查：

```cmd
node --check frontend\app.js
```

查看产品列表接口：

```cmd
curl http://127.0.0.1:8000/api/v1/products
```

查看系统产品素材接口：

```cmd
curl "http://127.0.0.1:8000/api/v1/assets?asset_type=product_image&source=product_material"
```

## 关于生图测试

下面命令会触发完整生成链路，会生成 JPG：

```cmd
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

如果只是检查服务是否启动，不要跑这个脚本，只跑 `/health` 即可。

## AI 中转站配置

前端不直接调用 AI 中转站，AI 调用统一封装在后端：

```text
app/services/ai_provider.py
```

`.env` 只放本地或服务器本机，不要提交，不要把真实 Key 写进代码、README 或日志。

当前生成链路是：

```text
文本模型生成海报文案和生图提示词
-> 图片模型读取产品参考图，生成融合场景图
-> 本地叠加 Logo、主标题、副标题、二维码、底部条
-> 输出 JPG
```

产品图模式默认必须调用图片模型生成融合场景图。如果图片 Key 未配置、图片接口失败、超时，或中转站协议不兼容，任务会直接失败并在前端显示 `AI_IMAGE_FUSION_FAILED`，不会再静默降级成本地背景加产品图堆叠。这样可以明确区分“真实 AI 生图”和“本地合成”。

如需在无 Key 环境临时演示，可在本地 `.env` 中设置 `AI_REQUIRE_IMAGE_FUSION=false`，此时才允许降级为本地兜底合成；正式联调和部署建议保持 `true`。

可参考 `.env.example` 配置：

```dotenv
AI_BASE_URL=https://你的中转站地址

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

`AI_IMAGE_FUSION_REQUEST_MODE` 默认是 `multipart`，用于 OpenAI 风格的图片编辑接口。不同中转站可能要求不同图片字段：

- 常见多图字段：`AI_IMAGE_FILE_FIELD=image[]`
- 有些中转站只认单数字段：`AI_IMAGE_FILE_FIELD=image`
- 有些中转站要求带序号：`AI_IMAGE_FILE_FIELD=image[{index}]`
- 如果你的中转站只支持 JSON base64，可改成：`AI_IMAGE_FUSION_REQUEST_MODE=json_base64`

`AI_IMAGE_RESPONSE_FORMAT=b64_json` 会请求接口直接返回 base64 图片；如果你的中转站只返回 URL，也可以按中转站文档调整或留空。

图片生成通常至少需要 30 秒，默认 `AI_IMAGE_TIMEOUT_SECONDS=120`。如果你的真实 `.env` 里还写着 `AI_IMAGE_TIMEOUT_SECONDS=90`，请手动改为 `120`；本项目规则要求 `.env` 只读，自动化 agent 不会替你修改真实 Key 配置文件。

## 已实现接口

- `GET /health`
- `GET /api/v1/marketing-nodes`
- `GET /api/v1/products`
- `GET /api/v1/assets`
- `GET /api/v1/assets/{asset_id}/file`
- `POST /api/v1/assets`
- `POST /api/v1/poster-tasks`
- `GET /api/v1/poster-tasks/{task_id}`
- `POST /api/v1/poster-tasks/{task_id}/rerender`
- `POST /api/v1/compliance/check`

## 目录说明

```text
app/                         后端代码
frontend/                    前端静态页面
config/                      节点、产品和规则 seed 数据
scripts/                     检查脚本
storage/                     本地数据库、上传文件、生成结果
docs/aliyun_deploy.md        阿里云 ECS 部署说明
素材/产品资料                 现有产品素材库，只读索引
```

## 阿里云部署

部署到阿里云 ECS 时，推荐使用：

- Ubuntu 22.04 或 24.04
- Python venv
- systemd 托管后端
- Nginx 托管前端静态文件
- Nginx 反向代理 `/api/v1` 和 `/storage`
- `storage/` 作为持久化目录

详细步骤见：

```text
docs/aliyun_deploy.md
```

生产环境注意：

- `.env` 只放服务器本地。
- 不要清空 `storage/`。
- 生产 CORS 收紧到正式域名或服务器 IP。
- 配 HTTPS 后再正式对外使用。
