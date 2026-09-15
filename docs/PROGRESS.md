# 项目进度

## 当前阶段

阶段 6：ContractParse + 合同字段/条款提取基础闭环 —— 已完成。

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
- 新增 `DocumentReadSnapshot`，按附件保存读取方式、文件 SHA-256、原始文本、blocks JSON、页数、OCR 标记、状态、错误和 Reader 版本。
- 建立 `ApprovalAttachment 1 -> N DocumentReadSnapshot` 外键关系，不在快照中冗余保存 `task_id`。
- 成功结果在附件 SHA-256 和 Reader 版本一致时直接复用，并记录 `DOCUMENT_READ_REUSED`；任一变化都会重新读取并新增快照，旧快照保留。
- PDF/DOCX 成功结果、OCR_REQUIRED、损坏/空内容及有附件归属的文件缺失结果都会持久化。
- 新增 `GET /api/tasks/{task_id}/document-reads`，可查询来源附件、文本、blocks、状态和错误信息。
- 新增 `OcrEngine` 端口、真实 `RapidOcrEngine` 和确定性 `MockOcrEngine`；真实 Provider 已在当前 CPU 环境完成图片 smoke test。
- 新增 `PyMuPdfPageRenderer`，只在扫描 PDF OCR 路径将页面渲染到临时目录，完成后自动清理。
- 新增 `DocumentOcrService`：图片按第 1 页识别，扫描 PDF 按真实页序逐页识别，`block_index` 在全文范围连续递增。
- OCR 成功新增 `read_method=ocr` 的成功快照，不覆盖原 `ocr_required` 快照；失败也保存带错误码的 OCR 快照。
- 相同附件 SHA-256、OCR 版本和成功 OCR 快照直接复用并记录 `DOCUMENT_OCR_REUSED`；SHA 或版本变化会重新执行。
- 新增 `POST /api/tasks/{task_id}/ocr`。文档读取阻塞任务可直接 `blocked -> parsing -> OCR`，不重新下载附件。
- 正常文本型 PDF 调用 OCR 会返回 `OCR_NOT_REQUIRED`；OCR 引擎、空结果、不支持类型、输入文件和 PDF 渲染错误均明确处理。
- 新增 `DocumentSourceSelector`，集中选择当前主附件、当前 SHA-256 下最新成功读取快照，旧文件快照不会被使用。
- 新增 `ContractParse`，直接外键关联 `DocumentReadSnapshot`，保存基本字段、主要条款、证据、状态及 Extractor 名称/版本。
- 新增统一 `ContractExtractor`、确定性实现和 Mock。当前使用正则、标签、条款标题及 block 顺序，不调用 LLM。
- 基本字段覆盖标题、编号、甲乙方、金额、币种、生效日期和到期日期；条款覆盖付款、交付、验收、违约、保密、数据、知识产权和争议解决。
- 每个事实保存 value、source_text、block/page 范围、extract_status 和 extract_method；DOCX 页码保持 null。
- 全部事实 found 时解析为 success；正常完成但存在 not_found/ambiguous/failed 字段时为 partial；Extractor 程序异常才为 failed 并阻塞于 field_extraction。
- 相同读取快照和 Extractor 名称/版本的 success/partial 结果直接复用；快照或版本变化新增解析记录并保留旧历史。
- 新增虚构中文合同文本与中文扫描 PDF；真实 RapidOCR 成功识别合同、编号、甲乙方、人民币金额和付款关键词。

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
| GET | `/api/tasks/{task_id}/document-reads` | 查询任务下所有文档读取快照 |
| POST | `/api/tasks/{task_id}/ocr` | 对明确需要 OCR 的主合同执行或复用 OCR |
| POST | `/api/tasks/{task_id}/parse-contract` | 提取或复用合同结构化事实 |
| GET | `/api/tasks/{task_id}/contract-parses` | 查询任务下的 ContractParse 历史 |

## 测试结果

执行命令：

```powershell
uv --cache-dir .uv-cache --python-preference only-system run pytest -q --basetemp=.test-tmp
```

结果：73 个测试全部通过，0 个失败。结构化提取新增 14 个测试，覆盖当前快照选择、旧 SHA 排除、八项基本字段、八类条款、跨 block/page 证据、DOCX null 页码、缺失/歧义/字段失败、系统异常 blocked、无快照 blocked、持久化查询、快照及 Extractor 版本变化历史、复用及真实中文 RapidOCR smoke。测试客户端依赖产生 2 条弃用警告，不影响本阶段结果。

## 当前没有实现

- 合同规则、LLM、`ReviewRule`、`RuleHit`、`ReviewResult`
- 评论生成、评论回写及 `CommentLog`
- 真实审批系统接口
- 前端
- 数据库迁移脚本；当前由 SQLAlchemy 模型创建本地 SQLite 表

## 下一阶段建议（尚未开始）

下一阶段可选择先设计“LLM 辅助补充缺失/歧义字段”，或优先设计确定性的 `ReviewRule + RuleHit`。建议先做规则闭环，因为当前结构化事实已经足以支持可解释的缺失项与数值规则；需要确认后再编码。

设计债务：当前 retry 始终执行 `blocked -> parsing -> 重新准备附件`。后续失败点扩展到 `document_reading`、`field_extraction`、`reviewing`、`comment_writeback` 后，需要按 `blocked_stage` 从正确检查点恢复；本阶段未提前实现复杂恢复引擎。
