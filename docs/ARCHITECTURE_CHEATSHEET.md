# 架构速记

```text
ApprovalGateway
  外部审批系统边界；当前用 Mock，可替换为真实 OA Adapter。
        ↓
ApprovalTask
  一张业务审批单；负责去重、任务状态、blocked 阶段和回写总状态。
        ↓
ApprovalAttachment
  审批附件元数据与本地文件；用 SHA-256 标识当前内容版本。
        ↓
DocumentReadSnapshot
  PDF/DOCX/OCR 得到的原始文本、blocks、页码、读取方法和版本快照。
        ↓
ContractParse
  从读取快照提取的合同字段和条款；每项保留 value、原文证据和位置。
        ↓
ReviewRule ──→ RuleHit
  Rule 定义风险等级和建议；Hit 保存某份解析实际命中的证据与说明。
        ↓
ReviewResult
  固定一次审查使用的 RuleHit，生成整体风险、关注点和评论草稿。
        ↓
CommentLog
  记录每次审批评论写回尝试、结果、外部评论 ID 和错误。
```

## 两条引擎线

```text
明确事实风险 -> DeterministicRuleEngine
复杂语义线索 -> LlmSemanticRuleEngine -> block 证据校验 -> RuleHit
```

LLM 不能直接决定风险等级、建议或审批结论。

## 三个状态重点

```text
pending -> parsing -> reviewing -> done
                     任意关键失败 -> blocked

not_written / failed -> writing -> success
```

只有评论写回成功，任务才能 `reviewing -> done`。

## 四种幂等依据

```text
业务唯一键：instance_id / approval_code
文件版本：SHA-256
算法版本：Reader / OCR / Extractor / Rule / Model
结果集合：rule-set fingerprint / rule-hit fingerprint
```

## 一句话讲项目

这是一个以证据可追溯、版本可复现和失败可恢复为核心的合同审批辅助审查后端；确定性规则优先，LLM 只做受控补充，外部审批平台当前使用 Mock。
