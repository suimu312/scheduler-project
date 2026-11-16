# 任务条目生成与覆盖矩阵

> 负责人：张艺津（E · 需求分析师）｜对应阶段：P6 WBS 定稿 + 覆盖校验｜代码：`src/req_builder/`
>
> 分支：`feature/E-req-builder`｜文档版本：v1.0（对齐《设计方案修订版 V2》第 3.6、4.4、5.1 节与 architecture.md §0.5）｜2026-11-16

---

## 1. 本模块管什么

**一句话**：把评审通过的方案落成一条条能验收、能追到出处的**开发任务卡**，再用覆盖矩阵证明规格没被漏掉。

| | 内容 |
|---|---|
| 输入 | `design_spec`（P1，`FEA-*` / `CON-*`）；`solution_comparison`（C · P4，采纳方案 + 任务草稿）；`conflict_register`（D · P3）；`review_sheet`（D · P5，`CHG-*`） |
| 输出 | `dev_task_list.json` + `task_coverage.json`（均带 meta 信封）；供 A 判 `G_P6_P7` 的数据面；`runs/<run_id>/viz/index.html` |
| 下游 | A 判门禁 → F 出确认单、冻结基线；可视化读 `task_coverage` 与 `schedule` |
| 不负责 | 不造方案外的任务、不改方案（C 的活）、不裁决冲突（D 的活）、不拍板基线（F 的活）、不判门禁（A 的活） |

**边界要说清的一点**：本模块只管"把已有的东西写清楚、写可验、写有出处"，不管"要不要做"。优先级只给等级与理由，最终取舍由 F 走人工确认——写卡的人和拍板的人不能是同一个。

### 1.1 P6 的四步

| 步 | 谁 | 做什么 | 产出 |
|---|---|---|---|
| ① 卡片草稿 | A1 产品经理 | 按采纳方案把功能点落成卡片草稿 | `solution_comparison.tasks[]` |
| ② 结构化与编号 | 本模块（代码） | 字段补齐、`DEV-*` 编号归一、八条规则校验 | `DevTask[]` |
| ③ 优先级判定 | 本模块（代码，不调模型） | 按 P1–P5 规则算等级并回填理由 | `priority` + `priority_reason` |
| ④ 覆盖计算 | 本模块（代码，不调模型） | 算加权覆盖率、识别缺口、组装产物 | `dev_task_list` + `task_coverage` |

**为什么 ③ ④ 不许模型自评**：让模型给自己写的卡排优先级，七成都会写成 Must；让它自查覆盖，结论永远是"已全面覆盖"。这两件事都是可复现的算术。

---

## 2. 数据结构

### 2.1 开发任务卡（产物八字段，docx 表 14）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | `DEV-001` 统一序列，删除后不复用 |
| `module_id` | string | 是 | `MOD-*`，来自 P2 的 module_map |
| `title` | string | 是 | 动词开头、一件事、≤40 字 |
| `depends_on` | list | 是 | 只允许指向存在的 `DEV-*`，不许成环 |
| `estimate_days` | number | 是 | 0.5–3 人天，>3 必须再拆 |
| `owner_role` | string | 是 | 唯一负责人，禁止"多人共同负责" |
| `acceptance` | list | 是 | 至少 1 条，写"什么操作之后看到什么结果" |
| `source_refs` | list | 是 | `产物名:ID` 字符串，如 `solution_comparison:FEA-001` |

产物另外附三样证据字段：`priority`、`priority_reason`、`priority_source`（`rule` / `human`），供复核与答辩逐条核对。

### 2.2 验收口径 `AcceptanceCriterion`

| 字段 | 说明 |
|---|---|
| `ac_id` | `AC-DEV-001-1`，卡号 + 序号 |
| `given` / `when` / `then` | Given 可构造、When 单动作、Then 可观测 |
| `type` | `functional` / `performance` / `security` / `usability` / `compatibility` |
| `metric` | `{name, op, value, unit}`，非功能验收必填 |

产物里允许直接写一句话（"搜「教材」后返回命中商品，p95 不超过 300ms"），`then` 即为该句；结构化写法会拼成 `Given …；When …；Then …`。

### 2.3 来源引用 `SourceRef`

| `kind` | 归一后的产物路径 |
|---|---|
| `feature` | `solution_comparison:FEA-001` |
| `constraint` | `design_spec:CON-001` |
| `conflict` | `conflict_register:CFL-001` |
| `challenge` | `review_sheet:CHG-001` |

模块内部用对象（带 `quote`、`relation`），对外只输出字符串数组。**只存 ID 的追溯是空指针追溯**，`quote` 缺失降 `WARN` 不阻断。

### 2.4 覆盖矩阵 `task_coverage.json`

```json
{
  "meta": { "schema_version": "1.0", "run_id": "R0001", "stage": "P6",
            "produced_by": "req_builder", "created_at": "2026-11-16T20:10:00+08:00" },
  "data": {
    "rows": [
      { "source_type": "feature", "source_id": "FEA-001", "title": "发布商品与列表浏览",
        "source_ref": "solution_comparison:FEA-001", "weight": 3,
        "covered_by": ["DEV-001", "DEV-002"], "status": "covered" },
      { "source_type": "conflict", "source_id": "CFL-001", "source_ref": "conflict_register:CFL-001",
        "weight": 2, "covered_by": [], "status": "gap" }
    ],
    "columns": ["DEV-001", "DEV-002"],
    "gap_count": 1, "coverage_rate": 0.864, "r_source": 0.8, "r_task": 1.0,
    "gaps": [{ "kind": "uncovered_source", "target": "CFL-001", "severity": "ERROR", "weight": 2 }],
    "gate": { "gap_count": 1, "all_have_acceptance": true, "g_p6_p7_data": false,
              "self_check_pass": false }
  },
  "extensions": {}
}
```

- **行**：功能点 + 约束 + 冲突 + 质疑四类全集都要进，漏任何一类都是"选择性覆盖"；
- **列**：非 `Wont` 的任务卡；
- `covered_by` 为空即 `status=gap`，`gap_count` 是 A 判 `G_P6_P7` 的直接输入。

---

## 3. 规则表

### 3.1 八条硬规则（代码逐条校验）

| 编号 | 规则 | 违反后果 |
|---|---|---|
| T1 | 一卡一件事，工时 0.5–3 人天；>3 必须再拆 | `bad_granularity` / `multi_action_card`，ERROR |
| T2 | 负责人唯一，禁止 `前端/后端`、`多人`、`共同` | `owner_not_unique`，ERROR |
| T3 | 依赖只能指向存在的任务卡，不许指向文档 | `dep_target_missing`，ERROR |
| T4 | 依赖成环直接判 `E_DEP_CYCLE`（Kahn 拓扑排不完的节点即环） | 排期中止，退回 P6 |
| T5 | 每卡至少一条可观测验收；非功能必须有 metric | `missing_acceptance` / `unverifiable_ac`，ERROR |
| T6 | 每卡带来源引用，禁止无来源卡（C1） | `orphan_task`，ERROR |
| T7 | 不许出现"联调""优化""杂项"这类不可验收的卡 | `vague_task`，ERROR |
| T8 | 同模块同文件不跨波并行 | **依赖 `schedule.json` 波次，P6 判不了，由 A 在 P7 校验**，本模块不假装校验 |

### 3.2 优先级（纯代码，先命中先返回）

| 编号 | 条件 | 结论 |
|---|---|---|
| P1 | 被其他任务（直接或传递）依赖，或标题命中鉴权/合规/资金等硬规则词 | `must` |
| P2 | 被别的任务验收口径直接引用（正文里点了这张卡的名） | `must` |
| P3 | 来源功能点落在 `scope_out` | `wont`（本期明确不做，不进覆盖率分母） |
| P4 | `priority_source == human` | 锁定，算法只读不写 |
| P5 | 加权得分 `score = 0.5·用户价值 + 0.3·下游数归一 + 0.2·工期压力`，≥0.75 → `must`，≥0.55 → `should`，≥0.30 → `could`，更低仍落 `could` | 按区间 |

**两个口径必须写死**：

- 硬规则直接 MUST，不参与打分——把"权限""数据不可恢复"扔进加权公式，会因为"成本高"被算成 SHOULD；
- `Wont` 是**主动排除**而不是没做完。把它塞进分母，结果是"越诚实标注不做的团队覆盖率越低"。

`priority_reason` 必填，写明命中的规则号与信号取值（如 `P5 得分 0.62（用户价值 1.00 / 下游 1 / 工期压力 0.67）`）。理由写不出来，评审只能靠吵。

### 3.3 验收口径的写法

| 约束 | 违例 |
|---|---|
| Given 可构造 | ✗ "Given 用户很开心" |
| When 单动作 | ✗ "When 点击并刷新页面" |
| Then 可观测（返回 / 出现 / 显示 / 数值 / 秒内） | ✗ "Then 体验良好" |
| 禁用模糊词：尽量、适当、合理、友好、高效、较好、等等、若干、大概 | ✗ "Then 响应快速" |
| 非功能必须有 metric | ✗ "Then 性能高" |

### 3.4 覆盖率公式

符号：`U` = 来源单元集合（feature ∪ constraint ∪ conflict ∪ challenge）；`w(u)` = 权重（`scope_in` 内功能点 3、其他功能点 2、约束 3、冲突 2、质疑 2）；`covered(u)` = 是否存在非 Wont 任务以 `source_refs` 关联它；`T` = 非 Wont 任务集合。

```
R_src   = Σ w(u)·1[covered(u)] / Σ w(u)                    来源加权覆盖率
R_task  = |{t∈T : source_refs≠∅ ∧ acceptance≠∅}| / |T|       任务完备率
R_total = 0.6·R_src + 0.4·R_task                            coverage_rate
```

`Wont` 不进任何分母；覆盖率由代码算，不许模型自评。

### 3.5 缺口类型与门禁分工

| 缺陷 | 判定 | 严重度 |
|---|---|---|
| `uncovered_source` | 来源无任何任务承接 | 权重 3 → ERROR，权重 2 → WARN |
| `orphan_task` | 任务无 `source_refs` | ERROR |
| `missing_acceptance` | 非 Wont 无验收 | ERROR |
| `unverifiable_ac` | 命中模糊词或非功能无 metric | ERROR |
| `bad_granularity` / `multi_action_card` / `owner_not_unique` / `dep_target_missing` / `vague_task` | 见 §3.1 | ERROR |
| `E_DEP_CYCLE` | 依赖成环 | ERROR |

**门禁分两层，别搞混**：

- **A 判 `G_P6_P7`**：`task_coverage.gap_count == 0` 且所有非 Wont 任务有非空 `acceptance`。**判定权在 A**，E 只提供数据；
- **E 自检**（比 A 严）：`gap_count == 0`、`ERROR 级缺口数 == 0`、`R_src ≥ 0.85`、全部有验收。自检不过就不交件，省得 A 打回。

| 结论 | 去向 |
|---|---|
| PASS | 交 F 做确认与基线 |
| FAIL + 缺口在方案侧 | 退 C 补方案条款，E 不替 C 写方案 |
| FAIL + 缺口在讨论侧 | 退 B 重开定点质询 |
| FAIL + 表述问题 | 本模块重写 |
| 补 2 次仍 FAIL | 硬停，输出未收敛报告，写明卡在哪项指标 |

### 3.6 编号与版本

| 对象 | 规则 |
|---|---|
| 任务 | `DEV-001` 自增，删除后不复用；已有合法号保留，非法号按顺序重编 |
| 验收 | `AC-{task_id}-{seq}` |
| 产物 | `schema_version` 固定 `1.0`，`stage=P6`，`produced_by=req_builder` |
| 版本 | 基线 `v1.0` 起，打回产生新版本，旧版本留档不覆盖 |

---

## 4. 接口

| 对象 | 约定 |
|---|---|
| A（编排器） | `run(ctx: StageContext) -> StageResult`，产物键为 `dev_task_list` 与 `task_coverage`，`budget_used = 0`（纯代码不计费）；E 不写 `runs/`、不改状态 |
| B（角色） | 只读 `problem_list` 与 `solution_comparison.tasks`；卡片草稿由 A1 提供，E 不生成方案外任务 |
| C（方案） | 只读采纳方案与功能点，不读被否决方案与过程发言 |
| D（评审） | 只在 `G_P5_P6 = PASS` 后启动；`conflict_register` 与 `review_sheet` 的编号进覆盖矩阵的行 |
| F（基线） | 只读 `dev_task_list.data.tasks`（含 `acceptance`、`baseline_version`）与 `task_coverage.{gap_count, coverage_rate}`；冻结要求 `gap_count = 0` |
| 可视化 | 见 `docs/visualization.md`；`render_views(events, until_seq, artifacts)` 纯函数，只读不写 |

代码文件：

```
src/req_builder/
├── __init__.py        # 包导出：三个入口函数
├── contracts.py       # StageContext / StageResult / RunEvent 信封 + meta 壳
├── models.py          # DevTask / AcceptanceCriterion / SourceRef / CoverageRow / Gap
├── priority.py        # P1–P5 优先级规则（纯代码）
├── validators.py      # T1–T8 八条硬规则 + Kahn 查环
├── coverage.py        # 覆盖矩阵、加权覆盖率、缺口、门禁数据面
├── task_list.py       # P6 主流程：build_task_list / run / load_task_list
├── demo.py            # 自检样例：67 条事件、8 张卡、7 天工期
└── viz/               # 七个视图（详见 docs/visualization.md）
```

---

## 5. 失败处理

| 情况 | 处理 |
|---|---|
| 缺 `design_spec` | `E_ARTIFACT_MISSING`，不造卡，直接 `blocked` |
| 缺任务草稿 | `E_ARTIFACT_MISSING`，P6 只做结构化，不替 A1 造卡 |
| 卡片编号非法或重复 | 按顺序重编 `DEV-001..`，不报错 |
| 依赖成环 | `E_DEP_CYCLE`，列出环上的任务，退回 P6 拆卡 |
| 有来源未被覆盖 | 记 `uncovered_source`；权重 3 判 ERROR，交由人工决定是否补卡 |
| 模型返回格式不符 | 归一后仍缺必填字段则记 ERROR，不阻塞其他条目 |
| 自检不过 | 不交件，`StageResult.status = blocked` 并附前 10 条 ERROR |
| 补 2 次仍不过 | 硬停，输出未收敛报告交人工 |

---

## 6. 修订记录（v0.2 → v1.0）

V2 把本模块的产物从"需求条目（`REQ-*` / `requirement_spec`）"改成"开发任务卡（`DEV-*` / `dev_task_list`）"，并把可视化明确归 E。变更点：

| 项 | v0.2（旧口径） | v1.0（本次） |
|---|---|---|
| 主产物 | `requirement_spec.json` + `coverage_matrix.json` | `dev_task_list.json` + `task_coverage.json` |
| 条目编号 | `REQ-001` | `DEV-001`（八字段，含依赖与工时） |
| 覆盖行 | 问题项 / 方案条款 | 功能点 / 约束 / 冲突 / 质疑（`FEA` / `CON` / `CFL` / `CHG`） |
| 优先级 | MoSCoW 五步（含成本权重） | P1–P5（依赖前置、验收引用、scope_out、人工锁定、加权得分） |
| 新增 | — | 八条硬规则、Kahn 查环、七视图渲染 |
| 保留 | 追溯链、代码算覆盖、A 判门禁 | 同左，口径不变 |

旧口径的 `REQ-*` 字段仍可由 `task_coverage` 的行结构与之一一对应，但不再作为产物名使用，避免同一字段两种叫法（V2 §7.2 第一条修正的正是这个问题）。
