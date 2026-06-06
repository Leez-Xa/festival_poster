# 朴道节日/节气海报宣传AI员工 后端MVP

本项目后端使用 FastAPI 实现 PRD 第 12.13 节接口协议，跑通“选择节点 -> 上传融合场景图 -> 创建生成任务 -> 查询任务状态 -> 返回 JPG 海报”的最短闭环。

## AI中转站配置

前端不直接调用中转站 API，AI 调用统一封装在后端 `app/services/ai_provider.py`。当前缺少中转站具体接口文档，已先实现 mock 适配层；提供请求格式和鉴权方式后，只需要替换 provider 内部实现。

`.env` 预留变量：

```dotenv
AI_BASE_URL=https://你的中转站地址

GPT_TEXT_API_KEY=your_text_generation_key_here
GPT_TEXT_MODEL=your_text_model_name

GPT_IMAGE_API_KEY=your_image_generation_key_here
GPT_IMAGE_MODEL=your_image_model_name

AI_TEXT_TIMEOUT_SECONDS=30
AI_IMAGE_TIMEOUT_SECONDS=90
```

`.env` 不要提交或返回给前端。填入真实文本 Key 后，后端会通过 OpenAI 兼容的 `/chat/completions` 生成结构化文案；填入真实图片 Key 后，会通过 `/images/generations` 生成无文字、无Logo、无二维码、无产品主体的背景图。Key 未配置、占位或接口异常时，会降级到本地模板文案和本地背景图库。

## 本地启动

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

接口前缀为 `http://127.0.0.1:8000/api/v1`，静态文件通过后端返回的 `/storage/...` URL 访问。

## 冒烟验证

```powershell
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

该脚本会临时启动后端服务，依次验证节点、产品、系统素材、合规检查、上传融合场景图、创建任务、轮询任务和生成 JPG URL 可访问性，结束后自动停止临时服务。

## 已实现接口

- `GET /api/v1/marketing-nodes`
- `GET /api/v1/products`
- `GET /api/v1/assets`
- `POST /api/v1/assets`
- `POST /api/v1/poster-tasks`
- `GET /api/v1/poster-tasks/{task_id}`
- `POST /api/v1/compliance/check`

MVP 使用本地 `storage/` 和内存任务状态；系统素材在应用启动时从现有素材目录初始化到 `storage/system/`。
