# 一键生图测试验收计划

本文档用于一键生图迭代的测试验收。范围只覆盖验证，不改变业务实现逻辑。

## 目标

- 保留旧手动配置链路可回归。
- 新增一键入口接口验收：意图解析、自动素材选择、任务创建、任务轮询、JPG 访问。
- 验证脚本不依赖真实 API Key，默认使用 demo fallback。
- 接口缺失、字段不匹配、任务失败时输出明确失败信息。

## 常用检查命令

```cmd
.\.venv\Scripts\python.exe -m compileall -q app config scripts
node --check frontend\app.js
.\.venv\Scripts\python.exe scripts\demo_check.py
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

如默认端口被占用：

```cmd
set ONE_CLICK_SMOKE_PORT=18087
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

如服务已经启动，可直接检查远端或现有本地服务：

```cmd
set ONE_CLICK_SMOKE_BASE_URL=http://127.0.0.1:8000
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
```

需要共享密码或 API token 的环境，可设置：

```cmd
set ONE_CLICK_SMOKE_AUTH_USER=your_user
set ONE_CLICK_SMOKE_AUTH_PASS=your_password
set ONE_CLICK_SMOKE_API_TOKEN=your_api_token
```

不要把真实 Key 或真实密码写入代码、文档、日志或提交记录。

## 覆盖场景

1. 健康检查：`GET /health` 返回 `status=ok`。
2. 基础目录：`GET /api/v1/marketing-nodes`、`GET /api/v1/products`。
3. 素材目录：产品素材、Logo、二维码、底部宣传条可查询。
4. 旧手动流程：上传临时产品图，调用 `POST /api/v1/poster-tasks`，轮询 `GET /api/v1/poster-tasks/{task_id}`，访问生成 JPG，读取 composition。
5. 二维码保真基础验收：选择二维码时，composition 中应存在二维码 safe zone。
6. 一键意图解析：`POST /api/v1/one-click-intent` 返回可识别的任务规划字段。
7. 一键任务创建：`POST /api/v1/one-click-poster-tasks` 返回 task id，后续复用任务轮询和 JPG 访问检查。

## 历史状态说明

本测试计划创建初期，`app.models` 中已有 `OneClickIntentRequest` 和 `OneClickPosterTaskCreate`，但 `app.main` 尚未挂载一键路由。该阶段运行 `scripts/one_click_smoke_test.py` 时，旧手动链路应先通过；随后一键接口会以 `ENDPOINT_MISSING` 明确失败，期望接口为：

```text
POST /api/v1/one-click-intent
POST /api/v1/one-click-poster-tasks
GET /api/v1/poster-tasks/{task_id}
```

截至 2026-06-08 本轮复验，一键路由已经挂载，当前 smoke test 已按实际接口契约更新并通过。

## 剩余风险

- smoke test 只验证接口契约、任务轮询和 JPG 可访问，不判断最终海报审美质量。
- 二维码只检查 safe zone 和后处理存在，不做扫码识别；后续可增加二维码解码库做保真验收。
- demo fallback 可覆盖无 Key 环境，但真实 AI 中转站的超时、响应字段和图片融合质量仍需联调验证。
- 一键素材匹配策略需要在接口实现后补充更细的断言，例如命中的产品、Logo、二维码、底部条是否符合指令。

## 2026-06-08 验证状态更新

当前一键路由已挂载，`scripts/one_click_smoke_test.py` 已按当前接口契约更新：

- `POST /api/v1/one-click-intent`：校验 `intent`、`matches`、`confidence`、`requires`、`warnings`，并检查 `scene_prompt`、`custom_requirement`、品牌资产匹配等规划字段。
- `POST /api/v1/one-click-poster-tasks`：使用 overrides 固定 `node_id`、`product_id`、测试上传产品图、Logo、二维码和底部条，避免素材匹配波动影响任务链路验收。
- 旧手动链路仍通过 `POST /api/v1/poster-tasks` 独立回归，并检查 JPG URL、composition 和二维码 safe zone。

本轮已通过：

```cmd
.\.venv\Scripts\python.exe -m compileall -q app config scripts
node --check frontend\app.js
.\.venv\Scripts\python.exe scripts\one_click_smoke_test.py
.\.venv\Scripts\python.exe scripts\demo_check.py
```
