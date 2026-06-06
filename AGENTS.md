# AGENTS.md

本文件是所有参与本项目的 agent 必须先阅读并遵守的公共协作与文件安全规则。

## 项目目录

项目根目录：

```text
D:\Codex_Projects\festival_poster
```

## 重要文件和素材目录

以下文件和目录默认视为重要资产，除非用户明确要求，否则只读：

```text
D:\Codex_Projects\festival_poster\节日节气海报宣传AI员工_PRD.md
D:\Codex_Projects\festival_poster\asset
D:\Codex_Projects\festival_poster\二维码汇总
D:\Codex_Projects\festival_poster\朴道logo
D:\Codex_Projects\festival_poster\节日节气海报
D:\Codex_Projects\festival_poster\.env
```

不允许删除、移动、重命名、覆盖或批量改写上述文件和目录。

## 禁止行为

1. 禁止执行删除类命令，包括但不限于 `rm`、`del`、`Remove-Item`、`rmdir`、`git clean`。
2. 禁止执行破坏性 Git 命令，包括但不限于 `git reset --hard`、`git checkout --`、`git restore`、`git clean`、force push。
3. 禁止移动、重命名、覆盖现有素材文件。
4. 禁止修改 PRD，除非用户明确要求。
5. 禁止修改系统目录、用户目录、全局环境变量、全局 npm/pip 配置。
6. 禁止把真实 API Key 写进代码、日志、README、提交记录或示例文件。
7. 禁止打印 `.env` 中的真实 Key。
8. 禁止私自扩大 MVP 范围。

## 允许行为

1. 可以读取素材、PRD和现有代码。
2. 可以在自己负责的模块目录中新建或修改代码文件。
3. 可以创建或维护 `.env.example`，但不能写入真实 Key。
4. 可以创建本地 `storage/`、`tmp/`、`logs/` 等运行目录。
5. 可以安装项目本地依赖，但不要全局安装。
6. 可以修改自己负责模块的代码，但修改前必须先读取相关文件。

## 需要先征得用户确认的操作

如果确实需要删除、移动、覆盖、重命名、清理文件，必须先停止并向用户说明：

1. 要操作的文件路径。
2. 为什么必须这么做。
3. 是否有替代方案。
4. 操作风险。

没有用户明确确认，不要执行任何破坏性操作。

## MVP 范围提醒

MVP 只需要跑通：

```text
选择节点 -> 上传素材 -> 创建生成任务 -> 查询任务状态 -> 返回JPG海报 -> 前端预览/下载
```

MVP 不做：

1. 完整数据库。
2. 完整历史记录。
3. 管理后台。
4. 人工复核工作流。
5. PSD 导出。
6. 视频海报。
7. CRM 集成。
8. 自动推送。

如果需求和实现发生冲突，以 `节日节气海报宣传AI员工_PRD.md` 中第9章和第12.13章为准。
