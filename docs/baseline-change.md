
| 项    | 值                                                    |
| ---- | ---------------------------------------------------- |
| 负责人  | 李欣媛（F·需求确认员）                                         |
| 分支   | `feature/F-baseline-mgr`                             |
| 状态   | 设计稿 v0.1（待步骤 3 设计评审会对齐后升 v1.0）                       |
| 对应代码 | `src/baseline_mgr/`                                  |
| 上游依据 | `第10组-多智能体需求生成工作流.docx` + `architecture.md`（A 的编排契约） |
| 下游依赖 | 无（直接对接 A 的调度引擎与 HumanGate）                           |

---
## 1. 本模块管什么

**一句话**：把机器折腾出来的需求规格和评审结论，变成“人愿意签字”的基线；如果人不满意，把打回意见精准转化为约束条件送回流水线；基线定下后，管好所有变更。

|内容|
|---|---|
|输入|E 生成的 `requirement_spec`、`coverage_matrix`，C 生成的 `solution_comparison`，D 生成的 `review_sheet`，以及人的 `HumanDecision`|
|输出|`confirmation_sheet`（确认单）、`opinion_handling`（意见处理表）、`baseline`（基线文件）、`change_request`（变更请求）|
|下游|A（读取状态决定流转）、人（阅读确认单并签署）|
|不负责|不写需求（E 的活）、不评审方案（D 的活）、不做流程调度（A 的活）|

**边界要说清的一点**：本模块不产生“新的业务结论”，只负责“把已有的结论无损、清晰地呈现给人”，并“把人的决策无损地翻译成机器能懂的指令”。

---

## 2. 数据结构（F 模块专属）

> 以下所有产物在落盘时，外层必须包裹 `architecture.md` §0.4 定义的 `Artifact`最小合法 JSON 壳，包含 `meta` 字段。

### 2.1 `confirmation_sheet.json`（人工确认单）

供 P7 阶段人类阅读的最终确认页面数据源，包含三件套。

|字段|类型|必填|说明|
|---|---|---|---|
|`summary`|object|是|需求摘要统计：`{total_reqs, must_have, should_have, could_have}`|
|`requirements`|list|是|直接引用 `requirement_spec.data.requirements`的全量数据|
|`review_opinions`|list|是|直接引用 `review_sheet.data.challenges`（仅展示被采纳/未解决的质疑）|
|`coverage_status`|object|是|包含 `{coverage_rate, gap_count}`，强制展示给人类看“没覆盖到的坑”|
|`signature_block`|object|是|待签署区，包含 `{required: true, hint: "请输入签字理由"}`|

### 2.2 `opinion_handling.json`（意见处理表）

记录 P7 阶段人类给出的每一条意见（comment）是怎么处理的，是 A 判定下一步动作的依据。

|字段|类型|必填|说明|
|---|---|---|---|
|`opinion_id`|string|是|`OPN-001`|
|`source_ref`|string|是|来源引用，格式 `confirmation_sheet:<field>`，如 `confirmation_sheet:requirements[REQ-001]`|
|`comment`|string|是|人类给出的原始意见文本|
|`decision`|enum|是|`adopt`（采纳，需改需求） / `reject`（驳回，无需改） / `pending`（挂起）|
|`action_taken`|string|否|采纳时说明修改动作，如“打回 P6 重写”或“新增约束返回 P3”|
|`handled_by`|string|是|固定为 `A6`（需求确认员）或 `human`|

### 2.3 `baseline.json`（基线文件）

> 遵循 A 的 §0.5.5 定义，此处补充详细结构。

|字段|类型|必填|说明|
|---|---|---|---|
|`version`|string|是|基线版本号，`v1.0`（首次冻结）或 `v1.1`（变更后）|
|`status`|enum|是|`draft` / `frozen`|
|`requirement_ids`|list|是|包含的 `req_id` 列表，顺序与 `requirement_spec` 一致|
|`signed_by`|string|是|签字人（通常为 `user`）|
|`signed_at`|string|是|签字时间（ISO 8601）|
|`source_refs`|list|是|追溯链：`requirement_spec:vX.X`, `confirmation_sheet:HD-XXX`|

### 2.4 `change_request.json`（变更请求）

P7 冻结后的 CR 流程记录。

|字段|类型|必填|说明|
|---|---|---|---|
|`cr_id`|string|是|`CR-001`|
|`from_version`|string|是|发起变更时的基线版本|
|`reason`|string|是|变更原因|
|`impact_analysis`|object|是|影响分析 `{affected_reqs: [], new_reqs: [], estimated_effort_hours: int}`|
|`review_result`|enum|是|`approved` / `rejected`|
|`to_version`|string|是|变更后的新版本号（若 approved），否则为 `null`|

---

## 3. 核心流程与规则

### 3.1 P7 人工确认流转（结合 A 的状态机）

1. **接收上游数据**：A 调度 F 的 `build_confirmation_sheet(ctx)`，F 从 `ctx.artifacts` 中提取 E、C、D 的产物，拼装出 `confirmation_sheet.json`。
    
2. **阻断等待人**：A 将 `RunState.phase` 设为 `HUMAN_GATE`，状态挂起，等待 CLI 传入 `HumanDecision`。
    
3. **接收人的动作**（A 接收，F 处理）：
    
    - **Approve / Sign**：调用 F 的 `freeze_baseline()`。F 校验 `requirement_spec`所有条目都有验收标准，生成 `baseline.json` (v1.0, `frozen=true`)，附带签字人。
        
    - **Reject / Comment**：调用 F 的 `apply_human_decision()`。F 生成 `opinion_handling` 记录，并调用 `build_rework_order()` 打包打回约束。
        
    - **Abort**：返回人工中止。
        

### 3.2 基线冻结规则（`freeze_baseline`）

|规则|说明|
|---|---|
|前置校验|必须能在 `ctx.artifacts` 中读取到完整的 `requirement_spec`，且 `coverage_matrix.gap_count == 0`。**若有人工特批跳过，须在签名理由中注明。**|
|版本号锁定|首次冻结固定为 `v1.0`；后续由 CR 流程产生 `v1.1`、`v1.2` 等。|
|不可变性|冻结后的 `baseline.json` 不可物理修改，任何变更只能通过生成新的 `change_request` 并评审通过后替换引用。|

### 3.3 变更请求（CR）流程（基线后）

text

人发起变更 → F 生成 CR-001 (from_version: v1.0) 
→ F 进行影响分析（扫描 source_refs 和 coverage_matrix） 
→ 评审（人工或机器） 
→ approved：走一遍 P4/P5/P6 生成增量需求 → 冻结 v1.1 
→ rejected：保持 v1.0，CR 记录归档

|规则|说明|
|---|---|
|防扩散|变更请求必须明确列出受影响的 `REQ-xxx`，F 计算波及范围，不许“无边界新增”。|
|一致性|版本升级后，`coverage_matrix` 必须重新计算通过，才能冻结新版本。|

---

## 4. 失败处理与异常兜底

|情况|处理|
|---|---|
|P7 人始终不操作（超时）|不自动推进，保持 `HUMAN_GATE`。若系统配置超时策略，标记为 `escalated` 并通知管理员。|
|人点了批准，但 `requirement_spec` 里仍有缺 `acceptance_criteria` 的条目|拒绝签署，抛出 `E_SCHEMA_INVALID`，通知 A 打回 P6 补齐。|
|人的意见（comment）语义模糊，无法映射到具体 `REQ`|挂起为 `pending`，需 A6 与人类二次确认，不得强行 `adopt`。|
|`change_request` 导致原基线出现孤儿需求（无源头）|阻断变更，要求追加 `source_refs` 或人工裁决。|

---

## 5. 接口与依赖（与 A 的约定）

F 模块必须实现 `architecture.md` §8.2 规定的统一签名：

python

def build_confirmation_sheet(ctx: StageContext) -> dict: ...
def apply_human_decision(decision: dict) -> dict: ...
def freeze_baseline(ctx: StageContext) -> dict: ...
def create_change_request(cr: dict) -> dict: ...

**与 A（调度器）的交互**：

- A 负责把人的 `HumanDecision` 指令传给 F，并调用 F 的方法；
    
- F 不直接操作 `RunState`，只返回处理后的产物（带 meta），由 A 统一落盘和状态转移。
    

**与 E（需求）的交互**：

- F 极度依赖 E 的 `requirement_spec` 和 `coverage_matrix`。如果 E 的产物质量差（如需求含糊），F 生成的确认单也会很难看，导致人类频繁打回。所以 F 的质量取决于 E。
    

---

## 6. 测试用例设计清单（供步骤 4 编码使用）

按照 F 的职责，整体测试用例需要覆盖以下几类：

|用例类型|测试场景|预期结果|
|---|---|---|
|**正常流**|P6 产出正常，人类在 P7 点击 Sign 并输入合法理由。|成功生成 `baseline.json`v1.0，`RunState.status` 变为 `frozen`，流程结束。|
|**打回流**|人类在 P7 对 REQ-001 提出意见并点击 Reject。|F 生成 `opinion_handling`(decision=adopt)，A 收到 `ReworkOrder` 并广播约束，流程退回 P3/P6。|
|**异常流-缺验收标准**|模拟 E 漏写了某需求的三段式验收标准，人类试图签字。|`freeze_baseline` 拦截，提示存在无效需求，不予冻结。|
|**异常流-意见不明**|人类意见为“再优化一下”，未指明具体 REQ。|系统提示无法解析，意见进入 `pending`，要求重新输入。|
|**变更流-成功**|v1.0 冻结后，人发起 CR-001 要求新增功能。|生成 CR 记录，影响分析列出新增 2 个需求，走完 P4-P6 后，成功冻结 v1.1。|
|**变更流-冲突**|CR 修改了某需求，导致原追溯链断裂。|系统拒绝冻结 v1.1，拦截并报告断裂的 `source_refs`。|

---

## 7. 答辩材料整理要点（负责整理）

> 答辩时，除了 PPT，F 需要负责把整个项目的“骨架”串起来，因为 F 管最终产出和整体测试。

1. **项目全流程展示**：以一张“需求基线 v1.0 的诞生”为例，展示从 P1 的一句话，经过 A、B、C、D、E，最后到 F 出具的 `confirmation_sheet` 与人类签名。
    
2. **质量保证（测试）演示**：展示失败兜底——比如“如果模型瞎编需求且缺少验收标准，系统是如何在闸门层（A 的 `G_P6_P7` 和 F 的 `freeze_baseline`）拦截它的”。
    
3. **人机协同演示**：演示人在 P7 打回，带约束回炉，最后二次确认通过的完整闭环。证明系统不是“无人值守的玩具”，而是“人机协同的工具”。