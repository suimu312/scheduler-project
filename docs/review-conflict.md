# 对抗式评审与冲突裁决

> 负责人：柳飞絮（D · 质量评审员）｜对应阶段：P5 对抗式评审 + 冲突登记与三级裁决
>
> 状态：草稿 v0.1｜2026-11-10

---

## 1. 本模块管什么

**一句话**：让方案在"被夸"和"被挑"之间过一遍，然后把分歧按证据强弱收敛掉，收敛不了的交给人。

| | 内容 |
|---|---|
| 输入 | 三套**任务分解**方案 + 比选表（来自 C）；P3 四份立场陈述与问答对（来自 B）；`Conflict` 条目（本模块自己登记） |
| 输出 | 评分表 `ScoreCard`、评审意见 `ReviewOpinion`、裁决结果 `Verdict`、门禁结论 `GateResult(G4)` |
| 下游 | 通过 → E 生成任务卡与覆盖矩阵；不通过 → 回炉（退 C 或退 B） |
| 不负责 | 不写方案、不打总分（只打自己那一维）、不代替人做 L3 裁决 |

**边界要说清的一点**：本模块只管"评"和"裁"，不管"改"。回炉改方案是 C 的活，重开讨论是 B 的活。这样切，是为了让"谁该改"这件事有唯一答案。

### 1.1 P5 的四步怎么走

| 步 | 谁 | 做什么 | 产出 | 可并行 |
|---|---|---|---|---|
| ① 支持陈述 | A1、A5 | 每条功能项给理由与影响面 | 支持意见 | 是 |
| ② 反方质疑 | A4 | 按四要素提质疑，缺一即无效 | `ReviewOpinion` | 是 |
| ③ 五维打分 | A2/A3/A4/A5 | 各自只打自己那一维，互不可见 | `ScoreCard` | 是 |
| ④ 门禁判定 | 本模块（代码，不调模型） | 算加权分、标准差、阻断项 | `GateResult(G4)` | — |

**A1 在 ③ 缺席**：方案是它写的，作者不评自己的作品——这条和真实评审会的做法一致，也和上次方案里"产品经理在 P5 退场"是同一件事。

---

## 2. 数据结构

### 2.1 ReviewOpinion（评审意见）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | `R-013` |
| `author` | enum | 是 | `A1`–`A5` |
| `target` | ref | 是 | 针对哪张任务卡/哪个功能项，不能针对"整体" |
| `problem` | string | 是 | 问题是什么 |
| `impact` | ref | 是 | 影响哪个任务卡/功能项（引用） |
| `severity` | enum | 是 | `BLOCKER` / `MAJOR` / `MINOR` |
| `if_unfixed` | string | 是 | **不改会怎样**，缺这条判无效 |
| `verdict` | enum | 是 | `ACCEPTED` / `REJECTED` / `PENDING` |

**四要素缺一不可**：`problem`、`impact`、`severity`、`if_unfixed`。少任何一个，这条意见直接丢弃并记事件 `E_INVALID`。

**为什么非要 `if_unfixed`**：让模型挑刺，它永远挑得出来，三轮能列四十条，其中三十五条是"建议增加日志""建议补充文档"这类正确的废话。要求它说清"不改会怎样"，等于要求它证明这条意见有后果——说不清后果的就不是质疑，是表态。

### 2.2 ScoreCard（评分表）

| 维度 | 打分者 | 权重 | 说明 |
|---|---|---|---|
| 功能完整性 | A2 用户代表 | 25 | 核心场景能否走通 |
| 技术可行性 | A3 架构师 | 25 | 能不能做、代价多大 |
| 风险与安全 | A4 质量评审员 | 25 | 是否有阻断级风险 |
| 可测试性 | A5 需求分析师 | 15 | 每条需求是否可验 |
| 成本合理性 | A1 产品经理 | 10 | **只提供估算，不计分** |

**结构**：`{dimension, scorer, score(0-100), reason}` × 5。

**三个必须坚持的点**：

1. **一个人只打一维**。让一个角色打总分，那叫单方评价，不叫多方评价。
2. **成本不计分只给估算**。成本是约束，不是质量维度；计分会让所有方案都往"省钱"上挤。
3. **A1 回避评审自己写的方案**。作者不评自己的作品，这条和真实评审会一致。

### 2.3 Conflict（冲突登记）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | `C-013` |
| `topic_id` | string | 是 | 必须取自议题词表，**不许自造** |
| `positions` | list | 是 | `{agent, stance, evidence_ref}` |
| `level` | enum | 是 | `L1` 有证据分歧 / `L2` 双方均无证据 / `L3` 无法收敛 |
| `settle` | object | 否 | `{method, result, by}` |
| `status` | enum | 是 | `OPEN` / `SETTLED` / `SUSPENDED` |

**`topic_id` 必须来自词表**：否则"手机号能不能给看"和"隐私保护"会被当成两个议题，冲突永远对不上。词表由 B 维护，本模块只引用。

**`level` 的判定规则**（登记时先置空，裁决过程中回填）：

| 情形 | level | 后续处理 |
|---|---|---|
| 仅一方有 `evidence_ref` | `L1` | 证据优先，直接判，不投票 |
| 双方都有证据，或双方都没有 | `L2` | 进加权投票 |
| `L2` 加权后 `|score| < 1.0` | `L3` | 挂起，交人裁决 |

### 2.4 Verdict（裁决结果）

`{conflict_id, method: EVIDENCE|WEIGHT|HUMAN, winner, weight_score, reason}`

---

## 3. 规则表

### 3.1 评审意见受理

| 条件 | 动作 |
|---|---|
| 四要素齐全 | 受理，`verdict = PENDING` |
| 缺 `if_unfixed` | 丢弃，记 `E_INVALID`，退回作者重写 |
| `target` 指向"整体方案" | 丢弃，要求定位到具体条目 |
| 同一 `target` 同一问题重复 | 合并，保留 `severity` 高的那条 |

### 3.2 门禁 G4 判定

```
# 四个计分维度权重和为 90，先归一化到百分制（除以权重和，不要再乘 100）
raw      = 25*完整性 + 25*可行性 + 25*风险安全 + 15*可测试性
Σw      = 25 + 25 + 25 + 15 = 90          # 权重和
weighted = raw / Σw                # 85.5 这种量级，满值 100；不要乘 100

pass = weighted >= 80
     ∧ blocker_count == 0
     ∧ std([完整性, 可行性, 风险安全, 可测试性]) < 12
```

| 条件 | 结论 |
|---|---|
| 三条同时满足 | PASS，交 E 生成需求 |
| 总分 < 80 | FAIL，退 C 改方案 |
| 存在 BLOCKER | FAIL，退 C 改方案（阻断项优先，不看总分） |
| 标准差 ≥ 12 | FAIL，按"谁给最低分"分流回炉 |

**两个口径必须写死，否则实现时一定出错**：

- **权重归一化**。25+25+25+15 = 90，成本那 10 不计分。如果直接按 `0.25/0.25/0.25/0.15` 加权，满分只有 90 分，却要求 ≥80——等于要求四维平均 89 分，几乎没有方案能过。所以要先除以权重和 Σw（=90）归一化到百分制——**不要再乘 100**，否则会得到 8550 这种量级，阈值 80 永远成立，门禁形同虚设。
- **标准差算在四个原始分上**，不含成本、也不算加权分。它衡量的是"有人极度不满"，用原始分才不失真；用加权分会被权重稀释，掩盖分歧。

**标准差那条最容易被人当成多余**：五个维度有人打 95 有人打 60，平均 78 看着像"差一点"，实际是"关键问题根本没谈拢"。总分过线但标准差 ≥12，说明有人极度不满，应该退回去，而不是"凑合过"。

### 3.3 回炉分流

| 失败原因 | 退到哪 | 说明 |
|---|---|---|
| 选型不当、成本估高 | C（改方案） | 方案层能解决 |
| 用户想要的和技术能给的对不上 | B（重开定点质询） | 需求层的矛盾，改十遍方案也解决不了 |
| 第 1 次失败 | C | — |
| 第 2 次失败 | B，只针对争议那一条 | — |
| 第 3 次失败 | **硬停，升级给人**，输出未收敛报告 | 报告写清：卡在哪一维、谁和谁不一致、试过什么 |

### 3.4 三级裁决

```
一级（证据优先，不投票）：
    一方有 evidence_ref，另一方只有"我觉得" → 采有证据的一方

二级（加权投票）：
    双方都有依据 → 按权重算
    A2 用户代表 = 1.5，A4 质量评审员 = 1.5，A1 = 1.0，A3 = 1.0
    score = Σ赞成权重 − Σ反对权重
    |score| >= 1.0 → 判定；否则进三级

三级（挂起）：
    标 L3，status = SUSPENDED，交人裁决
```

**权重 1.5 的理由**：用户代表和质量评审员的诉求被牺牲后，返工代价最大——前者导致验收失败，后者导致上线出事。这条要能当着老师面说出来，不能是"我们觉得"。

### 3.5 挑刺灌水的抑制

| 机制 | 规则 |
|---|---|
| 后果强制 | 缺 `if_unfixed` 的意见不计入 |
| 驳回率降权 | 某角色连续两轮被驳回 >60%，下一轮意见权重 ×0.5 |
| 数量上限 | 每轮每人 ≤5 条，超出的按 `severity` 取前 5 |

**驳回率怎么算**：分母是该角色提出的、进入裁决的意见条数，分子是其中被判"不成立"的（`verdict = REJECTED`，或被三级裁决否掉）。**这个数由裁决记录自动算，不许模型自评**——让模型给自己打分，它永远给自己打高分。

注意它和"人对意见的采纳"不是一回事：P5 阶段人不参与打分，只在 L3 挂起时裁一条。所以降权判的是"这个角色提的质疑反复站不住"，不是"人不同意它"。

---

## 4. 失败处理

| 情况 | 处理 |
|---|---|
| 模型返回格式不符（缺字段、severity 不在枚举内） | 判 `E_INVALID`，允许 1 次重试，仍失败则该条意见作废并记事件，不阻塞评审 |
| 引用 `target` 指向不存在的条目 | 丢弃该条，记 `E_INVALID` |
| 三轮评审后仍在挑新毛病 | 停止收集新意见，只对已有意见投票，进裁决 |
| 五维分数全部相同且都为满分 | 判为异常（疑似未实际评审），要求重打并记事件 |
| 裁决时双方证据都为空 | 直接进三级，不做加权（避免"两个都觉得"互相抵消） |
| 人长时间不裁决 | 不定时器，保持 SUSPENDED；界面持续高亮 |

---

## 5. 与其他模块的接口

| 对象 | 约定 |
|---|---|
| A（编排器） | 本模块只做判定，不做调度；所有动作通过 `GATE_FAIL` / `CONFLICT_SETTLE` / `HUMAN_*` 事件上报 |
| B（角色） | 冲突的 `topic_id` 取自 B 维护的议题词表；立场陈述由 B 产出 |
| C（方案） | 回炉指令只传"哪一维不达标 + 阻断项引用"，不传修改意见 |
| E（需求） | 通过的裁决结果作为需求条目的"来源链"之一，可追溯到哪条冲突被解决 |

---

## 6. 代码实现（`src/conflict_review/`）

本模块的判定全部是纯函数，不调模型、不依赖网络，`budget_used` 恒为 0，便于第 2 周联调时先用样例数据跑通。

| 文件 | 职责 |
|---|---|
| `contracts.py` | 信封适配层：复刻 `StageContext` / `StageResult` / `RunEvent` / `GuardSignal`，并提供产物 meta 壳 `make_artifact`（字段与 A 的 `validate_artifact` 逐字对齐） |
| `models.py` | 数据结构 `Stance` / `Conflict` / `Challenge` / `ScoreSheet`，以及字段归一（`claim`↔`proposal`、`source_refs`↔`evidence_refs`、`FOR`↔`for`） |
| `rules.py` | 七条纯函数：`canon_scores` / `weighted_total` / `stddev` / `gate` / `validate_challenge` / `triage_challenges` / `find_veto_items` / `detect_conflicts` / `adjudicate` / `route_rework` / `review` |
| `stage.py` | 统一入口 `run(ctx)`：`stage == P3` 走冲突扫描与裁决，`stage == P5` 走四要素校验、打分与回炉分流 |
| `sample_input.json` | 一份联调样例：B 真实格式的 9 条立场陈述（覆盖 L1/L2/L3 三种裁决）+ 打分 + 任务清单（含三种否决项） |
| `demo.py` | 读样例 → P3 裁决 → P5 打分，打印全过程 |
| `test_rules.py` | 12 条断言：基础规则 4 条、字段兼容 1 条、四要素与驳回率 2 条、否决项 2 条、回炉分流 1 条、`run(ctx)` 集成 2 条 |

运行方式：`python -m src.conflict_review.demo`（或在该目录下 `python demo.py`）；测试同理 `python test_rules.py`。

**约定**：本模块只返回判定结果，不写 `runs/`、不改状态；所有动作由编排器 A 落盘并转为事件，事件序号也由 A 的 `EventEmitter` 统一分配。

## 7. 与其他模块的字段契约（联调必读）

这一节是第 2 周联调的对齐基线。**改字段先改本节，再改 `models.py`。**

### 7.1 入口

五个模块一律在 `src/<模块>/__init__.py` 暴露 `run(ctx) -> StageResult`，A 的 `modules.resolve` 靠它加载。
D 的 `run` 按 `ctx.stage` 分发：`P3` → `run_p3`，`P5` → `run_p5`，其余阶段直接 `failed`。

### 7.2 吃进来的（B → D）

| D 的标准名 | 接受的别名 | 来源 |
|---|---|---|
| `agent_id` | `agent` / `speaker` / `scorer` | `discussion_minutes.statements[]` |
| `position` | `stance` / `attitude`；大小写与中文都认 | B 写 `FOR`/`AGAINST`，D 内部一律归一为 `for`/`against`/`neutral` |
| `claim` | `proposal` / `statement` / `text` / `opinion` | B 当前用 `claim` |
| `source_refs` | `evidence_refs` / `sources` / `evidence`（字符串自动转数组） | B 当前用 `source_refs` |
| `topic_id` | `issue_id`（缺失时回落到 `issue.topic_id`） | 分组键 |

**结论：B 不需要改任何字段**，D 侧做归一即可。这条是硬约束——两个模块都改字段只会让第三个人看不懂。

### 7.3 交出去的（D → A）

`conflict_register`（P3 / P5 都要回传）：

```
data.conflicts[]      conflict_id / issue_id / topic_id / stances[] / level / status / resolution
                      status: open | resolved | suspended（L3 挂起）
data.open_count       status == "open" 的条数
data.escalated_ids[]  被挂起交人的 conflict_id
data.stats            议题数 / 立场数 / 新登记数 / by_level
```

`review_sheet`（仅 P5）：

```
data.scores[]         {dimension, scorer, score, reason}，dimension ∈ completeness/feasibility/risk/testability
data.weighted_total   百分制（Σ 权重×分 ÷ 90，不再乘 100）
data.stddev           四维原始分的总体标准差
data.veto             是否存在否决项
data.veto_items[]     {code, severity, task_id?, detail}，code ∈ dep_cycle / no_acceptance / oversize
data.challenges[]     {challenge_id, description, impact_ref, consequence, severity, category, verdict}
data.rework_target    P4 | P3 | HUMAN（不通过时的回炉去向，A 的 G_P5_REWORK_* 直接读）
```

两份产物都走 `make_artifact`，`meta` 五字段齐全（`schema_version` / `run_id` / `stage` / `produced_by` / `created_at`），
`produced_by` 固定为 `conflict_review`。

### 7.4 已验证

拿 A 分支的 `validate_stage_result`、`gate_check.score_pass`、`challenge_category_is`、`has_escalated_conflict`
直接验过一遍：P3 / P5 产物校验均无错误，`score_pass` 正确读到 `total=85.5, stddev=5.12, veto=True`，
`challenge_category_is` 正确按 `category` 分流出 `solution → P4`。
