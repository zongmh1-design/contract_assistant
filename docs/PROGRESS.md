# 项目进度

## 当前阶段

阶段 2：附件元数据与 Mock 下载最小闭环 —— 已完成。

## 本阶段已完成

- 保留阶段 1 的任务同步、去重、查询、状态和日志闭环。
- 新增 `ApprovalAttachment`，数据库以 `task_id + external_attachment_id` 建立复合唯一约束。
- Mock Approval Adapter 新增附件元数据和下载能力，覆盖正常、无附件、无法识别主合同、不支持类型、首次下载失败及内容变化场景。
- 集中实现主合同识别：明确标记优先，否则按附件顺序匹配中英文合同关键词。
- 支持 `pdf`、`docx`、`jpg`、`jpeg`、`png`，不支持类型进入 `blocked`。
- 文件保存到 `storage/contracts/{approval_code}/`，文件名使用 `{attachment_id}_{safe_filename}` 并处理路径穿越和非法字符。
- 使用 SHA-256 判断首次下载、复用和上游内容更新，并分别记录 `ATTACHMENT_DOWNLOADED`、`ATTACHMENT_REUSED`、`ATTACHMENT_UPDATED`。
- 无附件、无主合同、不支持类型和下载失败均保存明确错误码并进入 `blocked`。
- 附件阶段人工重试执行 `blocked -> parsing` 后，立即重新执行附件准备流程，不创建新任务。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/tasks/sync` | 拉取 Mock 待办并去重保存 |
| GET | `/api/tasks` | 查询任务列表 |
| GET | `/api/tasks/{task_id}` | 查询单个任务 |
| POST | `/api/tasks/{task_id}/start-parsing` | `pending -> parsing` |
| POST | `/api/tasks/{task_id}/simulate-parsing-failure` | 对失败样例执行 `parsing -> blocked` |
| POST | `/api/tasks/{task_id}/retry` | 人工执行 `blocked -> parsing` |
| GET | `/api/tasks/{task_id}/logs` | 查询任务全链路日志 |
| POST | `/api/tasks/{task_id}/prepare-attachment` | 识别、下载或复用主合同附件 |
| GET | `/api/tasks/{task_id}/attachments` | 查询任务附件记录 |

## 测试结果

执行命令：

```powershell
uv --cache-dir .uv-cache --python-preference only-system run pytest -q --basetemp=.test-tmp
```

结果：16 个测试全部通过，0 个失败。阶段 1 的 5 个测试继续通过；附件测试覆盖正常下载落库、复合唯一约束、重复记录、SHA-256 复用、四类阻塞原因、下载失败后重试、路径安全、TaskLog、内容变化更新和英文关键词识别。测试客户端依赖产生 2 条弃用警告，不影响本阶段结果，后续升级依赖时再处理。

## 当前没有实现

- PDF 解析、OCR、字段/条款提取及 `ContractParse`
- 合同规则、LLM、`ReviewRule`、`RuleHit`、`ReviewResult`
- 评论生成、评论回写及 `CommentLog`
- 真实审批系统接口
- 前端
- 数据库迁移脚本；当前由 SQLAlchemy 模型创建本地 SQLite 表

## 下一阶段建议（尚未开始）

下一阶段建议先设计“合同文档读取与文本获取边界”，分别确认 PDF、DOCX 和图片附件如何路由、什么条件触发 OCR、文本及定位信息如何保存、空文档如何阻塞。未经确认不实现解析器，也不读取本阶段下载的合同内容。
