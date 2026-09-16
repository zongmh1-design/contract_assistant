# 面试讲解指南

这份文档按“面试官提问—候选人回答”组织。回答应以当前仓库的真实实现为准，不把 Mock、设计预留或未验证能力说成生产成果。

## 1. 为什么做合同审批审查系统？

合同审批的重复工作很多：找主合同附件、读取扫描件、提取金额和主体、检查条款、整理风险意见。人工处理速度慢，而且容易漏掉依据。我做这个项目是为了把这些可重复步骤自动化，并让每条风险都能回溯到合同原文。

系统只生成风险提示，不自动批准或拒绝合同，也不替代法务判断。这是业务边界，也是风险控制边界。

## 2. 你在项目中的职责是什么？团队其他人做什么？

这个仓库能证明的职责是：后端业务流程、数据模型、状态机、文档读取/OCR Adapter、字段与条款提取、确定性和 LLM 辅助规则、结果持久化、Mock 审批集成、Alembic、测试和演示脚本。

不能说“我带领了某某团队”或虚构真实同事。如果面试官要求按团队项目说明，可以用职责边界回答：我负责后端审查链路与集成边界；真实项目中，前端角色会负责审批人界面，平台集成角色会负责企业 OA 鉴权和接口联调，法务/业务专家会维护规则和验收风险结论，运维角色会负责生产数据库、密钥和监控。当前仓库没有这些团队协作记录。

## 3. 完整业务流程是什么？

```text
Mock ApprovalGateway 拉取待办
-> 按 instance_id / approval_code 去重保存 ApprovalTask
-> 获取审批详情并识别主合同
-> 下载 ApprovalAttachment，计算 SHA-256
-> DocumentReader 读取 PDF/DOCX；扫描 PDF/图片进入 OCR
-> 保存 DocumentReadSnapshot
-> ContractExtractor 提取字段和条款，保存 ContractParse
-> DeterministicRuleEngine + LlmSemanticRuleEngine
-> 保存合法 RuleHit
-> ReviewResultService 汇总风险并生成评论草稿
-> ApprovalGateway 写回评论，保存 CommentLog
-> task_status=done，write_status=success
```

关键事件和错误同时写入 TaskLog。

## 4. 为什么不用 LLM 直接审整份合同？

因为“全文给模型—直接返回高风险”难验证、成本高、结果不稳定，也无法回答风险依据在哪里。

确定性规则负责可以明确编码的问题，例如主体/金额/条款缺失、预付款比例、付款周期和关键词。LLM 语义规则只处理单方违约责任失衡、知识产权、数据处理和特殊争议解决等较难用正则判断的问题。

确定性规则优先。LLM 不能决定风险等级和建议，这些仍由 ReviewRule 定义；LLM 失败也不能清除已经完成的确定性审查。

## 5. OCR 怎么处理文本 PDF、扫描 PDF和图片？

- 文本型 PDF：`PdfDocumentReader` 使用 pypdf 按页提取文本，保留真实页码。
- 扫描 PDF：Reader 判断文本不足，保存 `ocr_required` 快照；OCR 阶段用 PyMuPDF 按页渲染临时图片，再逐页调用 RapidOCR，最后按页和 block 顺序合并。
- JPG/JPEG/PNG：直接标记需要 OCR，单张图片的 `page_number=1`，再由 OCR Adapter 识别。

OCR 结果仍转换为统一 DocumentReadResult，并保存新的 DocumentReadSnapshot；不会覆盖之前的 `ocr_required` 快照。PDF 临时页图片在临时目录处理，不永久堆积。

## 6. 如何防止 LLM 幻觉？

模型不返回可信 evidence 文本或页码，只返回 `block_start / block_end`。

系统随后回查当前 DocumentReadSnapshot：验证 block 存在、范围连续、属于目标条款，并由程序重新拼出 evidence_text 和 page/block 位置。字段值还要能被原文证据支持。

语义规则只有同时满足 `decision=hit` 和证据合法，才能创建 RuleHit。非法 block 会记录 `LLM_RULE_EVIDENCE_INVALID`，不会形成正式风险命中。

## 7. 为什么需要五个不同对象？

- `ApprovalAttachment`：记录审批系统中的文件身份、原文件名、本地路径、SHA-256 和下载状态。
- `DocumentReadSnapshot`：记录某个文件版本经过 Reader/OCR 后得到的原始文本、blocks、页码和读取状态。
- `ContractParse`：记录从原文中提取的业务事实和条款，以及每个事实的证据和 Extractor 版本。
- `RuleHit`：记录某条规则对某份 ContractParse 的正式命中、证据、位置和命中说明。
- `ReviewResult`：记录一次审查使用的 RuleHit 集合、整体风险、结构化关注点和评论草稿。

它们分别回答“文件是什么”“读出了什么”“业务事实是什么”“命中了什么风险”“最终结论是什么”。合并会失去职责边界和历史追踪。

## 8. 为什么保存这么多历史记录，不直接覆盖？

相同审批单的附件内容可能变化，所以先比较 SHA-256。即使文件不变，Reader/OCR、Extractor、规则、LLM 模型或汇总算法升级后，结果也可能不同。

系统保留：文件 SHA-256、Reader/OCR 版本、Extractor 名称和版本、Rule 版本、Provider/模型/evaluator 版本，以及 ReviewResult fingerprint。新版本创建新快照，旧记录不覆盖，因此可以解释“当时为什么得到这个结果”，也可以比较升级前后差异。

## 9. blocked 有什么意义？

`blocked` 表示任务没有被静默丢弃，而是在明确阶段停止，保留错误原因和已有成果，等待人工处理或重试。

典型情况：

- 附件：没有附件、找不到主合同、不支持类型、下载失败。
- 文档/OCR：文件缺失、损坏、内容为空、OCR 引擎或 PDF 渲染失败。
- 字段提取：没有可用读取快照或 Extractor 程序异常。
- 规则/结果：没有可用 ContractParse、规则引擎或结果构建异常。
- 评论回写：上游失败、响应无效、结果缺失或评论为空。

TaskLog 保存错误码和过程，任务表保存 `blocked_stage` 与 `blocked_reason`。

## 10. retry 怎么设计？哪些还有限制？

已经精确支持：

- `attachment_preparation / parsing`：恢复到 parsing 并重新准备附件。
- OCR 场景：调用专用 `/ocr`，使用现有附件恢复，不重新下载。
- `comment_writeback`：`blocked -> reviewing`，只重新执行评论回写。

明确限制：

- `document_reading` 需要根据原因调用 `/read-document` 或 `/ocr`，通用 `/retry` 不替用户猜测。
- `field_extraction` 没有通用恢复入口，需要从 parsing 检查点重新提取。
- `reviewing` 可能来自规则或 ReviewResult，当前通用 retry 会拒绝，需要按具体错误选择入口。

项目没有提前建设复杂工作流引擎，而是先实现能够明确解释的最小恢复分支。

## 11. 如何保证重复请求不会重复执行？

- ApprovalTask：数据库唯一约束保证 `instance_id` 和 `approval_code` 去重。
- ApprovalAttachment：`task_id + external_attachment_id` 唯一。
- 文件下载：比较 SHA-256；相同则复用，不同则更新附件记录。
- 文档读取/OCR：SHA-256 与 Reader/OCR 版本一致时复用成功快照。
- ContractParse：读取快照、Extractor 名称和版本一致时复用。
- RuleHit：ContractParse、Rule 和执行版本控制重复命中。
- ReviewResult：规则集合和命中集合生成 fingerprint，相同输入与汇总版本才复用。
- 评论：同一 ReviewResult 已有 success CommentLog 时不再调用 Gateway；上游还使用 review ID 作为幂等键。

## 12. 以后接真实企业审批平台怎么办？

业务 Service 只依赖 ApprovalGateway 接口：拉待办、取详情、下载附件、写评论。当前 MockApprovalGateway 只是 Adapter 的一个实现。

接真实平台时新增真实 Gateway，负责 HTTP、鉴权、字段映射、超时和平台错误；任务、文档、规则与结果服务不需要改成依赖某家 OA SDK。

仍需补做平台幂等键、限流、签名、回调安全和脱敏日志，这些当前没有假装完成。

## 13. 以后换大模型怎么办？

Extractor 和语义规则只依赖 LLMProvider 的 `generate_structured()`。Provider 负责 API、timeout、模型名、token usage 和厂商响应适配。

更换模型时实现或调整 Provider Adapter；`LlmContractExtractor`、`LlmSemanticRuleEngine`、ContractParse 和 RuleHit 的业务结构不用重写。当前 OpenAI-compatible Adapter 尚未完成真实网络 smoke test。

## 14. 项目当前最大的不足是什么？

必须直接回答：

1. 企业审批平台仍是 Mock，没有真实 OA 鉴权和联调。
2. 真实模型环境的网络 smoke test尚未完成，只验证了 Adapter、MockTransport 和降级边界。
3. 数据库迁移和 Demo 以 SQLite 为验收环境，生产数据库方言、并发、锁和多实例幂等还需验证。
4. 没有前端，当前通过 API、脚本和数据库查询演示。
5. 字段提取和 reviewing 阶段的通用 retry 仍有限制。

这些限制不影响当前培训与求职演示定位，但不能包装成已经达到企业生产上线标准。

## 15. 你认为项目最值得讲的技术点是什么？

不是“用了多少 AI 技术”，而是三个可控性设计：

1. 原始文件、读取文本、业务事实、规则命中和最终结果分层，并保留版本历史。
2. 确定性规则优先，LLM 只能补充，且所有正式命中必须有可回查原文证据。
3. 外部系统失败进入 blocked 并保留已完成成果，重复请求通过唯一键、SHA、版本和 fingerprint 幂等处理。
