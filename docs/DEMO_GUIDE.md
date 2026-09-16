# 10 分钟演示指南

## 演示前准备

在项目根目录打开 PowerShell，确认 Python 3.12 与 `uv` 可用。本演示不配置真实 LLM，不上传真实合同，审批平台使用 Mock。

建议先放大终端字体，并提前打开：

- `docs/ARCHITECTURE_CHEATSHEET.md`
- `scripts/run_contract_demo.py`
- `app/services/contract_review_service.py`
- `app/services/comment_writeback_service.py`

## 0:00～0:45 项目解决什么问题

讲解：企业审批人需要在有限时间内检查合同附件、关键字段、条款和风险。本系统把重复工作自动化，但只提供风险提示，不替代人工审批。

强调边界：真实的 PDF/DOCX 读取、OCR、结构化提取、规则、持久化和状态流转已经实现；企业审批待办、附件下载和评论回写仍是 Mock。

## 0:45～1:30 展示系统架构

执行：

```powershell
Get-Content docs/ARCHITECTURE_CHEATSHEET.md
```

应该看到：从 `ApprovalGateway` 到 `CommentLog` 的完整数据链，以及状态、日志、版本复用等横切能力。

体现的设计：外部系统通过 Adapter 隔离；文档原文、业务解析、规则命中和最终结果职责分开，任何结果都能回溯到原始附件。

## 1:30～2:10 初始化正式数据库结构

执行：

```powershell
uv run alembic upgrade head
uv run alembic current
```

应该看到：数据库升级到最新 revision `8a7b6c5d4e3f`。

体现的设计：正式运行使用 Alembic 管理结构，不依赖应用启动时偷偷 `create_all()`；测试才显式使用快速建库。

## 2:10～2:35 Seed 默认规则

执行：

```powershell
uv run python -m scripts.seed_review_rules
uv run python -m scripts.seed_review_rules
```

应该看到：第一次补充缺失规则；第二次创建数量为 0，总数保持不变。

体现的设计：`rule_code` 唯一，seed 幂等，并且不覆盖人工调整后的规则配置。

## 2:35～4:00 运行完整 Demo

执行：

```powershell
uv run python -m scripts.run_contract_demo --reset
```

应该看到 8 个步骤：拉取审批、下载附件、读取合同、提取字段、规则审查、生成结果、写回评论、完成。最终摘要应包含：

```text
overall_risk_level: high
rule_hit_count: 3
external_comment_id: mock-comment-1
task_status: done
write_status: success
```

体现的设计：脚本没有直接伪造数据库结果，而是真正调用现有 Service。`--reset` 只能清理专用的 `demo_contract_assistant.db`。

## 4:00～4:35 展示 ApprovalTask

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); print(*e.connect().execute(text('select id,instance_id,approval_code,task_status,write_status,blocked_stage,retry_count from approval_tasks')).mappings(),sep='\n')"
```

应该看到：同一任务同时保存外部业务标识和内部 ID，最终为 `done / success`。

体现的设计：`instance_id` 与 `approval_code` 都有数据库唯一约束，重复拉取不会生成重复任务。

## 4:35～5:20 展示附件和 DocumentReadSnapshot

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); q='select a.id as attachment_id,a.original_file_name,a.file_type,a.sha256,a.download_status,d.id as read_id,d.read_method,d.read_status,d.page_count,d.reader_version from approval_attachments a join document_read_snapshots d on d.attachment_id=a.id'; print(*e.connect().execute(text(q)).mappings(),sep='\n')"
```

应该看到：附件 SHA-256、下载状态，以及读取方法、页数和 Reader 版本。

体现的设计：附件表示外部文件；读取快照表示某个文件版本经过某个 Reader 后得到的原始文本和位置。二者不能合并成一个对象。

## 5:20～6:05 展示 ContractParse

执行：

```powershell
uv run python -c "import json; from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); row=e.connect().execute(text('select id,document_read_snapshot_id,parse_status,extractor_name,extractor_version,basic_info_json from contract_parses')).mappings().one(); b=json.loads(row['basic_info_json']); print({'id':row['id'],'snapshot_id':row['document_read_snapshot_id'],'status':row['parse_status'],'extractor':row['extractor_name']+'@'+row['extractor_version'],'amount':b['amount']['value'],'party_a':b['signing_party']['value'],'party_b':b['counterparty']['value']})"
```

应该看到：金额、双方主体、解析状态及 Extractor 版本。

体现的设计：`ContractParse` 保存业务事实和证据，而 `DocumentReadSnapshot` 只保存原始文本；Extractor 升级可以新增历史结果而不覆盖旧记录。

## 6:05～7:00 展示 RuleHit 证据

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); q='select h.id,r.rule_code,r.risk_level,h.evidence_type,h.evidence_text,h.evidence_position,h.hit_message from rule_hits h join review_rules r on r.id=h.rule_id order by h.id'; print(*e.connect().execute(text(q)).mappings(),sep='\n')"
```

应该看到：命中的规则、风险等级、证据类型、原文和 block/page 位置。

体现的设计：风险不是一个无法解释的“高风险”标签；系统能够回答命中了什么规则、依据原文哪里、建议是什么。

## 7:00～7:45 展示 ReviewResult

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); q='select id,contract_parse_id,overall_risk_level,review_status,summary_text,rule_set_fingerprint,rule_hit_fingerprint from review_results'; print(*e.connect().execute(text(q)).mappings(),sep='\n')"
```

应该看到：整体风险、动态摘要、审查状态和两类 fingerprint。

体现的设计：最终风险只从合法 RuleHit 汇总；相同解析、规则集合、命中集合和汇总版本才能复用结果。

## 7:45～8:30 展示 Mock 评论回写

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); q='select id,task_id,review_result_id,write_status,external_comment_id,error_code,error_message from comment_logs order by id'; print(*e.connect().execute(text(q)).mappings(),sep='\n')"
```

应该看到：`write_status=success` 和 `external_comment_id=mock-comment-1`。

体现的设计：`ReviewResult` 保存评论正文，`CommentLog` 保存每次外部写入尝试。相同 ReviewResult 已成功写回时不会产生第二条评论。

## 8:30～9:10 展示最终状态和全过程日志

执行：

```powershell
uv run python -c "from sqlalchemy import create_engine,text; e=create_engine('sqlite:///demo_contract_assistant.db'); q='select task_status,write_status,blocked_stage,blocked_reason from approval_tasks'; print(*e.connect().execute(text(q)).mappings(),sep='\n'); q='select log_level,log_type,log_content from task_logs order by id'; print(*e.connect().execute(text(q)).mappings(),sep='\n')"
```

应该看到：最终 `task_status=done`、`write_status=success`，以及从任务创建到 `TASK_COMPLETED` 的日志。

体现的设计：只有评论明确写回成功，`CommentWritebackService` 才允许 `reviewing -> done`。

## 9:10～10:00 总结

用三句话结束：

1. 系统用不可变快照和版本号保证来源可追溯。
2. 能确定的风险由代码规则判断，LLM 只做补充，且必须通过 block 证据验证。
3. 外部审批仍是 Mock，系统输出只辅助人工审批，不替代法律判断。

## 故障演示一：OCR 失败与恢复

Mock OCR 故障开关是测试注入能力，没有暴露为生产管理 API。执行现有集成测试：

```powershell
uv run pytest tests/test_document_ocr.py::test_ocr_engine_exception_is_saved_and_blocks tests/test_document_ocr.py::test_blocked_ocr_resume_does_not_download_or_duplicate -vv
```

应该看到：2 个测试通过。两个测试实际验证：

```text
parsing
-> OCR_ENGINE_FAILED
-> blocked(blocked_stage=document_reading)
-> 保存 failed DocumentReadSnapshot + TaskLog
-> POST /api/tasks/{task_id}/ocr
-> parsing
-> 成功 OCR
```

设计重点：恢复直接使用现有 `ApprovalAttachment`，不会重新下载或创建重复任务/附件。

## 故障演示二：评论回写失败与 retry

执行：

```powershell
uv run pytest tests/test_comment_writeback.py::test_gateway_failure_blocks_and_keeps_failed_attempt tests/test_comment_writeback.py::test_comment_retry_only_reexecutes_writeback -vv
```

应该看到：2 个测试通过。两个测试实际验证：

```text
reviewing
-> write_status=failed
-> blocked(blocked_stage=comment_writeback)
-> 保存 failed CommentLog
-> POST /api/tasks/{task_id}/retry
-> reviewing
-> 只重新执行 CommentWritebackService
-> write_status=success
-> done
```

设计重点：retry 不重新下载附件、不重新 OCR、不重新提取字段、不重新运行规则；失败和成功两次 CommentLog 都保留。
