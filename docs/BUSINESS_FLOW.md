# 业务流程与状态流转

## 1. 完整主流程

```text
待办拉取
  -> 按 instance_id 新建或更新 ApprovalTask（去重）
审批详情
  -> 更新审批展示信息和附件元数据
附件下载
  -> 保存 ApprovalAttachment、文件路径和 SHA-256
文档解析
  -> 文档读取 -> 文本获取 -> 扫描件才 OCR
字段提取
  -> 基本字段 -> 主要条款 -> 证据与位置 -> 保存 ContractParse
规则审查
  -> 读取启用规则 -> 确定性规则 -> 必要时 LLM 辅助 -> 保存 RuleHit
风险汇总
  -> 总风险等级 -> 中文摘要 -> 审批关注点 -> 评论文本
结果保存
  -> 保存 ReviewResult
评论回写
  -> 保存 CommentLog -> 更新 write_status
完成
```

当前已使用 Mock Approval Gateway 完成整个确定性主流程；真实审批平台和 LLM 仍未实现。

## 2. 正常状态流转

| 当前状态 | 触发条件 | 下一状态 | 同时记录 |
| --- | --- | --- | --- |
| 无任务 | 首次拉取且 `instance_id` 不存在 | `pending` | 创建任务日志，`write_status=not_written` |
| `pending` | 详情有效、存在可处理附件，开始下载/解析 | `parsing` | 状态日志，清空旧阻塞信息 |
| `parsing` | 下载、文档解析、字段/条款提取均成功且结果已保存 | `reviewing` | 有效 `ContractParse` 标识 |
| `reviewing` | 规则、汇总、结果保存、评论回写全部成功 | `done` | `ReviewResult`、`CommentLog`、`write_status=success` |

重复拉取不创建新任务：更新允许刷新的审批标题、申请人、申请时间和附件元数据，但不覆盖正在处理或已经完成的内部结果。

## 3. 回写状态流转

```text
not_written -> writing -> success
                      -> failed
```

- 创建任务时为 `not_written`。
- 发起外部调用前先改为 `writing` 并创建 `CommentLog`。
- 收到明确成功响应后改为 `success`。
- 超时、拒绝、响应无法确认或接口异常时改为 `failed`；任务同时进入 `blocked`。
- 超时不等于对方一定未写入。当前以 `review_result_id` 作为上游幂等业务键；本地已有 success CommentLog 时直接复用，Mock Gateway 对同一结果也返回同一外部评论。

## 4. 失败进入 blocked

任何关键步骤失败都统一执行：

1. 保留此前已成功保存的附件、解析、命中和结果，不清空现场。
2. 写入 `blocked_stage`、`blocked_reason`、`retry_target`。
3. 新增错误级别 `TaskLog`；回写失败还要结束对应 `CommentLog`。
4. 通过集中状态服务将任务改为 `blocked`，禁止业务代码直接赋状态字符串。

典型映射：

| 失败点 | 示例 | `retry_target` |
| --- | --- | --- |
| 审批详情/附件 | 接口失败、附件不存在、下载或校验失败 | `parsing` |
| 文档与字段 | 文档为空、PDF 解析失败、OCR 失败、必要字段提取过程异常 | `parsing` |
| 规则与汇总 | 规则异常、汇总失败、结果保存失败 | `reviewing` |
| 评论回写 | 超时、平台拒绝、响应无法确认 | `reviewing`，复用有效结果，只重走回写检查点 |

## 5. blocked 人工重试

人工重试不是简单地把状态改回去，而是从已记录检查点恢复：

1. 管理员查看 `blocked_stage`、原因、最近日志和已有产物。
2. 管理员修复外部条件，例如补附件、替换样例、恢复接口或调整规则。
3. 系统验证任务当前确实为 `blocked`，增加 `retry_count` 并记录重试日志。
4. 按 `retry_target` 集中转换到 `parsing` 或 `reviewing`。
5. 复用仍然有效的成功产物，重新执行失败步骤及其后续步骤。
6. 再次失败则回到 `blocked` 并覆盖当前阻塞摘要，但历史 `TaskLog`/`CommentLog` 保留。

恢复规则：

- `attachment_preparation/parsing`：当前通用 `/retry` 保留附件准备恢复。
- `document_reading`：明确要求使用 `/read-document` 或 `/ocr` 专用入口，不自动回到附件步骤。
- `field_extraction/reviewing`：失败来源仍不够细，当前明确拒绝通用 retry，不伪装已支持精确恢复。
- `comment_writeback`：执行 `blocked -> reviewing`，复用有效 ReviewResult，只重新执行评论回写；不下载附件、不 OCR、不提取、不运行规则。
- `done` 默认不可重试；若合同或规则变化，应由未来明确的“重新审查”用例处理，不混入失败重试。

## 6. 状态管理约束

状态变化由 `app/core/task_state.py` 中一个明确入口集中管理（具体函数名在编码阶段确认），至少校验：

- 转换是否在允许表中。
- 进入 `reviewing` 前是否有成功解析记录。
- 进入 `done` 前是否已有审查结果且回写成功。
- 进入 `blocked` 时是否填写失败步骤和原因。
- 离开 `blocked` 时是否来自人工重试且存在合法 `retry_target`。

每次状态变化与业务数据保存应处于同一数据库事务，避免“数据已保存但状态未更新”或相反情况。
