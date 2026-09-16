# 合同审批审查系统

一个面向培训、演示和求职讲解的单体 Python 项目。系统从企业审批待办中取得合同，完成附件准备、文档读取/OCR、结构化事实提取、确定性规则审查、风险结果保存和评论回写。系统只提供风险提示，不替代人工审批。

## 核心流程

```text
Mock 待办 -> 主合同附件 -> PDF/DOCX 读取或 OCR
-> ContractParse -> ReviewRule / RuleHit -> ReviewResult
-> Mock 评论回写 -> done
```

所有关键失败都会记录 `TaskLog` 并进入 `blocked`。成功结果按文件哈希和实现版本复用，历史快照不会被覆盖。

## 真实能力与 Mock 边界

当前真实实现：

- PDF、DOCX 文本读取和位置保留
- RapidOCR 本地中英文 OCR，以及扫描 PDF 按页渲染
- 确定性字段/条款提取和证据定位
- 确定性风险规则、风险汇总和评论草稿
- SQLAlchemy 持久化、Alembic 迁移、状态流转和失败日志

当前 Mock：

- 企业审批平台待办拉取
- 企业审批详情和附件下载
- 企业审批评论回写

项目尚未接入真实企业 OA、前端或异步任务队列。当前已提供可选的 OpenAI-compatible LLM 信息抽取 Provider，但默认未配置模型和 API Key，也不执行 LLM 风险审查。

## 技术栈

- Python 3.12、FastAPI、Pydantic
- SQLAlchemy 2.x、Alembic、SQLite
- pypdf、python-docx
- RapidOCR、ONNX Runtime CPU、PyMuPDF
- OpenAI-compatible HTTP Provider（可选，用于辅助字段/条款提取和语义规则）
- pytest、uv

## 目录结构

```text
app/                 API、领域模型、服务、仓储和外部适配器
alembic/             数据库迁移环境与版本脚本
scripts/             默认规则 seed 和完整 Demo 命令
sample_data/         脱敏、虚构的公开演示合同
tests/               单元和业务集成测试
docs/                范围、架构、数据模型、流程和决策记录
storage/             运行时下载文件，不提交 Git
```

`sample_data/demo_procurement_contract.pdf` 及其生成脚本只用于系统演示和测试，不包含真实个人或公司数据。

## 安装

```powershell
uv sync
```

项目默认使用根目录下的 `contract_assistant.db`。该数据库文件被 Git 忽略。

## 初始化数据库

正式运行使用 Alembic，不依赖应用启动时的 `create_all()`：

```powershell
uv run alembic upgrade head
uv run python -m scripts.seed_review_rules
```

如需指定数据库，可设置 `CONTRACT_ASSISTANT_DATABASE_URL` 后执行 Alembic，并向 seed 命令传入相同的 `--database-url`。seed 可重复执行，只补充缺失的 `rule_code`，不会覆盖人工配置。

## 启动 API

完成迁移和 seed 后运行：

```powershell
uv run uvicorn app.main:app --reload
```

接口文档：`http://127.0.0.1:8000/docs`。

## 运行完整 Demo

```powershell
uv run python -m scripts.run_contract_demo --reset
```

Demo 使用独立的 `demo_contract_assistant.db` 和 `storage/demo_contracts/`，会自行执行 migration 和 seed，并真正调用现有业务 Service。`--reset` 只允许删除这个项目专用 Demo 数据库，不会删除正式数据库。

再次运行时可以省略 `--reset`：

```powershell
uv run python -m scripts.run_contract_demo
```

第二次运行会复用已完成的任务、读取、解析、规则结果和成功评论，不会制造重复审批、规则或评论。

## 运行测试

```powershell
uv run python -m compileall -q app tests scripts
uv run pytest -q --basetemp=.test-tmp
```

测试通过独立 SQLite 数据库显式使用 `create_all()` 加速，不代表正式启动会自动建表。

## Real LLM configuration

真实 Provider 只从环境变量读取配置。`.env.example` 仅提供变量名和占位值；项目不会自动读取 `.env`，需要由 PowerShell、部署平台或密钥管理工具把变量注入进程环境。

```powershell
$env:LLM_BASE_URL="https://api.example.com/v1"
$env:LLM_API_KEY="replace-with-your-api-key"
$env:LLM_MODEL="replace-with-model-name"
$env:LLM_TIMEOUT_SECONDS="30"
```

- `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 必须同时配置。
- `LLM_TIMEOUT_SECONDS` 可选，默认 30 秒，必须为正数。
- 全部核心变量未配置时，正式应用不会创建真实 Provider，也不会自动调用外部模型。
- 部分配置会在启动时明确报错，错误信息只包含缺失变量名，不包含密钥值。

配置后可以执行独立 smoke test：

```powershell
uv run python -m scripts.smoke_test_llm
```

脚本只使用虚构的小型合同片段，分别验证字段补充和违约责任语义判断，并显示模型、耗时、token usage、证据 block 和本地校验结果。若 Provider 不返回 usage，会显示 `unavailable`，不会自行估算。真实调用可能产生模型费用。

默认 Demo 不调用真实 LLM，以保证离线、稳定和可重复；默认 pytest 使用 MockLLMProvider 或 httpx MockTransport，不需要 API Key、网络或付费调用。

## 主要 API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/tasks/sync` | 拉取 Mock 待办并去重 |
| GET | `/api/tasks` | 查询任务 |
| POST | `/api/tasks/{task_id}/prepare-attachment` | 准备主合同附件 |
| POST | `/api/tasks/{task_id}/read-document` | 读取 PDF/DOCX/图片路由结果 |
| POST | `/api/tasks/{task_id}/ocr` | 对需要 OCR 的附件执行 OCR |
| POST | `/api/tasks/{task_id}/parse-contract` | 提取字段和条款 |
| POST | `/api/tasks/{task_id}/parse-contract/llm-assist` | 用 LLM 补充 unresolved 字段并保存 hybrid 解析 |
| POST | `/api/tasks/{task_id}/run-rules` | 执行确定性规则 |
| POST | `/api/tasks/{task_id}/review-results` | 生成风险结果快照 |
| POST | `/api/tasks/{task_id}/write-comment` | Mock 写回审批评论并完成任务 |
| GET | `/api/tasks/{task_id}/logs` | 查询全过程日志 |

其余查询接口可在 FastAPI 自动文档中查看。

## 当前限制

- 只支持 PDF、DOCX、JPG、JPEG、PNG；不支持旧版 DOC 和压缩包。
- 字段/条款可由 LLM 辅助补缺，语义规则可辅助发现人工关注线索；两者都必须通过原文 block 证据校验。
- 默认 Demo 和测试不启用真实 Provider；正式应用只在环境变量完整时启用。
- OCR 同步运行，长文档可能耗时较长。
- 当前数据库基线面向 SQLite 验证；切换生产数据库前需单独验证方言和并发策略。
- 所有审批平台行为仍是 Mock，不应表述为已接入真实 OA。
