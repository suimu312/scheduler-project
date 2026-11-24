# 确认与基线模块设计

| 项    | 值                                                                 |
| ---- | ----------------------------------------------------------------- |
| 负责人  | 李欣媛（F·需求确认员）                                                      |
| 分支   | `feature/F-baseline-mgr`                                          |
| 状态   | 设计稿 v0.2（对齐《设计方案（修订版V2）》）                                         |
| 对应代码 | `src/baseline_mgr/`                                               |
| 上游依据 | `第10组-多智能体任务分解与编排系统设计方案（修订版V2）.docx` + `architecture.md`（A 的编排契约） |
| 下游依赖 | 无（直接对接 A 的调度引擎与 HumanGate）                                        |

---

## 0. 本次修订说明
新版方案把 P7 定义为“**排期、确认与冻结**”，因此 F 的职责从“需求规格确认”迁移为“**开发任务清单与排期表的确认、基线冻结与变更管理**”。主要改动：

1. 上游输入由 `requirement_spec / coverage_matrix / solution_comparison / review_sheet` 改为新版产物：`dev_task_list`、`task_coverage`、`schedule`、`review_sheet`、`conflict_register`。
2. 确认单内容由“需求条目”改为“**任务卡 + 排期 + 覆盖矩阵 + 评审意见**”四件套并列呈现。
3. 基线冻结规则对齐新版 §2.4 的 `baseline.json` 最小必填集（`task_ids[]`、`signed_by`、`signed_at`、`source_refs[]`）。
4. 补齐图片中指出的“**签字之后的基线管理、变更流程**”：新增 CR 模块的真实处理逻辑与状态流转，而不只是数据结构占位。
5. 接口清单对齐新版 §2.8 表 10：F 对外至少暴露 `build_confirmation(ctx)` 与 `freeze_baseline(ctx)`；本设计在此基础上补充 `apply_human_decision` 与 `create_change_request` 两个内部/扩展接口，仍遵守“模块只返回 StageResult、不写 runs、不改状态”的 C6 硬规则。

---

## 1. 本模块管什么

**一句话**：把编排器算出的 WBS、排期、覆盖矩阵与评审结论，变成“人愿意签字”的基线；如果人不满意，把打回意见精准转化为约束送回 P3/P6；基线定下后，管好所有变更。

| 内容 |
|---|
| 输入 | `dev_task_list`（WBS）、`task_coverage`（覆盖矩阵）、`schedule`（波次与关键路径）、`review_sheet`（评审结论）、`conflict_register`（未决/升级冲突）、人的 `HumanDecision` |
| 输出 | `confirmation_sheet`（人工确认单）、`opinion_handling`（意见处理表）、`baseline`（基线文件）、`change_request`（变更请求） |
| 下游 | A（读取状态决定流转）、人（阅读确认单并签署） |
| 不负责 | 不写任务卡（P6/A1+E 的活）、不排期（A 的纯代码排期）、不评审方案（D 的活）、不做流程调度（A 的活） |

**边界要说清的一点**：本模块不产生“新的业务结论”，只负责“把已有的结论无损、清晰地呈现给人”，并“把人的决策无损地翻译成机器能懂的指令”。尤其**排期不是模型写的，是代码算的**（见新版 §5.3），F 只负责把排期结果呈现与冻结，不重新计算关键路径。

---

## 2. 数据结构（F 模块专属）

> 所有产物落盘时，外层必须包裹 `architecture.md` §0.4 定义的 `Artifact` 最小合法 JSON 壳，包含 `meta`（`schema_version`、`run_id`、`stage`、`produced_by`、`created_at`）。缺 meta 由编排器拒收（C5）。

### 2.1 `confirmation_sheet.json`（人工确认单）

供 P7 阶段人类阅读的最终确认页面数据源。新版要求把“任务清单、排期表、覆盖矩阵与意见处理表并列呈现”（§3.7），因此确认单改为四件套 + 签署区。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `summary` | object | 是 | 摘要统计：`{total_tasks, must_tasks, should_tasks, could_tasks, total_waves, total_days}` |
| `tasks` | list | 是 | 直接引用 `dev_task_list.data.tasks` 的全量数据（含 `task_id`、`module_id`、`depends_on[]`、`estimate_days`、`owner_role`、`acceptance[]`、`priority`、`source_refs[]`） |
| `schedule` | object | 是 | 直接引用 `schedule.data`：`{waves[], critical_path[], total_days}` |
| `coverage_status` | object | 是 | 引用 `task_coverage.data`：`{coverage_rate, gap_count}`，强制展示“没覆盖到的坑” |
| `review_opinions` | list | 是 | 引用 `review_sheet.data.challenges`（仅展示被采纳/未解决的质疑） |
| `signature_block` | object | 是 | 待签署区：`{required: true, hint: "请输入签字理由"}` |

### 2.2 `opinion_handling.json`（意见处理表）

记录 P7 阶段人类给出的每一条意见是怎么处理的，是 A 判定下一步动作的依据。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `opinion_id` | string | 是 | `OPN-001` |
| `source_ref` | string | 是 | 格式 `confirmation_sheet:<field>`，如 `confirmation_sheet:tasks[DEV-001]` |
| `comment` | string | 是 | 人类给出的原始意见文本 |
| `decision` | enum | 是 | `adopt`（采纳，需改任务/排期）/ `reject`（驳回，无需改）/ `pending`（挂起） |
| `action_taken` | string | 否 | 采纳时说明动作，如“打回 P6 重拆任务卡”或“退回 P3 补质询” |
| `handled_by` | string | 是 | 固定为 `A6`（需求确认员）或 `human` |

### 2.3 `baseline.json`（基线文件）

对齐新版 §2.4 表 7 的最小必填集，并补充追溯链。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `version` | string | 是 | `v1.0`（首次冻结）或 `v1.1`（变更后） |
| `status` | enum | 是 | `draft` / `frozen` |
| `task_ids` | list | 是 | 包含的 `DEV-xxx` 列表，顺序与 `dev_task_list` 一致 |
| `signed_by` | string | 是 | 签字人（通常为 `user`） |
| `signed_at` | string | 是 | 签字时间（ISO 8601） |
| `source_refs` | list | 是 | 追溯链：`dev_task_list:vX.X`, `schedule:vX.X`, `confirmation_sheet:HD-XXX` |

### 2.4 `change_request.json`（变更请求）

P7 冻结后的 CR 流程记录。这是图片中“签字之后的基线管理、变更流程”的落地载体。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cr_id` | string | 是 | `CR-001` |
| `from_version` | string | 是 | 发起变更时的基线版本 |
| `reason` | string | 是 | 变更原因 |
| `impact_analysis` | object | 是 | 影响分析：`{affected_tasks: [], new_tasks: [], estimated_effort_hours: int}` |
| `review_result` | enum | 是 | `approved` / `rejected` |
| `to_version` | string | 是 | 变更后的新版本号（若 approved），否则为 `null` |

---

## 3. 核心流程与规则

### 3.1 P7 人工确认流转（结合 A 的状态机）

1. **接收上游数据**：A 调度 F 的 `build_confirmation_sheet(ctx)`，F 从 `ctx.artifacts` 中提取 `dev_task_list`、`schedule`、`task_coverage`、`review_sheet`，拼装出 `confirmation_sheet.json`。
2. **阻断等待人**：A 将 `RunState.phase` 设为 `HUMAN_GATE`，状态挂起为 `waiting_human`，等待 CLI 传入 `HumanDecision`。
3. **接收人的动作**（A 接收，F 处理）：
   - **Approve / Sign**：调用 F 的 `freeze_baseline()`。F 校验所有任务卡都有验收口径、依赖无环、覆盖矩阵无空缺，生成 `baseline.json`（v1.0，`status=frozen`），附带签字人与签字理由。
   - **Reject / Comment**：调用 F 的 `apply_human_decision()`。F 生成 `opinion_handling` 记录，并调用 `build_rework_order()` 打包打回约束。按新版 §3.7，“打回不等于从头重来”：打回时携带原因、已有方案与已有意见回到 **P3**，原因作为新约束广播给全部角色，后续每个角色回应时必须先回应它；同一份设计规格最多打回两次，第三次转升级态交人。
   - **Abort**：返回人工中止。

### 3.2 基线冻结规则（`freeze_baseline`）

| 规则 | 说明 |
|---|---|
| 前置校验 | 必须能在 `ctx.artifacts` 中读取到完整的 `dev_task_list`，且 `task_coverage.gap_count == 0`；每张卡至少一条可观测验收口径（C3）；`depends_on` 无环（C2）。**若有人工特批跳过，须在签名理由中注明。** |
| 版本号锁定 | 首次冻结固定为 `v1.0`；后续由 CR 流程产生 `v1.1`、`v1.2` 等。 |
| 不可变性 | 冻结后的 `baseline.json` 不可物理修改；任何变更只能通过生成新的 `change_request` 并评审通过后替换引用。 |
| 事件记录 | 冻结成功与失败都必须由 A 追加 `human.decision` 事件（新版 §2.7 事件契约），F 只返回 StageResult，不自行写事件流。 |

### 3.3 变更请求（CR）流程（基线后）

```text
人发起变更 → F 生成 CR-001 (from_version: v1.0)
→ F 进行影响分析（扫描 baseline.source_refs 与 task_coverage）
→ 评审（人工或机器）
→ approved：走一遍 P3/P4/P5/P6 生成增量任务与排期 → 冻结 v1.1
→ rejected：保持 v1.0，CR 记录归档