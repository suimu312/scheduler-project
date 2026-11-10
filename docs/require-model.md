# 需求条目生成与覆盖矩阵

> 负责人：张艺津（E · 需求分析师）｜对应阶段：P6 需求条目生成 + 覆盖矩阵 + 来源追溯
>
> 对应代码：`src/req_builder/`｜分支：`feature/E-req-builder`
>
> 状态：v0.2（对齐 architecture.md §0.5.3/§0.5.4 契约）｜2026-11-10

---

## 1. 本模块管什么

**一句话**：把评审通过的方案翻译成一条条能验收、能追到出处的需求，再用覆盖矩阵证明没漏。

| | 内容 |
|---|---|
| 输入 | `solution_comparison`（C · P4，取 `feature_items[*].adopted`，编号 `FEA-xxx`）；`review_sheet` + `conflict_register`（D · P5，编号 `CHG-xxx` / `CFL-xxx`，且 `GateResult(G_P5_P6)=PASS`）；`problem_list`（B · P2 经 A 中转）；`idea_card`（A · P1） |
| 输出 | 产物 `requirement_spec.json` + `coverage_matrix.json`（均带 meta 信封，architecture §0.4）；内部对象 `CoverageReport` / `Gap` / `TraceLink`；门禁数据供 A 判 `G_P6_P7`；对外文档 `docs/require-model.md` |
| 下游 | 通过 → F 做人工确认与需求基线（F 只读 `requirement_spec.data.requirements` 与 `coverage_matrix.{gap_count, coverage_rate}`）；不通过 → 退 C 补方案条款、退本模块补需求，纯缺口交人工 |
| 不负责 | 不新增方案外需求（只出草稿）、不改方案（C 的活）、不做冲突裁决（D 的活）、不拍板基线（F 的活）、不判门禁（A 的活，本模块只提供数据与自检） |

**边界要说清的一点**：本模块只管"把已有的东西写清楚、写可验、写有出处"，不管"要不要做"。优先级只给建议等级，最终取舍由 F 走人工确认。这样切，是为了让"写需求的"和"拍板的"不是同一个人。

### 1.1 P6 的四步怎么走

| 步 | 谁 | 做什么 | 产出 | 可并行 |
|---|---|---|---|---|
| ① 条款拆解 | A5 需求分析师 | 把方案功能点拆成需求草稿，一条一句 | `RequirementItem`(draft) | 是 |
| ② 优先级判定 | 本模块（代码，不调模型） | 按 MoSCoW 规则算等级与理由 | `priority` + `priority_reason` | — |
| ③ 验收标准生成 | A5 需求分析师 | 每条需求写 Given-When-Then | `AcceptanceCriterion` | 是 |
| ④ 覆盖计算 | 本模块（代码，不调模型） | 算覆盖率、识别缺口、组装产物 | `requirement_spec` + `coverage_matrix` | — |

**为什么 ② 和 ④ 不许模型自评**：让模型给自己写的需求排优先级，它会把七成都写成 MUST；让它自查覆盖，它会说"已全面覆盖"。这两件事都是可复现的算术，必须由代码算。模型只负责"写清楚"，代码负责"算明白"。

**为什么产物必须带 meta 信封**：A 的持久化、事件、重放全部按 `{meta, data, extensions}` 走（architecture §0.4）。本模块不自己造轮子，`make_artifact(data, run_id)` 一行套壳，`stage=P6`、`produced_by=req_builder`。

---

## 2. 数据结构

### 2.1 RequirementItem（需求条目，模块内部模型）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `req_id` | string | 是 | `REQ-001` 统一序列，会话内唯一，**删除后不复用** |
| `session_id` | string | 是 | 所属会话 |
| `version` | string | 是 | 需求文档版本，打回重做产生新版本而非覆盖 |
| `category` | enum | 是 | `FR` 功能 / `NFR` 非功能 / `CON` 约束 |
| `title` | string | 是 | 一句话，动词开头，≤40 字 |
| `description` | string | 是 | 做什么 + 为什么，必须写清依据 |
| `priority` | enum | 是 | MoSCoW：`MUST` / `SHOULD` / `COULD` / `WONT`（产物中小写） |
| `priority_reason` | string | 是 | 判定理由，写明命中的规则与信号值，供复核 |
| `priority_source` | enum | 是 | `rule` 代码判定 / `agent` 智能体判定 / `human` 人工锁定 |
| `acceptance_criteria` | list | 是 | 至少 1 条 `AcceptanceCriterion`；`WONT` 可为空 |
| `source_refs` | list | 是 | 至少 1 条 `SourceRef`，无来源即孤儿需求 |
| `status` | enum | 是 | `draft` / `pending_confirm` / `confirmed` / `rejected` / `deferred` |
| `parent_id` | string | 否 | 父需求编号，需求分解时使用（产物中序列化为 `depends_on[]`） |
| `tags` | list | 否 | 业务域标签，重复检测时用于分桶 |
| `gap_flag` | bool | 是 | 是否由覆盖缺口反推的草稿，`true` 时不得直接进基线 |

**为什么内部模型和产物字段名不强行一致**：内部 `acceptance_criteria` 是对象列表（带 `ac_id` / `metric`），产物里 `given/then` 按契约拍平成字符串（architecture §0.5.3 示例就是字符串）。序列化在 `_spec_item()` 一处完成，内部富结构不丢，对外契约不破。

**为什么 `priority_reason` 是必填而不是选填**：优先级算错是常态，但只要理由写得出来，人工复核三秒就能判断"这条凭什么 MUST"。没有理由的优先级，评审时只能靠吵。

**为什么 `gap_flag` 要单独一个字段**：覆盖缺口反推出来的需求没有方案依据，本质是"猜的"。给它打标记，是为了让 F 在确认阶段一眼区分"方案里有的"和"我们补的"。

### 2.2 AcceptanceCriterion（验收标准）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ac_id` | string | 是 | `AC-REQ-001-1`，需求编号 + 序号 |
| `given` | list | 是 | 前置条件，至少 1 条，必须是可进入的系统状态（产物中以 `；` 连接成字符串） |
| `when` | string | 是 | **单一**触发动作，多个动作必须拆成多条 AC |
| `then` | list | 是 | 期望结果，至少 1 条，必须可观测（产物中同 `given` 处理） |
| `type` | enum | 是 | `functional` / `performance` / `security` / `usability` / `compatibility` |
| `metric` | object | 否 | `{name, op, value, unit}`，`NFR` 必填 |

**为什么 `when` 只许一个动作**：一个 AC 验两件事，失败时分不清是哪件坏了。测试用例是一条 AC 一个场景，多动作的 AC 拆不成用例。

### 2.3 SourceRef（来源引用）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | enum | 是 | 链路透传：`idea` / `problem` / `feature` / `conflict` / `challenge`；证据类：`stance` / `vote` / `verdict` / `review`（只留痕，不参与覆盖计算） |
| `ref_id` | string | 是 | 上游实体 ID，如 `P-001`、`FEA-002`、`CFL-001` |
| `quote` | string | 否 | 原文摘录 ≤120 字，便于人工复核，缺失降为 `WARN` |
| `relation` | enum | 是 | `direct` 直接依据 / `derived` 派生 / `constraint` 约束来源 |

**上游命名归一（实现层别名，不进需求正文）**：

| 上游叫法 | 归一为 | 产物中的引用字符串（architecture §0.1 规则 6） |
|---|---|---|
| B 的 `Issue` / 设计文档 `ProblemItem` | `problem` | `problem_list:P-001` |
| C 的 `ProposalSection` / `feature_items` | `feature` | `solution_comparison:FEA-001.adopted` |
| D 的 `Verdict` / 冲突 | `conflict` | `conflict_register:CFL-001` |
| D 的评审质疑 | `challenge` | `review_sheet:CHG-001` |
| A 的原始想法 | `idea` | `idea_card:IDEA-xxx` |

产物 `source_refs` 是**字符串数组**（契约硬约束 C3：必须能解析到 `solution_comparison:FEA-*.adopted` 或 `conflict_register:CFL-*` 等真实产物路径）；模块内部仍用对象，靠 `source_ref_string(kind, ref_id)` 一处映射。

**为什么必须留 `quote`**：只存 ID 的追溯是"空指针追溯"——点进去可能发现上游早被改过。留一句原话，等于给链路拍了快照，后面谁改都赖不掉。

### 2.4 CoverageMatrix（覆盖矩阵）

模块内部 `CoverageReport` 管判定，对外产物是 architecture §0.5.4 的 `coverage_matrix.json`：

```json
{
  "rows": [
    { "source_type": "problem",  "source_id": "P-001",
      "source_ref": "problem_list:P-001", "covered_by": ["REQ-001"], "status": "covered" },
    { "source_type": "conflict", "source_id": "CFL-001",
      "source_ref": "conflict_register:CFL-001", "covered_by": [], "status": "gap" }
  ],
  "columns": ["REQ-001", "REQ-002"],
  "gap_count": 1,
  "coverage_rate": 0.864
}
```

- **行**（来源单元）：`problem_list` 问题项 + `conflict_register` 冲突 + `review_sheet` 质疑 + 已采纳方案功能点（`feature`）——四类全集都要进，漏任何一类都是"选择性覆盖"；
- **列**（需求条目）：非 `WONT` 的 `REQ-xxx`；
- `covered_by` 为空即 `status=gap`，计入 `gap_count`，这是 A 判 `G_P6_P7` 的直接输入。

### 2.5 Gap（缺口）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | enum | 是 | 见 §3.5 缺陷类型表 |
| `target` | string | 是 | 来源 `ref_id` 或需求 `req_id` |
| `severity` | enum | 是 | `ERROR` 阻断门禁 / `WARN` 只记录 |
| `detail` | string | 是 | 一句话说明问题 |
| `weight` | int | 是 | 用于排序，缺口按权重降序处理 |

### 2.6 TraceLink（追溯链）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `trace_id` | string | 是 | `T-0001` |
| `req_id` | string | 是 | 需求编号 |
| `path` | list | 是 | 有序链路 `idea → problem → feature → req` |
| `depth` | int | 是 | 链路深度，满链为 4 |
| `complete` | bool | 是 | 是否追溯到想法根节点 |
| `level` | enum | 是 | `OK` / `WARN`（非 MUST 断链）/ `ERROR`（MUST 断链） |
| `broken_at` | string | 否 | 断链层级 |

**为什么 MUST 断链要直接 ERROR 而不是告警**：Must 意味着"没它不行"，而不知道它从哪来，就没法判断改方案时会不会把它改坏。这条不能凑合。

### 2.7 对外产物形状（architecture §0.5.3）

```json
{
  "meta": { "schema_version": "1.0", "run_id": "R0001", "stage": "P6",
            "produced_by": "req_builder", "created_at": "2026-11-10T10:00:00+08:00" },
  "data": {
    "requirements": [
      { "req_id": "REQ-001", "category": "FR", "title": "……", "description": "……",
        "acceptance_criteria": [ { "ac_id": "AC-REQ-001-1", "given": "……", "when": "……", "then": "……" } ],
        "priority": "must", "depends_on": [],
        "source_refs": ["solution_comparison:FEA-001.adopted", "conflict_register:CFL-001"],
        "baseline_version": "v1.0" }
    ],
    "hard_validation": { "all_have_acceptance_criteria": true }
  },
  "extensions": {}
}
```

设计文档 7.1 的 `RequirementDoc(id, fr_list, nfr_list, priority, acceptance)` 保留为汇总视图，由 `data.requirements` 单源渲染，不许各写一份。

---

## 3. 规则表

### 3.1 需求条目生成

| 规则 | 说明 |
|---|---|
| 一条一句 | 一条需求只描述一件事，`title` 里不许出现"以及""同时"连接两个动作 |
| 必须挂方案条款 | `source_refs` 必须含 `feature` 或 `conflict`，否则判 `orphan_requirement` |
| 不许新增方案外需求 | 只能写 `gap_flag=true` 草稿，不进基线，由 F 人工决定 |
| 拆分粒度 | 一个方案功能点至多拆 3 条需求，超出说明拆过细，要求合并 |
| 依据要写进描述 | `description` 必须点明依据哪条问题项或方案功能点，不许只写功能名 |
| 原文摘录 | 来源引用尽量带 `quote`，缺失降 `WARN` 不阻断 |

**为什么"一条一句"是硬规则**：需求粒度失控是多智能体系统里最常见的产物缺陷——模型被要求"全面"，就会把三个功能揉进一句话，最后验收标准写得出来、测试用例拆不出来。

### 3.2 MoSCoW 优先级判定

| 级别 | 含义 | 覆盖权重 | 变更影响 |
|---|---|---|---|
| `MUST` | 缺失则产品不可用或违规 | 3 | 移除需人工审批 + 基线变更单 |
| `SHOULD` | 重要，可短期替代 | 2 | 可延后一个迭代，需记录 |
| `COULD` | 锦上添花 | 1 | 直接延后，无需审批 |
| `WONT` | 本期明确不做 | 0 | 记入 backlog，**不进覆盖率分母** |

判定顺序，先命中先返回：

```
1. risk_if_absent == hard（合规/安全/资金/数据不可恢复） → MUST
2. must_have == true（用户代表原话或 P7 人工裁决标记）    → MUST
3. score = 0.4*业务价值 + 0.4*用户影响 + 0.2*(1 - 成本归一)
     score >= 0.70 → MUST
     0.50 ~ 0.70   → SHOULD
     0.30 ~ 0.50   → COULD
     score <  0.30 → WONT
4. 被 2 条以上需求依赖 → 至少提到 SHOULD
5. priority_source == human → 锁定，1~4 一律不得覆写
```

| 规则 | 说明 |
|---|---|
| 硬规则清单 | 权限、鉴权、登录、隐私、合规、支付、资金、删除、覆盖、不可恢复、备份、审计 |
| 成本归一 | `min(1, 估算人天 / 20)`，超过 20 人天按 20 算，避免大项目整体降级 |
| 人工锁定 | 人工设置过优先级的条目，算法此后只读不写 |
| 理由回填 | `priority_reason` 必须写出命中的规则号与三项信号取值 |

**两个口径必须写死，否则实现时一定出错**：

- **硬规则直接 MUST，不参与打分**。把"权限""数据不可恢复"扔进加权公式，会因为"成本高"被算成 SHOULD——这类东西本来就不该有成本讨论空间。
- **`WONT` 是主动排除，不是没做完**。把它塞进覆盖率分母，结果是"越诚实标注不做的团队，覆盖率越低"，指标立刻变成鼓励含糊其辞。

### 3.3 Given-When-Then 验收标准

| 约束 | 说明 | 违例 |
|---|---|---|
| Given 可构造 | 必须是系统能进入的状态 | ✗ "Given 用户很开心" |
| When 单动作 | 一个 AC 只验一个行为 | ✗ "When 点击并刷新页面" |
| Then 可观测 | 有具体界面 / 接口 / 数据变化 | ✗ "Then 体验良好" |
| 禁用模糊词 | 尽量、适当、合理、友好、快速、高效、较好、等等、若干、大概 | ✗ "Then 响应快速" |
| NFR 必须有 metric | 无指标的 NFR 无法验收，直接判 `ERROR` | ✗ "Then 性能高" |

```
Given 管理员已登录且拥有题目编辑权限
When  管理员提交已存在的题目编号
Then  系统返回 409 并提示"题目已存在"
And   原题目数据保持不变
```

**为什么 NFR 不给指标就直接判错**：非功能需求是确认阶段最容易吵架的地方，没有数字就只能各说各的。给出 `p95 <= 300ms` 之后，F 的确认环节才有判断依据，而不是靠"感觉还行"。

### 3.4 覆盖率公式

符号：`U` = 来源单元集合（`problem` ∪ `feature` ∪ `conflict` ∪ `challenge`）；`w(u)` = 权重，问题项取 `criticality∈{1,2,3}`，方案功能点 / 冲突 / 质疑固定 3 / 2 / 2；`covered(u)` = 是否存在非 `WONT` 需求以 `direct`/`derived` 关联它；`R` = 非 `WONT` 需求集合。

```
R_src   = Σ w(u)·1[covered(u)] / Σ w(u)                 来源加权覆盖率
R_req   = |{r∈R : source_refs≠∅ ∧ acceptance_criteria≠∅}| / |R|   需求完备率
R_must  = |{u : w(u)=3 ∧ covered(u)}| / |{u : w(u)=3}|   Must 来源覆盖率
R_total = 0.6·R_src + 0.4·R_req                          综合覆盖率（产物 coverage_rate）
```

**两个口径必须写死，否则实现时一定出错**：

- **`WONT` 不计入任何分母**。理由同上，主动排除不该被当成遗漏。
- **覆盖率由代码算，不许模型自评**。让模型评价自己的覆盖情况，结论永远是"已全面覆盖"，这条和 D 模块"驳回率不许模型自评"是同一个道理。

**为什么 `R_must` 单独拎出来做一票否决**：综合覆盖率 0.95 听起来很好，但如果漏掉的那 5% 正好是"登录鉴权"，这个需求文档就是废的。平均值会掩盖关键缺失，所以 Must 级来源必须零遗漏。

### 3.5 缺口识别与门禁 G_P6_P7

| 缺陷类型 | 判定 | 严重度 | 处置 |
|---|---|---|---|
| `uncovered_source` | 来源无任何需求承接 | 权重 3/2 → `ERROR`，1 → `WARN` | 反推 `gap_flag=true` 草稿交人工 |
| `orphan_requirement` | `source_refs` 为空且非 gap 草稿 | `ERROR` | 删除或补依据 |
| `missing_acceptance` | 非 `WONT` 但无验收标准 | `ERROR` | 退回补 AC |
| `unverifiable_ac` | 命中模糊词，或 NFR 无 metric | `ERROR` | 退回重写 |
| `duplicate` | 同 `category` 且标题相似度 ≥ 0.85 | `WARN`，超过 2 条判 `FAIL` | 合并或注明差异 |
| `unlinked_section` | 方案功能点未关联任何问题项 | `WARN` | 要求 C 回溯补链，不阻断 |

**门禁分两层，别搞混**：

- **A 判 `G_P6_P7`**（架构口径，architecture §8.1）：`coverage_matrix.gap_count == 0` 且 `requirement_spec` 中所有 `REQ` 有非空 `acceptance_criteria`。**判定权在 A**，E 不自己宣布通过；
- **E 自检**（本模块口径，比 A 严）：`R_must == 1.0` 且 `R_total >= 0.85` 且 `ERROR 级缺口数 == 0` 且 `duplicate 数 <= 2`。E 自检不过就不交件，省得 A 再打回来。

| 结论 | 去向 |
|---|---|
| `PASS` | 交 F 做人工确认与基线 |
| `FAIL` + 缺口在方案侧 | 退 C 补方案条款 |
| `FAIL` + 缺口在讨论侧 | 退 B 重开定点质询 |
| `FAIL` + 纯需求表述问题 | 本模块重写，最多 2 次 |
| 补 2 次仍 `FAIL` | 硬停，输出未收敛报告交人工，报告写明卡在哪项指标 |

### 3.6 编号与版本

| 对象 | 规则 |
|---|---|
| 需求编号 | `REQ-001` 统一序列自增（architecture 全项目约定），删除后不复用 |
| 验收编号 | `AC-{req_id}-{seq}`，与需求同生命周期 |
| 追溯编号 | `T-0001`，一次计算内唯一 |
| 版本 | `v1.0` 起，打回重做升 `v1.1`，旧版本留档不覆盖（产物字段 `baseline_version`） |
| 与文档同步 | `RequirementItem` 的 Schema 变更，`docs/require-model.md` 必须同版本更新 |

---

## 4. 失败处理

| 情况 | 处理 |
|---|---|
| 需求缺 `source_refs` | 判 `orphan_requirement`，`ERROR`，退回重写 1 次，仍缺则剔除并记事件 |
| Must 需求追溯断链 | `ERROR`，阻断自检，要求补链后再算 |
| 方案功能点未关联问题项 | 记 `unlinked_section`，`WARN` 不阻断，同时通知 C 补链 |
| NFR 缺 metric | `ERROR`，退回补指标，不许用"性能良好"糊过去 |
| AC 命中模糊词 | `ERROR`，退回重写，最多 2 次，仍不达标则挂起该条 |
| 标题高度重复 | `WARN`；超过 2 条判 `FAIL`，交人工判断是否合并 |
| 模型返回格式不符（枚举越界、缺字段） | 允许 1 次重试，仍失败则该条作废并记事件，不阻塞其他条目 |
| 覆盖缺口来自方案 | 退 C 补条款，不由 E 自行编造需求 |
| 覆盖缺口来自讨论 | 退 B 重开定向质询，补出该议题的结论 |
| 缺口既非方案也非讨论（凭空冒出） | 生成 `gap_flag=true` 草稿，交人工判断是否纳入 |
| 同一功能点拆出 >3 条需求 | 告警，要求合并后重算 |
| 补写 2 次仍 `FAIL` | 硬停，输出未收敛报告交人工 |

---

## 5. 与其他模块的接口

| 对象 | 约定 |
|---|---|
| A（编排器） | 签名按 architecture §8.2：`build_requirements(ctx) -> StageResult`、`build_coverage_matrix(ctx) -> dict`；产物带 §0.4 meta 信封（`stage=P6`，`produced_by=req_builder`）；`G_P6_P7` 由 A 判，E 只保证自检口径更严 |
| B（角色） | 读 `problem_list`（`P-xxx`，含 `criticality`）作为问题来源，`stance` / `vote` 只做证据留痕；B 的 `Issue` 与 `problem` 做别名归一，需求正文不出现两套编号 |
| C（方案） | 只读 `solution_comparison.feature_items[*].adopted`（`FEA-xxx`），不读被否决方案与过程发言；缺口只回传"哪个 `FEA` 没被承接"，不替 C 写方案 |
| D（评审） | 仅在 `GateResult(G_P5_P6)=PASS` 且无 `BLOCKER` 后启动；`conflict_register`（`CFL-xxx`）与 `review_sheet` 质疑（`CHG-xxx`）都进覆盖矩阵的行；E 不重新打分、不改裁决 |
| F（基线） | F 只读两个字段面：`requirement_spec.data.requirements`（含 `acceptance_criteria`，冻结要求全非空）与 `coverage_matrix.{gap_count, coverage_rate}`（冻结要求 `gap_count=0`）；人工驳回产生新版本，旧版本留档 |
| `docs/require-model.md` | 本文档与 `RequirementItem` 的 JSON Schema 同版本；实现位于 `src/req_builder/`（models / moscow / acceptance / trace / coverage / generate / schema / envelope / demo），`python -m req_builder.demo` 可自检 |
