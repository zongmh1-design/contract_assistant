# 项目进度

## 当前阶段

阶段 3：合同文档读取与文本获取最小闭环 —— 已完成。

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
- 新增统一 `DocumentReadResult`、文本块和读取状态值对象，没有创建 `ContractParse` 或新数据库表。
- 新增静态 `DocumentReaderRouter`，集中把 PDF、DOCX、JPG、JPEG、PNG 分派给三个简单 Reader。
- 文本型 PDF 使用 pypdf 按页提取，文本块保留 1 开始的真实页码；多页文本保持原页序。
- 扫描型或有效文本不足的 PDF 返回 `requires_ocr=true`，并以 `OCR_REQUIRED` 阻塞。
- DOCX 使用 python-docx 按正文顺序读取段落与表格，使用 `block_index` 表示顺序，`page_number=null`，不伪造分页。
- JPG、JPEG、PNG 明确返回需要 OCR，本阶段不执行 OCR。
- 统一处理文件不存在、0 字节、损坏、无法读取和正文无效，并写入 TaskLog。
- 新增 `POST /api/tasks/{task_id}/read-document` 最小验证入口。

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
| POST | `/api/tasks/{task_id}/read-document` | 路由 Reader，返回文本和基础位置 |

## 测试结果

执行命令：

```powershell
uv --cache-dir .uv-cache --python-preference only-system run pytest -q --basetemp=.test-tmp
```

结果：32 个测试全部通过，0 个失败。阶段 1–2 的 16 个测试继续通过；新增 16 个测试实例覆盖 PDF 文本与页码、多页顺序、PDF OCR 判断、DOCX 段落和表格顺序、图片 OCR 标记、不存在/0 字节/损坏/空内容、Reader 路由、失败日志及任务/附件不重复。测试客户端依赖产生 2 条弃用警告，不影响本阶段结果。

## 当前没有实现

- OCR、字段/条款提取及 `ContractParse`
- 合同规则、LLM、`ReviewRule`、`RuleHit`、`ReviewResult`
- 评论生成、评论回写及 `CommentLog`
- 真实审批系统接口
- 前端
- 数据库迁移脚本；当前由 SQLAlchemy 模型创建本地 SQLite 表

## 下一阶段建议（尚未开始）

下一阶段建议先设计“OCR 最小闭环”或“读取结果持久化边界”，二者都需要先确认再编码。不得直接进入合同字段、条款或 LLM 提取。

设计债务：当前 retry 始终执行 `blocked -> parsing -> 重新准备附件`。后续失败点扩展到 `document_reading`、`field_extraction`、`reviewing`、`comment_writeback` 后，需要按 `blocked_stage` 从正确检查点恢复；本阶段未提前实现复杂恢复引擎。
