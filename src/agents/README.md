# B 模块 角色与讨论管理

本模块按修订版方案实现角色、P2 召集、P3 独立陈述/定向质询/投票、防趋同及用户价值建议。正式设计文档是仓库的 `docs/role-discussion.md`。

在“设计规格 → 任务分解 → 编排 → 可视化”的整体流程中，B 负责组织有依据的讨论，为任务分解提供结构化输入。已补齐 A 的统一模型网关适配和原生阶段信封，并完成真实模型讨论验证；六模块端到端效果仍需另外验收。

## 功能与职责

| 功能 | 说明 |
|---|---|
| 角色与提示词 | 配置六个智能体的目标、立场约束、知识边界和上场规则 |
| P2 召集 | 按功能点分类和置信度召集角色，确保关键角色在场 |
| P3 讨论 | 独立陈述、定向质询、带理由投票，保留结构化证据链 |
| 防趋同 | 代码计算相似度，触发补发言，记录检测降级和挂起信号 |
| 用户价值 | 根据明确的规格标记提供 M/S/C/W 建议，不猜测缺失依据 |
| 异常处理 | 校验越界引用、无效输出、超时、预算和步骤顺序 |

B 提供讨论机制与记录；A 负责调度、全局预算与排期，C 生成分解方案，D 登记和裁决冲突，E 整理任务与可视化，F 负责确认和基线。

## 角色设置

| 编号 | 角色 | P3 参与方式 |
|---|---|---|
| A1 | 产品经理 | 陈述、质询、投票；P5 回避 |
| A2 | 用户代表 | 陈述、质询、投票 |
| A3 | 技术架构师 | 陈述、质询、投票 |
| A4 | 质量评审员 | 陈述、质询、投票 |
| A5 | 需求分析师 | 处理型，不持立场、不投票 |
| A6 | 需求确认员 | 处理型，不持立场、不投票 |

编排器单列，不占业务智能体编号。小组成员 A–F 与运行时智能体 A1–A6 是两套编号。

## 目录结构

```text
src/agents/
├── README.md                  # 本说明
├── __init__.py                # 公共接口
├── __main__.py                # 演示与单步调用入口
├── core.py                    # 召集、讨论、防趋同、价值建议
├── providers.py               # HTTP 模型网关适配
├── agent_config.yaml          # 角色、知识边界、议题和阈值
├── prompt_templates/          # 通用协议与六角色模板
├── examples/
│   ├── fixture.py             # 明确标记的离线脚本和输入
│   └── expected/              # 固定输入与输出样例
└── tests/
    └── test_agents.py         # 自动验收
```

所有 B 配置、模板、代码、样例和测试均在 `src/agents/` 内。需要 Python 3.10+。单独运行离线脚本不需要第三方依赖；调用 A 的模型网关、集成测试及真实调用入口需要安装 A 使用的 PyYAML，见 requirements.txt。

## 接入真实火山方舟模型

调用链为 `B 模型请求 → SchedulerGatewayModel → A 的 LLMGateway.call → ChatCompletionsProvider → 火山方舟 API`。没有配置时明确报错，不回退成脚本结果。模型 ID 使用当前账号可调用的值，不在代码中硬编码。

从仓库根目录创建环境并安装依赖：

```powershell
python -m venv .venv
& './.venv/Scripts/python.exe' -m pip install -r src/agents/requirements.txt
```

在本机配置密钥（隐藏输入，不写入仓库）：

```powershell
& './.venv/Scripts/python.exe' -m src.agents.configure --model '你的模型ID'
```

Windows 配置器把 ARK_API_KEY 与 ARK_MODEL 保存到当前用户环境变量；密钥不会出现在命令参数或输出中。无需运行 PowerShell 脚本或修改系统执行策略。其他系统可自行设置 ARK_API_KEY 和 ARK_MODEL。可选 ARK_BASE_URL 默认是 `https://ark.cn-beijing.volces.com/api/v3`。

非方舟的 Chat Completions 服务可使用 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL。存在方舟配置时优先使用方舟配置。旧的 B_MODEL_ENDPOINT 自定义网关仍可供底层 run-step 使用；A 原生阶段入口使用统一 LLMGateway。

完整真实讨论（会将指定样例材料发送到所配置的模型服务并消耗额度）：

```powershell
& './.venv/Scripts/python.exe' -m src.agents live --output runs/b-live
```

默认输入是仓库的校园二手交易虚构样例；可用 `--input 你的上下文.json` 替换。live 的角色回答来自真实模型，D 的登记和裁决由本地代码执行。输出 stage_result.json、discussion_minutes.json、guard_signals.json、conflict_register.json、gateway_usage.json 和可阅读的运行记录.md；输入缺失或讨论挂起时保留已有结果，明确失败，不宣称成功。

连接成功不等于所有模型输出都满足协议。正式运行可能触发 JSON 修正、越界证据拒绝、趋同补发言、预算耗尽或人工处理。

官方接口依据：[Base URL 与鉴权](https://docs.volcengine.com/docs/ark/base-url-and-authentication?lang=zh)、[结构化 JSON 输出](https://docs.volcengine.com/docs/ark/structured-output-beta?lang=zh)。远程请求使用 HTTPS；不将响应里的推理内容替代最终 JSON 回答。

## 与 A 原生调度器集成

`src.agents.run(ctx)` 接受 A 的 StageContext 并返回 A 的 StageResult；`src/scheduler/agents_bridge.py` 负责组合 B 与 D 的调用。`modules.resolve("agents", "real")` 指向该组合入口，mock 模式保持离线。

P2 读取 idea_card.data.design_spec，用真实模型做一次分类，生成符合 A 闸门的 problem_list 与 convocation_table。P3 按“B 独立陈述 → D 登记 → B 质询 → B 投票 → D 裁决”的顺序执行，A 继续负责阶段状态、落盘与计费。B 不自行导入其他业务模块。

正常一轮上限 12 次：4 次陈述、最多 4 次回答、4 次投票。无对立观点时不虚构冲突或问答。计数通过 A 的线程安全网关记录，返回预算由 A 统一 charge；模型超时不计本次调用。

P3 的私有材料通过 convocation_table.extensions.b_context 随阶段快照传递。正式规格需要提供 `role_materials` 数组，每条遵循下文 material_catalog 的结构。B 不把缺失用户访谈、架构资料或风险依据编造为事实；缺独占材料时阻断，要求上游补充。默认 live 样例已经显式包含所需的虚构材料。

本次对 A 的小范围适配包括：真实模块注册组合入口、网关并发计数与超时口径，以及将 blocked/need_human 的已有产物与费用先保存后进入人工闸门。这些变更应随 B 接入一起提交，不能只复制 providers.py。

## 运行

从仓库根目录执行：

```powershell
python -m unittest discover -s src/agents/tests -v
python -m src.agents demo --output runs/b-demo
```

离线演示使用固定脚本，不联网。结果为四条独立陈述、四对定向问答、四张带理由的票，合计十二次模拟调用。输出文件在 `runs/b-demo/`，不提交。已保存的固定样例在 `src/agents/examples/expected/`。

演示后优先查看：

| 输出文件 | 查看内容 |
|---|---|
| `演示记录.md` | 可阅读的陈述、问答与投票 |
| `convocation_result.json` | P2 召集表和角色配置快照 |
| `discussion_minutes.json` | 本轮完整结构化讨论纪要 |
| `statement_result.json` | 相似度报告和防趋同信号 |
| `user_value_result.json` | 任务用户价值与优先级建议 |
| `events.jsonl` | 演示宿主分配序号后的事件 |

这只是 B 的独立验收，不代表真实模型和其他五模块已联调完成。示例中的分类、冲突登记和任务清单都是明确标记的夹具。

## 对接入口

```python
from src.agents import build_convocation, render_prompt, run_discussion
from src.agents import estimate_similarity, evaluate_user_value
```

| 接口 | 输入/输出 |
|---|---|
| build_convocation(ctx) | P2，输入 design_spec 和 classification，返回召集表与角色卡 |
| render_prompt(agent_id, protocol_step, ctx) | 隔离材料后返回模型请求；只用于 P3 的 A1–A4 |
| run_discussion(ctx, model=None, embedder=None) | P3 单议题单步骤，返回纪要、信号、事件和 usage |
| estimate_similarity(a, b, embedder=None) | 字面/语义分数、flagged、degraded |
| evaluate_user_value(ctx) | P6，输入设计规格和任务清单，只返回优先级建议 |

模块函数不写 runs、不改上下文、不决定下一阶段、不调用其他模块。输出事件不含全局 seq，由 A 负责分配、发布和落盘。全局预算和阶段余额由 A 管理。

## StageContext 和 StageResult

输入对象字段：run_id、stage、budget_left、artifacts、config。每个 artifact 是 `{"meta": {...}, "data": {...}}`；meta 至少含 schema_version=1.0、run_id，产物还记录 stage、producer、artifact_name。

底层独立接口的返回值为 `{"artifacts": {...}, "events": [...], "usage": 调用次数}`。原生 run(ctx) 和 A 的组合入口已适配为 StageResult：使用 budget_used、RunEvent、GuardSignal，以及 produced_by/created_at/顶层 extensions 产物信封。A 的 validate_stage_result 集成测试已通过。

完整上下文见本模块 examples/expected/input_context.json，P2/P6 的对应输入也在同目录。

P3 config 必填：

```json
{
  "protocol_step": "statement",
  "round": 1,
  "issue": {"issue_id": "ISS-001", "topic_id": "granularity", "description": "需要判断的同一命题"}
}
```

步骤依次为 statement、query、vote。议题类型为 granularity、dependency、estimate、acceptance。轮数 1–3，A 负责开新轮及归档旧轮；同一轮不能重复提交同一步。config.max_calls 可限制本步骤；budget_left 必须是全局与 P3 剩余额度的较小值。

## P2 召集

classification.data.items 每行包含 source_ref（例如 design_spec:FEA-001）、categories、confidence，可选 owner_role/responsibility。类别为 functional/technical/cost/risk，可多选。每个功能点必须有分类。

A3/A4 必须参加，A1 组织模块责任；默认置信度低于 0.75 时全员 A1–A6 召集。0.75 是本实现配置值。A5/A6 以处理身份辅助，不获得 P3 投票权。

## 材料与讨论

材料位于 material_catalog.data.items，每条包含 source_ref、category、text、source_refs。例如：

```json
{"source_ref":"material_catalog:MAT-003","category":"user_feedback","text":"明确给定的用户反馈","source_refs":["design_spec:FEA-002"]}
```

上游必须明确材料类别和来源。来源须对应当前 design_spec 的 SPEC/FEA/CON/MOD；各角色至少有可见和独占证据。不能把同一材料换标签冒充信息差。

先由 B 完成 statement，再由 D 登记冲突，A 将 conflict_register 与 discussion_minutes 传回 B 执行 query。D 登记的相关行应含：

```json
{
  "conflict_id":"CFL-001",
  "topic_id":"granularity",
  "stances":[
    {"source_ref":"discussion_minutes:ST-ISS-001-1-A1"},
    {"source_ref":"discussion_minutes:ST-ISS-001-1-A3"}
  ],
  "level":"L1_evidence",
  "status":"open",
  "extensions":{"issue_id":"ISS-001"}
}
```

登记行放在 conflict_register.data.items 中，并包 meta 信封。质询必须针对 FOR/AGAINST 对立主张；问题由 B 的主持模板引用原主张，目标角色模型回答。每步每个角色最多回答一次。D 审查回答是否实质回避，需要追加讨论由 A 开新轮。

query 完成后把最新 discussion_minutes 传给 vote。每一步从 budget_left 扣除返回 usage，再调下一步，不能每次都重置成 12。正常完整一轮为 12 次调用，重试也占预算。

B 仅记录 FOR/AGAINST/ABSTAIN 原始票型，无理由弃权同样无效；不执行 D 的加权裁决。有效票少于 3 张挂起，平票交 D。G_P3_P4/G_P3_HUMAN 由 A/D 判断。

## 真实模型与防趋同

推荐注入 `model(request, timeout=120) -> dict`，回调必须线程安全、落实网络超时。请求含 agent_id/step/system/user；只传 render_prompt 的隔离结果，不能传完整上下文。响应格式在 prompt_templates/common.md。

可选注入 `embedder([文本1, 文本2]) -> [向量1, 向量2]`。语义 cosine >=0.85 或字面 bigram Jaccard >=0.70 时补发言；最多补两次。补发言须引用独占证据，且相对原文语义相似度 <0.6。

无 embedding 或服务失败时执行字面检测，语义分记 null 并公开 lexical_degraded；降级补发言按字面 <0.6 检查，不能称为语义已通过。向量服务若收费，应由 A 另外计入资源费用。

未注入模型时，底层入口优先使用已设置的 B_MODEL_ENDPOINT 自定义网关，否则读取方舟/LLM 配置并复用 A 网关。旧自定义网关接受请求 JSON，返回 `{"response": {...}}`；真实方舟入口按官方 Chat Completions 协议调用。

```powershell
python -m src.agents run-step --input src/agents/examples/expected/input_context.json --output runs/live/statement_result.json
```

没有模型配置会明确报错，不会偷换成模拟结果。真实模型质量、语义检测效果仍需配置后验证。

## 用户价值建议

evaluate_user_value 输入 design_spec 与 dev_task_list。任务沿用全组 task_id/module_id/depends_on/estimate_days/owner_role/acceptance/priority/source_refs；B 只读取必要字段。

明确的 FEA/CON 标记：out_of_scope → W；mandatory/core_journey → M；user_value=high → S；user_value=low → C；信息不足 → null 并要求确认。这些字段若在 A 契约中位于 extensions，入口映射一次即可。不得让模型补造用户价值。

建议 binding=false，不改任务优先级或时间表。技术前置任务可通过强制约束获得 M。超过三人天或缺验收只作提醒，D/E 执行正式校验。

## 验收与提交范围

已通过 37 项 B 与模型接入测试：原有 24 项角色与协议测试，以及 13 项模型 HTTP 协议、敏感信息不进入错误、A 网关并发计数、原生信封、P2→P3 快照、B/D 组合调用和挂起计费等集成测试。A 调度器自检 16/16 通过。

2026 年 11 月 16 日真实验证：使用账号提供的模型 ID kimi-k3-1-pro-261024，经火山方舟 API 返回有效结果。校园二手交易样例产出 4 条有效独立陈述与 4 张有效票，合计 8 次调用；四人均为 FOR，没有对立立场，因此质询环节按规则跳过，QA 为 0。记录在本机 runs/b-live-verified，不含密钥且不提交版本库。对立观点的 4 对 QA 和 D 裁决分支由本地集成测试覆盖。

首次真实测试曾因提示词同时列出三个步骤格式而返回嵌套对象，校验拒绝并挂起；已改为当前步骤唯一扁平 output_schema，并将模板/配置版本更新为 1.0.1。保留该限制能避免“接口返回了内容”被误认为“讨论结果有效”。

embedding 尚未接入，仍明确按字面检测降级。C/E 对讨论结果的消费及全系统 P1–P7 运行不由 B 的局部验收代替。

向仓库提交 src/agents/ 的代码、配置、模板、examples、tests 和本说明，以及 docs/role-discussion.md。不要提交 __pycache__、*.pyc、runs、密钥。独立输出样例不证明六模块已经联调。
