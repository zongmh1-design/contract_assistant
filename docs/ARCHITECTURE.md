# 架构设计

## 1. 设计目标

采用单体 Python 应用和清晰分层。一次业务处理在同一进程中按顺序执行，先不引入队列、微服务、工作流框架或多 Agent。

## 2. 第一版调用关系

```text
API / script
  -> ApprovalWorkflowService        编排一个审批任务的完整流程
      -> ApprovalGateway            拉取详情、下载附件、回写评论
      -> TaskRepository             任务去重、状态和日志持久化
      -> ContractParser             文档读取、文本/OCR、字段和条款提取
      -> ContractParseRepository    保存解析结果与证据
      -> ContractReviewService      执行规则并汇总风险
          -> ReviewRuleRepository   读取启用规则
          -> RuleHitRepository      保存规则命中
      -> ReviewResultRepository     保存最终审查结果
      -> CommentLogRepository       保存每次回写结果
```

本阶段只确认这些职责边界，不创建接口类和实现类。

当前已经落地的文档读取持久化调用链：

```text
POST /api/tasks/{task_id}/read-document
  -> DocumentReadingService
      -> AttachmentRepository             获取已下载的主合同附件
      -> DocumentReadRepository           按附件 SHA-256 和 Reader 版本查复用结果
      -> DocumentReaderRouter             路由 PDF / DOCX / Image Reader
      -> DocumentReadSnapshot             保存原始文本、blocks、状态和错误
      -> TaskRepository / TaskStateService 记录日志，失败进入 blocked

GET /api/tasks/{task_id}/document-reads
  -> DocumentReadRepository
      -> ApprovalAttachment 外键连接查询
```

`DocumentReadingService` 只在没有可复用成功快照时访问本地文件。`DocumentReadSnapshot` 与后续 `ContractParse` 分开：前者保存文件读取事实，后者未来保存字段和条款等业务理解结果。

当前 OCR 调用链：

```text
POST /api/tasks/{task_id}/ocr
  -> DocumentOcrService
      -> DocumentReadRepository       校验 ocr_required 前置快照 / 查找可复用 OCR
      -> TaskStateService             blocked(document_reading) -> parsing
      -> OcrEngine                    单张图片文字识别端口
          -> RapidOcrEngine           真实 CPU Provider
          -> MockOcrEngine            确定性测试 Provider
      -> PdfPageRenderer              扫描 PDF 专用页面渲染端口
          -> PyMuPdfPageRenderer      临时目录内逐页生成 PNG
      -> DocumentReadSnapshot         新增 ocr 成功或失败快照
      -> TaskRepository               记录 OCR 开始、成功、复用或错误
```

图片直接交给 `OcrEngine`；扫描 PDF 先按真实页序渲染到临时目录，再逐页 OCR。临时 PNG 在成功或异常后都由上下文管理器清理，不进入 `storage/contracts/` 或数据库。业务服务不导入 RapidOCR API，因此以后替换本地或云 Provider 时不修改 OCR 编排规则。

当前合同事实提取调用链：

```text
POST /api/tasks/{task_id}/parse-contract
  -> ContractExtractionService
      -> DocumentSourceSelector          当前主附件 + 当前 SHA + 最新 success 快照
      -> ContractParseRepository         查找同快照、同 Extractor 版本的可复用结果
      -> ContractExtractor               统一结构化提取端口
          -> DeterministicContractExtractor  正则、标签和条款标题
          -> MockContractExtractor           异常及复杂结果测试
      -> ContractParse                   保存字段、条款、证据和提取状态
      -> TaskRepository / TaskStateService 记录日志；系统失败时 blocked

GET /api/tasks/{task_id}/contract-parses
  -> ContractParseRepository
```

Extractor 只读取 `DocumentReadSnapshot.blocks_json` 并产生统一 Schema；Service 不理解正则和条款标题。未来 LLM Extractor 必须输出相同结构，但本阶段没有 LLM 调用。字段或条款 `not_found/ambiguous` 是有效业务结果，不触发 blocked。

当前确定性规则审查调用链：

```text
POST /api/tasks/{task_id}/run-rules
  -> ContractReviewService
      -> ContractParseSelector          当前主附件 -> 当前读取快照 -> 最新 success/partial 解析
      -> ReviewRuleRepository           读取 active 规则
      -> TaskStateService               parsing -> reviewing
      -> RuleEngine
          -> DeterministicRuleEngine    按 match_mode 和 match_text 执行
      -> RuleHitRepository              按 parse + rule + version 新建或复用命中
      -> RuleReviewSummary              即时汇总风险等级，不提前持久化 ReviewResult
      -> TaskRepository                 记录完成、复用或错误

GET /api/tasks/{task_id}/rule-hits
  -> RuleHitRepository

GET /api/review-rules
  -> ReviewRuleRepository
```

Engine 不决定风险等级和建议文案，这些属于 `ReviewRule` 数据；Service 不编写具体风险判断。证据直接使用 `ContractParse` 中已有的 `source_text` 和 `position`。缺失类命中明确标记 `evidence_type=missing`，派生阈值命中标记为 `derived`。

当前最终审查结果持久化调用链：

```text
POST /api/tasks/{task_id}/review-results
  -> ReviewResultService
      -> ContractParseSelector             选择当前有效 ContractParse
      -> ReviewRuleRepository              计算当前 active rule-set fingerprint
      -> TaskRepository                    校验对应规则审查完成检查点
      -> RuleHitRepository                 只取该 parse、当前 active 规则版本的命中
      -> ReviewResultRepository            按 parse + 两个 fingerprint + version 复用
      -> DeterministicReviewResultBuilder  生成等级、摘要、关注点和评论草稿
      -> ReviewResult + association table  保存不可变快照及实际使用的 RuleHit
      -> TaskRepository / TaskStateService 记录日志；失败时 blocked

GET /api/tasks/{task_id}/review-results
  -> ReviewResultRepository
```

规则运行日志中的 `ContractParse + rule-set fingerprint` 是本阶段的最小完成检查点：它既允许 0 RuleHit 的合法审查，又阻止规则版本改变后直接沿用旧审查。`review_result_rule_hits` 关联表固定结果实际使用的命中，后续查询不重新推断历史。评论草稿只存储，不调用 Approval Gateway。

当前评论回写调用链：

```text
POST /api/tasks/{task_id}/write-comment
  -> CommentWritebackService
      -> CurrentReviewResultSelector       当前 parse + rule-set + hit-set + completed
      -> CommentLogRepository              查找该结果已有 success，或创建 writing 尝试
      -> TaskStateService                   not_written/failed -> writing
      -> ApprovalGateway
          -> MockApprovalGateway            成功、失败、无效响应和上游幂等
      -> CommentLogRepository              保存 success/failed、响应或错误
      -> TaskStateService                   成功：writing -> success 且 reviewing -> done
                                            失败：writing -> failed 且 reviewing -> blocked

POST /api/tasks/{task_id}/retry
  -> TaskRetryService
      -> comment_writeback: blocked -> reviewing -> 只重试 CommentWritebackService

GET /api/tasks/{task_id}/comment-logs
  -> CommentLogRepository
```

业务 Service 只依赖 `ApprovalGateway` 协议，不导入 Mock。`review_result_id` 同时作为上游幂等业务键；本地若已存在该结果的成功 CommentLog，直接复用且不再调用 Gateway。

## 3. 各层职责

| 层/目录 | 职责 | 不应承担的职责 |
| --- | --- | --- |
| `app/api/` | 参数校验、调用服务、返回结果 | 不写审查规则和状态转换 |
| `app/schemas/` | API 和外部接口输入输出结构 | 不作为数据库模型 |
| `app/models/` | 8 个核心对象及持久化映射 | 不编排业务流程 |
| `app/services/` | 用例编排、风险汇总、评论生成 | 不直接依赖 Mock 文件 |
| `app/repositories/` | 数据读写、去重查询 | 不决定业务状态 |
| `app/integrations/` | 审批系统端口、Mock/真实适配器 | 不执行合同审查 |
| `app/parsers/` | 文档读取、文本/OCR、字段与条款提取 | 不执行审查规则 |
| `app/rules/` | 规则匹配和命中生成 | 不回写审批系统 |
| `app/core/` | 配置、日志、枚举、集中状态流转 | 不放万能工具函数 |

## 4. 关键输入、输出、保存位置与失败策略

| 模块 | 输入 | 输出 | 保存位置 | 失败处理 |
| --- | --- | --- | --- | --- |
| 待办同步 | `limit` | 新建或更新的 `ApprovalTask` | 任务仓储 | 单项失败记录日志；无法识别业务标识时不创建任务 |
| 详情与附件 | `instance_id`、附件标识 | 审批详情、本地文件与校验信息 | 任务、附件仓储和受控文件目录 | 关键接口、附件缺失或校验失败时 `blocked` |
| 文档读取 | 主合同附件 | 原始文本、文本块与位置 | `DocumentReadSnapshot` | 空文档、读取失败或需要 OCR 时 `blocked` |
| OCR | `ocr_required` 快照及附件 | 带页码的统一读取结果 | 新的 `DocumentReadSnapshot` | 引擎、渲染、文件或空内容错误时保存失败快照并 `blocked` |
| 合同事实提取 | 当前有效读取快照 | 字段、条款、证据与定位 | `ContractParse` | 单字段缺失记 `partial`；找不到快照或程序异常才 `blocked` |
| 规则审查 | 当前有效 ContractParse、active 规则 | `RuleHit` 与临时汇总 | `RuleHit`；汇总仅响应返回 | 无解析或引擎异常时阻塞于 `reviewing`；0 命中合法 |
| 风险汇总 | 当前 ContractParse、规则检查点和当前 RuleHit | `ReviewResult` | 结果表及 RuleHit 关联表 | 无规则审查时 blocked；0 命中正常生成 low；构建异常保存 failed 后 blocked |
| 评论回写 | 审批实例、审查结果 | 平台响应 | 评论日志及任务回写状态 | 回写失败时 `blocked` 且保留已保存结果 |

## 5. 外部系统隔离

`ApprovalGateway` 只定义四项业务能力：

- `list_pending_contract_approvals(limit)`
- `get_contract_approval(instance_id)`
- `download_contract_attachment(instance_id, attachment_id, file_name)`
- `write_approval_comment(instance_id, review_id, comment_text)`

Mock 与未来真实平台都遵守同一输入输出约定。适配器负责把外部字段、错误码转换为本系统结构和明确异常。

## 6. 第一版目录结构

只在对应阶段需要时创建目录，不提前创建空目录。

```text
contract_assistant/
├─ app/
│  ├─ main.py
│  ├─ api/
│  │  ├─ approvals.py
│  │  └─ reviews.py
│  ├─ models/
│  │  ├─ approval.py
│  │  ├─ contract.py
│  │  └─ review.py
│  ├─ schemas/
│  │  ├─ approval.py
│  │  └─ review.py
│  ├─ services/
│  │  ├─ approval_sync_service.py
│  │  ├─ attachment_preparation_service.py
│  │  └─ approval_workflow_service.py
│  ├─ repositories/
│  │  ├─ task_repository.py
│  │  ├─ attachment_repository.py
│  │  ├─ contract_parse_repository.py
│  │  └─ review_repository.py
│  ├─ integrations/
│  │  └─ approval/
│  │     ├─ gateway.py
│  │     └─ mock_gateway.py
│  ├─ parsers/
│  │  └─ contract_parser.py
│  ├─ rules/
│  │  └─ contract_rules.py
│  └─ core/
│     ├─ config.py
│     ├─ logging.py
│     └─ task_state.py
├─ tests/
│  ├─ unit/
│  └─ integration/
├─ scripts/
├─ sample_data/
├─ docs/
├─ pyproject.toml
└─ uv.lock
```

`scripts/` 只放本地演示或初始化入口，`sample_data/` 只放脱敏样例；没有实际内容前不创建。

## 7. 建议技术方案与依赖说明

下表是后续编码建议，本阶段不安装依赖。

| 技术/依赖 | 解决的问题 | 选择原因 | 更简单的替代方案 |
| --- | --- | --- | --- |
| Python 3.12 | 应用运行语言 | 团队学习成本低，类型提示和生态成熟 | Python 3.11，若现有环境更稳定可直接使用 |
| `uv` | 环境、依赖和锁文件管理 | 安装快，能用 `uv.lock` 固定环境 | `venv + pip + requirements.txt` |
| FastAPI | 提供工具服务 HTTP 入口和参数校验 | 结构清楚，自动接口文档便于演示 | 第一小阶段仅用 Python 脚本；但最终仍需服务入口 |
| Uvicorn | 本地运行 FastAPI ASGI 应用 | 配置简单，是 FastAPI 常用的轻量运行方式 | 只在测试中使用 `TestClient`，但无法作为人工调用入口 |
| Pydantic | 校验 API、Mock 和服务边界的数据 | 与 FastAPI 配合直接，结构化错误清楚 | `dataclasses` 手工校验，依赖少但边界校验代码更多 |
| pypdf | 从文本型 PDF 按页提取文本 | API 小、纯 Python、能直接保留 PDF 页序 | PyMuPDF，速度和渲染能力更强，但依赖更重且当前阶段不需要渲染能力 |
| python-docx | 按正文顺序读取 DOCX 段落和表格 | 成熟、接口直接，适合第一版结构化遍历 | 直接解析 OOXML ZIP，依赖更少但代码复杂、维护成本高 |
| RapidOCR | 本地识别图片中的中英文文本 | 提供预训练中英文模型，可通过简单适配器运行 | Tesseract 需要额外安装程序和中文语言包；云 OCR 有外部费用与网络依赖 |
| ONNX Runtime CPU | 执行 RapidOCR 模型推理 | Windows/Python 3.12 有预编译包，不依赖 CUDA | Paddle 推理体积更大；GPU Runtime 当前没有必要 |
| PyMuPDF | 把扫描 PDF 按页渲染成 OCR 输入图片 | Python 内可控、保留页序，不依赖系统 Poppler | `pdftoppm` 更简单，但要求部署机器安装系统程序 |
| SQLAlchemy 2.x | 8 个对象的关系映射和事务管理 | SQLite/MySQL 均可用，后续替换数据库成本低 | 直接使用 `sqlite3`，更少依赖但映射和事务代码更分散 |
| Alembic | 数据库结构可追踪升级 | 与 SQLAlchemy 配套，避免手工改表不可复现 | 第一版早期重建 SQLite；有稳定数据后不安全 |
| SQLite | 本地持久化与闭环演示 | 零运维、文件级数据库，适合培训项目 | 内存仓储/JSON 文件更简单，但难验证事务、去重和关系 |
| httpx | 未来真实审批系统的 HTTP 调用 | 同时支持同步/异步、超时配置和测试替身 | 标准库 `urllib`，无新增依赖但可读性和测试便利性较差 |
| pytest | 正常、异常、边界和状态重试测试 | 测试表达简洁、生态成熟 | 标准库 `unittest`，无新增依赖但样板代码较多 |
| ReportLab（仅开发依赖） | 生成确定性的单页、多页和空文本 PDF 测试夹具 | 测试文件可重复生成，不依赖私人文件 | 提交手工制作 PDF，但来源和重建过程不透明 |

暂不建议加入任务队列、Redis、工作流框架和容器编排。单进程顺序执行已经能表达第一版业务；需要后台并发或多实例抢占时再重新评估。
