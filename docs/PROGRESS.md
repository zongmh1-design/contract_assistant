# 项目进度

## 当前阶段

阶段 11：LLM 辅助字段 / 条款提取 —— 已完成。

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
- 新增 `ReviewRule` 与九条可重复 seed 的默认规则，`rule_code` 唯一；风险等级、阈值和建议文案不散落在 Engine 条件代码中。
- 新增 `RuleHit`，直接关联 `ContractParse` 与 `ReviewRule`，保存证据类型、原文/说明、block/page 位置、实际值、期望值和命中说明。
- 新增 `ContractParseSelector`，沿当前主附件和当前有效读取快照选择最新 success/partial 解析，旧文件或旧解析不会被规则阶段误用。
- 新增 `RuleEngine` 端口与 `DeterministicRuleEngine`，支持字段缺失、数值阈值、关键词和存在性；`future_llm` 只跳过，不调用模型。
- 第一批规则覆盖主体、金额、保密、验收、付款缺失，预付款比例、付款周期、自动续约及争议管辖信息。
- 相同 `ContractParse + ReviewRule + rule_version` 命中由数据库唯一约束保证不重复；规则版本变化新增命中并保留历史。
- 风险汇总按最高命中等级返回临时 `RuleReviewSummary`，本阶段不提前创建 `ReviewResult`。
- 规则开始时执行 `parsing -> reviewing`，成功保持 reviewing；无可用 ContractParse 或 Engine 异常阻塞于 reviewing，0 命中合法。
- 新增不可变 `ReviewResult`，直接关联 ContractParse，保存风险等级、确定性摘要、结构化关注点、评论草稿、状态、错误和汇总版本。
- 新增 `review_result_rule_hits` 关联表，固定每个结果实际使用的 RuleHit；结果不冗余 task_id，可沿完整来源链追溯。
- 规则完成日志加入 active rule-set fingerprint，结果生成前必须匹配该检查点；因此 0 命中可合法生成，规则版本改变则必须先重跑规则。
- 只选择当前 ContractParse、当前 active 规则版本对应的 RuleHit，不混用旧解析或旧规则版本命中。
- 使用标准库 SHA-256 计算规则集合与命中集合指纹；相同 parse、两类指纹和 review_version 才复用并记录 `REVIEW_RESULT_REUSED`。
- `DeterministicReviewResultBuilder` 按真实命中动态生成 summary、结构化 focus points 和中文 comment draft，不调用 LLM 或审批平台。
- 确定性规则完整执行记 completed；构建异常保存 failed ReviewResult 并阻塞于 reviewing。成功生成后仍保持 reviewing，等待后续评论回写。
- 新增 `CommentLog`，按回写尝试保存任务、ReviewResult、状态、响应、外部评论 ID 和错误；不重复保存评论正文。
- `CurrentReviewResultSelector` 只选择当前 ContractParse、当前规则/命中指纹对应的最新 completed/partial ReviewResult，不误用历史结果。
- `CommentWritebackService` 只依赖 ApprovalGateway；Mock 支持成功、上游异常、无效响应以及同一 review ID 的幂等返回。
- 评论开始时 `not_written/failed -> writing`；明确成功后 `writing -> success` 且任务 `reviewing -> done`，并记录完整 TaskLog。
- 失败保存 failed CommentLog，任务 `write_status=failed` 并阻塞于 `comment_writeback`，已完成的附件、读取、解析、规则和结果全部保留。
- 相同 ReviewResult 已有成功 CommentLog 时直接复用，不再次调用 Gateway，并记录 `COMMENT_WRITE_REUSED`。
- `/retry` 新增 blocked_stage 最小分派：comment_writeback 只恢复 reviewing 并重试评论；document_reading 指向专用接口；field_extraction/reviewing 明确拒绝，不再全部重跑附件。
- 新增 Mock 全业务闭环测试，从待办、附件、文档读取、ContractParse、规则、ReviewResult 到评论写回，最终 task=done、write_status=success，并验证七类数据外键追溯。
- 引入 Alembic，并创建覆盖当前 10 张业务/关联表、唯一约束、外键和索引的 baseline migration。
- 正式 FastAPI 启动不再自动 `create_all()` 或 seed；测试仍通过显式开关使用隔离数据库快速建表。
- 新增独立、幂等的默认规则 seed 命令，只补缺失 `rule_code`，不覆盖人工配置。
- 新增虚构中文文本型 PDF 演示合同，已验证四页可视布局和 pypdf 中文文本提取。
- 新增一键完整 Demo，真实调用已有 Service 完成待办、附件、读取、提取、规则、结果和 Mock 评论回写，最终为 `done + success`。
- Demo 使用独立数据库，`--reset` 只允许清理该精确文件；不 reset 的重复执行会复用既有结果，不重复评论。
- 新增迁移、约束、外键、seed、正式启动无副作用和 Demo 重复执行测试。
- 新增 `LLMProvider` 协议、OpenAI-compatible HTTP Provider 和确定性 `MockLLMProvider`；Provider 统一处理超时、HTTP/响应校验、模型名和 token usage。
- 新增 `LlmContractExtractor`，只请求 deterministic 的 not_found/ambiguous 字段，并把编号 blocks 与确定性上下文发送给模型。
- 模型只返回 value/status/block 范围；最终证据原文和页码由程序从快照重建，越界或原文不支持的值不能保存为 found。
- 合并时 deterministic found 永远优先；LLM 越权返回不同值只记录 `LLM_EXTRACTION_CONFLICT`。
- 新增 hybrid 提取 Service 和 API，先保存/复用 deterministic 记录，再新增 hybrid ContractParse；Provider/Schema 失败保存降级结果而不 blocked。
- `ContractParse.llm_metadata_json` 保存 provider、model、token usage、请求/补充字段、冲突、证据错误和降级信息，不保存密钥或认证头。
- 新增第二份 Alembic migration，不修改已提交 baseline。
- 新增 17 个 LLM 辅助测试，覆盖补充、歧义、证据位置、幻觉拒绝、冲突优先级、超时/Provider/JSON/Schema 降级、降级后恢复、历史、复用、Provider 变化、migration、未配置边界和真实 HTTP 适配器边界。

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
| POST | `/api/tasks/{task_id}/parse-contract/llm-assist` | 只补充 unresolved 字段并保存 hybrid ContractParse |
| GET | `/api/tasks/{task_id}/contract-parses` | 查询任务下的 ContractParse 历史 |
| POST | `/api/tasks/{task_id}/run-rules` | 执行确定性规则并返回命中及临时风险汇总 |
| GET | `/api/tasks/{task_id}/rule-hits` | 查询任务下可追溯的规则命中历史 |
| GET | `/api/review-rules` | 查询默认及当前规则定义 |
| POST | `/api/tasks/{task_id}/review-results` | 生成或复用最终审查结果快照 |
| GET | `/api/tasks/{task_id}/review-results` | 查询任务的 ReviewResult 历史 |
| POST | `/api/tasks/{task_id}/write-comment` | 写回或复用当前 ReviewResult 的审批评论 |
| GET | `/api/tasks/{task_id}/comment-logs` | 查询每一次评论回写尝试 |

## 测试结果

执行命令：

```powershell
uv --cache-dir .uv-cache --python-preference only-system run pytest -q --basetemp=.test-tmp
```

上一阶段结果：156 个测试全部通过，0 个失败。

本阶段新增 `llm_semantic` 模式及 4 条语义规则：违约责任失衡、知识产权归属、数据处理、特殊争议解决。`LlmSemanticRuleEngine` 只接收目标条款 blocks，结构化输出 `hit / not_hit / uncertain`；程序验证范围并重建证据，只有合法 hit 生成 RuleHit。

新增 `LlmRuleEvaluation` 保存判断状态、reason、模型/token/evaluator 元数据和可选 RuleHit 关联；同 parse、规则版本、evaluator、Provider、模型可复用。Provider/Schema/证据失败不 blocked，ReviewResult 标记 partial。第三份 migration 扩展 match_mode 并创建语义审计表。

本阶段验收：`compileall` 通过；169 个测试全部通过，0 个失败（2 条第三方弃用警告）；空 SQLite 数据库完成三段 migration，`alembic check` 无待生成操作；deterministic Demo 从空库运行到 `task_status=done / write_status=success`。

## 当前没有实现

- 真实 LLM 语义规则联网 smoke test；默认测试全部使用 MockLLMProvider
- 真实审批平台；当前评论回写使用 Mock Approval Gateway
- 真实审批系统接口
- 前端
- 生产级数据库方言与并发部署验证；当前 migration 和 Demo 以 SQLite 为验收环境

## 下一阶段建议（尚未开始）

下一阶段可考虑语义审查的真实 Provider 手工 smoke test与运维配置，或补齐 field_extraction/reviewing 的精确 retry；需要确认后再编码。

设计债务：comment_writeback 已支持精确恢复；document_reading 通过专用读取/OCR 接口恢复。field_extraction 与 reviewing 仍缺少按具体错误码分派的恢复入口，当前 `/retry` 会明确拒绝，不伪装支持。
