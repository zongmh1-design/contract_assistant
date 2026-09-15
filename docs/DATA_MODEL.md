# 核心领域对象设计

本文同时记录长期领域对象设计和当前已落地的数据模型。当前 SQLite 已实现 `ApprovalTask`、`TaskLog`、`ApprovalAttachment`、`DocumentReadSnapshot` 与 `ContractParse`；其余对象仍是后续阶段设计。

## 1. ApprovalTask

职责：表示一个审批实例在本系统中的唯一处理任务，承担去重、主状态、阻塞信息和重试检查点。

建议字段：

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
- `rule_status: enabled | disabled`
- `match_mode: deterministic | llm_assisted`
- `match_text: str`：匹配条件或规则参数的可读表示。
- `suggestion_text: str`
- `rule_version: int`
- `created_at: datetime`
- `updated_at: datetime`

关系：一条规则可产生多个 `RuleHit`。第一版优先实现 `deterministic`，`llm_assisted` 仅预留枚举，不在本阶段实现。

## 5. RuleHit

职责：记录某任务为什么命中某规则，以及证据来自合同哪里。

建议字段：

- `id: int`
- `task_id: int`
- `contract_parse_id: int`
- `rule_id: int`
- `rule_code_snapshot: str`
- `rule_name_snapshot: str`
- `risk_level: low | medium | high`
- `evidence_text: str`
- `evidence_position: str | None`
- `suggestion_text: str`
- `hit_status: hit | not_hit | error`
- `created_at: datetime`

保存规则名称、编码等快照，避免规则以后修改导致历史审查结果无法解释。通常只持久化 `hit` 和 `error`；是否保存全部 `not_hit` 在规则引擎设计阶段决定。

## 6. ReviewResult

职责：保存一次任务审查的最终汇总，用于展示和评论回写。

建议字段：

- `id: int`
- `task_id: int`
- `contract_parse_id: int`
- `overall_risk_level: low | medium | high`
- `summary_text: str`
- `focus_points_json: list[str]`
- `comment_text: str`
- `result_version: int`
- `created_at: datetime`

关系：属于一个任务和一次有效解析；汇总多个 `RuleHit`；被 `CommentLog` 引用。重试审查时可新增版本，旧结果保留审计。

## 7. CommentLog

职责：记录每一次评论回写尝试、结果和外部响应，不把回写成败只压缩在任务表中。

建议字段：

- `id: int`
- `task_id: int`
- `review_result_id: int`
- `write_status: writing | success | failed`
- `request_id: str | None`：本次调用标识，便于排查和幂等控制。
- `write_response_text: str | None`：脱敏后的平台响应。
- `error_message: str | None`
- `created_at: datetime`
- `finished_at: datetime | None`

关系：属于一个任务和一个审查结果。`ApprovalTask.write_status` 是当前状态，`CommentLog` 是完整尝试历史。

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
ApprovalTask 1 ── * RuleHit       * ── 1 ReviewRule
ContractParse 1 ── * RuleHit
ApprovalTask 1 ── * ReviewResult
ContractParse 1 ── * ReviewResult
ReviewResult 1 ── * CommentLog
ApprovalTask 1 ── * CommentLog
ApprovalTask 1 ── * TaskLog
```

## 10. 暂缓决定

- 数据库产品和实际列类型留到数据库设计阶段确认。
- JSON 字段是否拆表，等样例解析结果稳定后再判断；第一版优先保持简单。
- 多附件合并审查策略尚未确定。第一版建议明确“一个主合同附件”，其他附件是否参与审查需用户确认后再设计。
