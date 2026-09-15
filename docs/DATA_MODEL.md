# 核心领域对象设计

本文同时记录长期领域对象设计和当前已落地的数据模型。当前 SQLite 已实现八个核心领域对象，并以 `DocumentReadSnapshot` 和 `review_result_rule_hits` 支撑原文快照与结果证据关联。

## 1. ApprovalTask

职责：表示一个审批实例在本系统中的唯一处理任务，承担去重、主状态、阻塞信息和重试检查点。

当前字段：

- `id: int`：内部主键。
- `approval_code: str`：审批编号，用于展示。
- `instance_id: str`：审批平台唯一业务标识，建议唯一约束，作为去重依据。
- `approval_title: str`
- `applicant_name: str`
- `applied_at: datetime | None`
- `task_status: pending | parsing | reviewing | blocked | done`
- `write_status: not_written | writing | success | failed`
- `blocked_stage: str | None`：失败发生的业务步骤。
- `blocked_reason: str | None`：可读的失败原因，不只保存异常类名。
- `retry_target: parsing | reviewing | None`：人工重试应回到的状态。
- `retry_count: int`
- `created_at: datetime`
- `updated_at: datetime`

关系：一个任务有多个附件、解析记录、规则命中、评论日志和任务日志；通常只有一个当前有效审查结果。

## 2. ApprovalAttachment

职责：保存已识别主合同附件的来源标识、下载结果和文件校验信息。本阶段已经实现该对象。

建议字段：

- `id: int`
- `task_id: int`
- `external_attachment_id: str`
- `original_file_name: str`：审批系统返回的原始文件名，用于展示和审计。
- `file_type: str`
- `file_size: int | None`
- `file_path: str | None`
- `sha256: str | None`
- `is_main_contract: bool`
- `download_status: pending | success | failed`
- `download_error: str | None`
- `created_at: datetime`
- `updated_at: datetime`

关系：属于一个 `ApprovalTask`；拥有多个 `DocumentReadSnapshot`，以后还可被一个或多个 `ContractParse` 记录引用。数据库以 `task_id + external_attachment_id` 建立复合唯一约束，避免重复附件记录。

## 2.1 DocumentReadSnapshot（已实现的支撑对象）

职责：保存一次针对具体附件文件版本的原始文档读取结果，供 OCR、字段提取和规则审查跨请求查询与复用。它不承担合同字段或条款的业务解析职责。

字段：

- `id: int`
- `attachment_id: int`：外键关联 `approval_attachments.id`。
- `read_method: str`：当前为 `pdf_text`、`docx`、`image_pending_ocr` 或 `ocr`。
- `file_sha256: str`：读取时对应的附件内容版本。
- `file_type: str`
- `text: text`：Reader 获得的原始全文。
- `blocks_json: json`：按顺序保存 `text`、`page_number`、`block_index`。
- `page_count: int | null`：仅在格式有可靠页语义时保存；DOCX 为 null。
- `requires_ocr: bool`
- `read_status: success | ocr_required | failed`
- `error_code: str | null`
- `error_message: text | null`
- `reader_version: str`
- `created_at: datetime`

关系：`ApprovalAttachment 1 -> N DocumentReadSnapshot`。不保存 `task_id`，任务查询通过附件外键连接，避免重复的归属数据不一致。当前不拆分文本块表，因为没有单块 SQL 检索需求。

OCR 不增加新表或新字段，而是继续创建 `DocumentReadSnapshot`：

- 先前快照保留为 `read_method=pdf_text/image_pending_ocr`、`read_status=ocr_required`。
- OCR 成功新增 `read_method=ocr`、`read_status=success`、`requires_ocr=false`。
- OCR 失败新增 `read_method=ocr`、`read_status=failed`，保存明确错误码。
- `reader_version` 对 OCR 快照保存 Provider 及版本，例如 `rapidocr-3.9.2`。

这样字段/条款提取只读取成功的 `DocumentReadSnapshot`，不需要区分文本来自 pypdf、python-docx 还是 OCR。

## 3. ContractParse

职责：保存一份明确读取快照经过指定版本 Extractor 后得到的结构化合同事实及证据，不保存风险结论。

已实现字段：

- `id: int`
- `document_read_snapshot_id: int`：外键关联产生本次事实的明确原文快照。
- `basic_info_json: object`：合同标题、编号、主体、对方、金额、币种、生效/到期时间。
- `clause_info_json: object`：付款、交付、验收、违约、保密、数据、知识产权、争议解决条款。
- `parse_status: success | partial | failed`
- `parse_error: str | None`
- `extractor_name: str`
- `extractor_version: str`
- `created_at: datetime`

`basic_info_json` 和 `clause_info_json` 中每个重要字段统一保存：

```json
{
  "value": "500000",
  "source_text": "合同总金额为人民币500000元整。",
  "position": {
    "block_start": 4,
    "block_end": 4,
    "page_start": 1,
    "page_end": 1
  },
  "extract_status": "found",
  "extract_method": "regex_amount"
}
```

`extract_status` 使用 `found`、`not_found`、`ambiguous`、`failed`。DOCX 没有稳定页码时，`page_start/page_end` 为 null；`block_start/block_end` 仍来自读取快照。字段缺失或歧义只使整体状态成为 `partial`，Extractor 程序异常才是 `failed`。

关系：`DocumentReadSnapshot 1 -> N ContractParse`。不冗余保存 `task_id` 或 `attachment_id`，可沿快照和附件关系追溯任务。解析记录是不可变历史，因此不设置 `updated_at`；算法版本变化时新增记录。

## 4. ReviewRule

职责：定义一条可启停、可解释的审查规则。

建议字段：

- `id: int`
- `rule_code: str`：稳定且唯一的规则编码。
- `rule_name: str`
- `risk_level: low | medium | high`
- `rule_status: active | inactive`
- `match_mode: field_missing | numeric_threshold | keyword | presence | future_llm`
- `match_text: str`：JSON 格式的字段路径、关键词或阈值参数；保持原字段名，第一版不再拆配置表。
- `suggestion_text: str`
- `rule_version: str`：规则条件变化时显式升级，用于命中幂等和历史追踪。
- `updated_at: datetime`

关系：一条规则可产生多个 `RuleHit`。默认规则由启动 seed 按唯一 `rule_code` 补充，重复启动不会重复插入，也不会覆盖人工修改。`future_llm` 只预留并跳过，不执行模型调用。

## 5. RuleHit

职责：记录某份 ContractParse 为什么命中某一规则版本，以及证据来自合同哪里。

当前字段：

- `id: int`
- `contract_parse_id: int`
- `rule_id: int`
- `rule_version: str`
- `evidence_text: str`
- `evidence_position: JSON | None`：直接复制 ContractParse 的 block/page 范围。
- `evidence_type: source | missing | derived`
- `actual_value: str | None`
- `expected_value: str | None`
- `hit_message: str`
- `hit_status: hit`
- `created_at: datetime`

数据库以 `contract_parse_id + rule_id + rule_version` 唯一，重复执行复用已有命中；规则版本变化时新增命中并保留历史。第一版只保存实际命中，不保存 `not_hit`。不冗余 `task_id`，沿 `ContractParse -> DocumentReadSnapshot -> ApprovalAttachment -> ApprovalTask` 完整追溯。规则定义仍由外键关联；当前不做规则物理删除，避免破坏历史解释。

## 6. ReviewResult

职责：保存某份 ContractParse 在明确规则集合、RuleHit 集合和汇总算法版本下的不可变审查快照，用于查询和后续评论回写。

当前字段：

- `id: int`
- `contract_parse_id: int`
- `overall_risk_level: low | medium | high`
- `summary_text: str`
- `focus_points_json: list[dict]`：每项保存规则编码、名称、风险等级、说明、建议和证据。
- `comment_text: str`：确定性中文评论草稿，不表示已经回写。
- `review_status: completed | partial | failed`
- `review_error: str | None`
- `review_version: str`
- `rule_set_fingerprint: str`：active 规则代码及显式版本的稳定 SHA-256。
- `rule_hit_fingerprint: str`：实际 RuleHit ID、规则 ID 和版本的稳定 SHA-256。
- `created_at: datetime`

关系：`ContractParse 1 -> N ReviewResult`；`ReviewResult N <-> N RuleHit` 通过只有两个复合主键列的 `review_result_rule_hits` 关联表固定。ReviewResult 不冗余 `task_id`，沿 ContractParse、读取快照和附件追溯任务。相同 ContractParse、规则集合指纹、命中集合指纹和 review_version 才复用，否则新增并保留旧历史。

## 7. CommentLog

职责：记录每一次评论回写尝试、结果和外部响应，不把回写成败只压缩在任务表中。

当前字段：

- `id: int`
- `task_id: int`
- `review_result_id: int`
- `write_status: not_written | writing | success | failed`
- `write_response_text: str | None`：脱敏后的平台响应。
- `external_comment_id: str | None`
- `error_code: str | None`
- `error_message: str | None`
- `created_at: datetime`

关系：属于一个任务和一个 ReviewResult。`ApprovalTask.write_status` 是当前整体状态，`CommentLog` 是每次具体尝试；同一结果失败后重试会新增日志，不覆盖失败历史。评论正文只保存在 ReviewResult，CommentLog 不重复保存。

## 8. TaskLog

职责：记录任务全链路中的关键业务事件、状态变化和错误，支持人工定位和面试讲解。

建议字段：

- `id: int`
- `task_id: int`
- `log_level: info | warning | error`
- `log_type: status_change | external_call | parse | rule | persistence | comment`
- `stage: str`
- `log_content: str`
- `error_code: str | None`
- `created_at: datetime`

关系：属于一个任务。日志不得保存密钥、完整认证头或未脱敏的外部响应。

## 9. 对象关系总览

```text
ApprovalTask 1 ── * ApprovalAttachment
ApprovalAttachment 1 ── * DocumentReadSnapshot
DocumentReadSnapshot 1 ── * ContractParse
ContractParse 1 ── * RuleHit * ── 1 ReviewRule
ContractParse 1 ── * ReviewResult * ── * RuleHit
ReviewResult 1 ── * CommentLog
ApprovalTask 1 ── * CommentLog
ApprovalTask 1 ── * TaskLog
```

## 10. 暂缓决定

- 数据库产品和实际列类型留到数据库设计阶段确认。
- JSON 字段是否拆表，等样例解析结果稳定后再判断；第一版优先保持简单。
- 多附件合并审查策略尚未确定。第一版建议明确“一个主合同附件”，其他附件是否参与审查需用户确认后再设计。
