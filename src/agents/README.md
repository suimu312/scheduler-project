# B 模块使用与联调说明

本模块按修订版方案实现角色、P2 召集、P3 独立陈述/定向质询/投票、防趋同及用户价值建议。正式设计文档是仓库的 `docs/role-discussion.md`。

所有 B 配置、模板、代码、样例和测试均在 `src/agents/` 内。需要 Python 3.10+；默认配置使用合法 YAML 1.2 的 JSON 语法，不需安装依赖。普通 YAML 可选使用 PyYAML。

## 运行

从仓库根目录执行：

```powershell
python -m unittest discover -s src/agents/tests -v
python -m src.agents demo --output runs/b-demo
```

离线演示使用固定脚本，不联网。结果为四条独立陈述、四对定向问答、四张带理由的票，合计十二次模拟调用。输出文件在 `runs/b-demo/`，不提交。已保存的固定样例在 `src/agents/examples/expected/`。

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

返回值为 `{"artifacts": {...}, "events": [...], "usage": 调用次数}`。由于当前尚未接入 A 的正式信封 schema，A 应按修订版在其统一 schema 中登记或适配，不要建立第二套全局契约。

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

未注入模型时，可设置 B_MODEL_ENDPOINT 与可选 B_MODEL_TOKEN。HTTP 网关接受请求 JSON，返回 `{"response": {...}}`；远程必须 HTTPS。本协议需要网关转译，不是任意供应商原生接口都能直接兼容。

```powershell
python -m src.agents run-step --input src/agents/examples/expected/input_context.json --output runs/live/statement_result.json
```

没有模型配置会明确报错，不会偷换成模拟结果。真实模型质量、语义检测效果仍需配置后验证。

## 用户价值建议

evaluate_user_value 输入 design_spec 与 dev_task_list。任务沿用全组 task_id/module_id/depends_on/estimate_days/owner_role/acceptance/priority/source_refs；B 只读取必要字段。

明确的 FEA/CON 标记：out_of_scope → W；mandatory/core_journey → M；user_value=high → S；user_value=low → C；信息不足 → null 并要求确认。这些字段若在 A 契约中位于 extensions，入口映射一次即可。不得让模型补造用户价值。

建议 binding=false，不改任务优先级或时间表。技术前置任务可通过强制约束获得 M。超过三人天或缺验收只作提醒，D/E 执行正式校验。

## 验收与提交范围

已通过 24 项自动测试：角色配置、知识边界、材料追溯、独立陈述、召集、步骤顺序、D 登记、预算、超时、格式修正、无理由弃权、平票、重复发言、embedding 降级、配置指纹和用户价值等。

向仓库提交 src/agents/ 的代码、配置、模板、examples、tests 和本说明，以及 docs/role-discussion.md。不要提交 __pycache__、*.pyc、runs、密钥。独立输出样例不证明六模块已经联调。
