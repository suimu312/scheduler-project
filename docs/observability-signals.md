# 观测判据层与故障注入（D 模块）

> 对应交付项 **O3 判据与检出表**｜维护：D｜配套文件：`src/observability/signals.yaml`
> 定位：作业3 里唯一能被量化打分的一块——不靠形容词，靠"注入了 N 个故障，检出 M 个，平均几秒发现"。

---

## 一、前置问题：`model.call` 埋点在，但 mock 模式下一条都不发

先纠正一个容易误判的地方：**`model.call` 不是没有埋点，而是没被触发。**

- `src/scheduler/statemachine.py:_on_model_call()` 会在 LLMGateway 真正发起调用后发一条
  `model.call`，同时配一条 `model` span——埋点本身是完整的；
- `src/agents/evidence.py` 还额外分了口径：真实调用发 `model.call`，mock 调用发 `fixture.call`。

问题在于现在入库的 run（`DEMO-WX` / `MOCKD`）**全是 mock 模式**，所以 `events.jsonl` 里
`model.call` 是 0 条。实测 `DEMO-WX` 的 52 条事件里一共 14 种类型，没有一条是 `model.call`。

所以 **W6 的验收标准不用改，要改的是跑法**：用真模型跑一次（`e2ef1d4` 已接 ark），
`model.call` 自然就出现。但有两件事必须同时说清楚，否则信号会"看起来能算、其实算不对"：

1. **OBS-01 现在只能算调用次数余量。** `_on_model_call()` 的 detail 里只有
   `agent_id / stage / provider / latency_ms / ok / error`，**没有 token 数**。
   要做成 token 预算，得在这个出口补 `prompt_tokens` / `completion_tokens`。
2. **OBS-03 算不出来，卡点不在 `model.call`。** 它需要 `task.dispatched` / `task.completed`
   来划"一张卡的一次尝试"，而这两个事件只有 `src/req_builder/demo.py` 会造，
   调度器自己从不发（见缺陷 6）。

答辩时被问"你的成本指标从哪来"，答"从预算计数"和答"从 token 数"是两套体系，别混着说。

---

## 二、三条不肯让步的口径（与 P5 的评审口径一致）

1. **能反算的就不拍阈值。** 覆盖空缺、依赖成环、回炉次数、打分标准差，全部由产物算出；
   只有"阶段停留多久算慢""重试几次算多"这类没有历史基线的，才用经验阈值，且必须在 yaml 里标注来源（历史 P95 / 经验值 / 人给定基准）。
2. **算不出来不补。** 与 P5 四维打分同一条纪律：证据不全就标 `unavailable`，不猜、不补、不美化。
3. **一条信号三段式。** 证据（从哪取）+ 判定式（可复算）+ 处置（退哪层、给谁、建议什么动作）。
   缺任何一段，这条信号就是空阈值，评审一问就穿。

三个等级与动作的对应关系固定：

| 等级 | 含义 | 系统动作 |
|---|---|---|
| info | 仅提示 | 记一条事件，界面标灰点 |
| warn | 需人注意 | 告警列表置顶，不阻断流程 |
| block | 必须干预 | 进等待人态，未处置不许进下一阶段 |

---

## 三、十条信号一览

完整定义见 `src/observability/signals.yaml`，这里只列"看哪、判什么、怎么办"。

判据不是纸面约定：`src/observability/signals.py` 是这份 yaml 的**可执行副本**，
其中 OBS-01 / OBS-03 / OBS-06 三条已写成纯函数（只读事件流与产物、不调模型、无副作用），
五例故障注入就是拿这三个函数判的。分工边界一句话：`src/scheduler/obs.py` 负责写（埋点），
`src/observability/signals.py` 负责判。

| ID | 信号 | 证据来源 | 判定要点 | 等级 | 处置 |
|---|---|---|---|---|---|
| OBS-01 | 预算余量 | `budget.charged` 末条 / `global_limit` | 余量 < 20% 且未到 P7 | warn / block | 降并行、提前进 P7、或转人 |
| OBS-02 | 阶段停留超时 | `stage.enter` / `stage.exit` 的 ts 差 | > 2× 历史 P95 | warn | 下钻该阶段最长 span |
| OBS-03 | 单卡重试超限 | 卡内 `model.call` 次数 | > 3 次 | block | 改参数后单卡重试 |
| OBS-04 | 依赖成环 | `schedule.errors` 的 `E_DEP_CYCLE` | 命中即报 | block | 退 P6，高亮环上节点 |
| OBS-05 | 依赖死锁 | `run.blocked` reason=deadlock | ready 空但未结束 | block | 转人，导出 ready 集合 |
| OBS-06 | 发言趋同 | `guard.signal` 的 similarity | > 0.85 判无效；单轮 ≥2 次 | warn | 灰蒙层 + 建议插质询 |
| OBS-07 | 角色沉默 | 按 `agent_id` 数连续空轮 | ≥ 2 轮 | warn | 高亮 + 强制补发言 |
| OBS-08 | 覆盖空缺 | `coverage_matrix.gap_count` | > 0 | block | 留白呈现，指出缺的 FEA |
| OBS-09 | 关键路径漂移 | 实际完成波次 vs `schedule.waves` | 落后 ≥ 1 波 | warn | 甘特高亮漂移条 |

实验性：OBS-10 并发失真（`event_id` 顺序与 `ts` 顺序不一致的区段）——接真模型、有真实耗时后再启用。

---

## 四、故障注入用例（答辩材料就是这张表的填写版）

原则：**注入的故障必须有唯一正确的检出信号**，否则测不出漏报；
每个用例跑完要同时记"是否检出"和"有没有误报别的信号"。

| 编号 | 注入什么 | 怎么注入（不改业务代码） | 期望信号 | 期望发现时机 | 通过判据 |
|---|---|---|---|---|---|
| FI-01 | 依赖成环 | 在 `dev_task_list` 里把 DEV-002 的 `depends_on` 指向 DEV-004，而 004 原本依赖 002 | OBS-04 | P7 校验阶段，即刻 | 15 秒内 block，且指出环上任务号 |
| FI-02 | 预算打满 | 把 `ledger.global_limit` 调到已用次数 + 1，再跑一轮 | OBS-01 | 该轮末尾 | 出现 warn 后 1 轮内变 block，且不再发起新调用 |
| FI-03 | 单卡重试 | 让某卡的校验器恒失败（临时把 `acceptance` 判空） | OBS-03 | 第 4 次调用后 | 指出具体 task_id，而非只说"有重试" |
| FI-04 | 覆盖空缺 | 从 `dev_task_list` 抽掉承接某个来源的**全部**任务卡 | OBS-08 | P6 闸门前 | block，且点开能定位到具体来源 id |
| FI-05 | 发言趋同 | 给两个角色传同一份上下文切片，制造高度相似陈述 | OBS-06 | 并行陈述后 | 相似度数值可见，且该块被标为无效 |

注：五例现在**全部可跑**，但注入层次不同，答辩时要分清——

- **产物层注入**（FI-01 / FI-04）：改内存里的任务卡集合，判据用的是仓库里现成的
  `find_cycle` 与 `compute_coverage`，产物不动一行。
- **配置层注入**（FI-02）：改内存里的账本限额（`global_limit` 压到已用 + 1），
  再模拟跑一轮，验 warn → block 的升级路径。
- **fixture 注入**（FI-03 / FI-05）：FI-03 要用的 `task.dispatched` / `task.completed`
  **调度器从不发**（只有 demo 脚本会造），所以只能用 fixture 拼一段事件流，
  证明"埋点一到位这条信号就能抓到"；FI-05 用的是产物里现成的 4 条发言文本。
  这不是真实链路检出，汇报时必须这么讲，别混成"线上已验证"。

---

## 五、检出记录表（跑完就填，填完就是 O3 的验收物）

命令：`python tests/fault_injection/inject.py`
（默认读入库的 `examples/results/DEMO-WX/`，clone 后不跑任何前置步骤就能复现；
换成自己本地的 run 用 `--run runs/<run_id>`。结果同时写到 `tests/fault_injection/last_run.json`，
答辩时直接拿这份原始数据）

| 用例 | 是否检出 | MTTD（判定耗时） | 命中的信号 | 级别 | 有无误报 | 依据 |
|---|---|---|---|---|---|---|
| FI-01 依赖成环 | 是 | 0.4 ms | OBS-04 | block | 无 | 环 2 张：DEV-001 → DEV-002 → DEV-001（`find_cycle` 另把下游 6 张也算进环，见缺陷 4） |
| FI-02 预算打满 | 是 | 0.1 ms | OBS-01 | warn → block | 无 | 把 `global_limit` 压到 17（已用 16）：该轮末尾余量 5.9% 报 warn；再跑一轮 17/17 余量 0% 转 block |
| FI-03 单卡重试 | 是 | 0.1 ms | OBS-03 | block | 无 | 只报 DEV-001（5 次调用，retry=4）；同批 DEV-002 的 retry=2 未被误报 |
| FI-04 覆盖空缺 | 是 | 0.5 ms | OBS-08 | block | 无 | 抽掉承接 FEA-001 的 4 张卡后，未覆盖来源由 1 变 4（CON-001/002/003、FEA-001） |
| FI-05 发言趋同 | 是 | 0.2 ms | OBS-06 | warn | 无 | 两对陈述相似度 1.00 ≥ 阈值 0.85，4 条发言判无效（A1↔A4、A2↔A3） |

当前口径：**注入 5 例，检出 5 例（100%），误报 0 条，单例最慢 0.5 ms。**

三个必须主动说明的口径边界，否则这张表会被当成吹牛：

1. **MTTD 只算判据层耗时，不含采集延迟。** 真实链路上"从故障发生到人看到"还要加埋点落盘与
   界面刷新的时间，那部分归 O1/O2，不在这张表里。
2. **FI-03 / FI-05 是 fixture 注入，不是线上检出。** 依据是"埋点与固定 fixture 到位后能抓到"，
   不是"真实模型跑出过这个故障"。
3. **基线是干净的。** 不注入时三条判据全部返回未命中（`last_run.json` 里 `baseline.signals` 为空），
   所以"零误报"是有对照组的结果，不是没测。

---

## 六、跑这五例时暴露的六个问题（比检出表更重要）

前四条属于"结果不可复算"，后两条属于"埋点口径"，都是评审最容易问穿的地方，
建议直接在群里@对应人。

### 缺陷 1：`dev_task_list.json` 落盘后来源全丢（E 修，2 行代码）

`DevTask.to_dict()` 写出的是 `feature_refs` / `requirement_refs`，而 `DevTask.from_dict()` 只读
`source_refs` / `sources`。结果是：**任何下游重新加载这张表，任务的来源引用全部为空**。
实测影响：重算覆盖矩阵时覆盖率从 100% 掉到 0%，缺口从 0 变 21。

修法（二选一）：
- `from_dict` 里兼容 `feature_refs` / `requirement_refs`（推荐，不破坏已有产物）；
- 或 `to_dict` 同时写 `source_refs`。

> 我在 `tests/fault_injection/inject.py` 里加了临时兼容层 `_patch_refs()`，
> E 修好后这个函数可以整段删掉——**别把它当正式方案留在仓库里**。

### 缺陷 2：`coverage_matrix.json` 与 `dev_task_list.json` 不自洽（E 修）

产物里的 `coverage_matrix.columns` 是 `REQ-001 / REQ-002 / REQ-003`，
而同一目录的 `dev_task_list.tasks[].task_id` 是 `DEV-001 … DEV-008`。
两份产物不是同一轮任务集算出来的，所以**存下来的"gap_count=0"没法复算**。

用当前任务集重算（`examples/results/DEMO-WX`）是：**任务 8 张、缺口 15、覆盖率 70%**，
其中 3 个来源空缺、11 张卡的验收标准不可验证、1 张卡粒度不合规。
如果这个数字是真的，那 G_P6_P7（要求空缺为零）本来就不该放行。

### 缺陷 3：约束类来源永远空缺（C + E 对齐口径）

15 个缺口里，`CON-001 / CON-002 / CON-003` 三个是设计规格的**约束**，
但任务卡只引用 `feature_refs` 和 `requirement_refs`，**从来没人引用约束**。
要么任务卡增加 `constraint_refs`，要么明确"约束类来源不参与分母"并写进文档——
现在这种状态会让人以为覆盖率天生就只有 70%。

### 缺陷 4（顺带）：`find_cycle` 报的"环上任务"范围偏大（E 修）

`validators.find_cycle` 用的是 Kahn 残余法，返回的是"入度仍大于 0 的全部节点"，
也就是**环 + 环下游的整条链**。实测注入一个 2 张卡的环，它报了 8 张。

判定有没有环没错，但产物里若有"环上任务"这个字段，评审一问就露。
修法：对残余节点再做一次 DFS 取回溯边，只留最小环；或字段改名为 `blocked_tasks` 并说明口径。

### 缺陷 5：「发言趋同」存在两套阈值，同一段话会有两个结论（B + D 对齐）

- `src/agents/core.py:estimate_similarity` 判 `flagged` 用的门槛是 **字面相似度 ≥ 0.70 或语义 ≥ 0.85**；
- `signals.yaml` 的 OBS-06 与 `config.json` 的 `limits.similarity_threshold` 都写 **0.85**。

于是同一对发言可能出现"agents 模块判为趋同、判据层判为不算"。现在注入的是完全相同的两段话
（相似度 1.00），两套口径都会命中，所以这个坑被盖住了；一旦真实模型跑出 0.72 这种边缘值，
两边就会给出不同结论。

修法：`estimate_similarity` 不要再写死 0.70，统一读 `limits.similarity_threshold`；
或者明确分层——0.70 只作"疑似"提示（info），0.85 才判"无效"（warn），并把这句话写进 yaml。

另注：现网没有 embedding 服务，`estimate_similarity` 走的是**字面 bigram 降级口径**
（返回值里 `degraded: true`）。0.85 这个阈值本来是按语义相似度定的，套到字面口径上会偏严，
答辩时被问"你这相似度怎么算的"，要主动说清楚是降级口径、不是向量余弦。

### 缺陷 6：调度器从不发 `task.dispatched` / `task.completed`（A 的埋点缺口）

全仓搜索的结果：这两个事件只在 `src/req_builder/demo.py:273/277` 被造出来（演示用），
`src/scheduler/` 下**一次都没发过**。后果有两层：

1. **OBS-03「单卡重试超限」在真实链路上永远判不出来。** 它的判定式是"同一 `task_id` 在
   dispatched 与 completed 之间的 `model.call` 次数"，没有起止边界就无从计数；
2. **任务级的时间与重试在事件流里完全不可见。** 现在想知道"哪张卡跑了几次"，只能去翻
   `spans.jsonl` 里有没有 `kind=model` 的 span，而 span 本身又不带 `task_id`。

修法（A，两处 emit 即可）：派发任务卡时发 `task.dispatched`（带 `task_id` / `module_id` /
`wave`），完成或失败时发 `task.completed` / `task.failed`（带 `task_id` / `status` / `attempt`）。
补上之后 OBS-03 的 fixture 注入就能换成真实链路检出。

---

## 七、汇总口径（汇报时直接念这一句）

> 判据层定义了 10 条信号，全部可复算、不调模型，其中 3 条已写成可执行函数；已用 5 例故障注入验证，
> 5 例全部检出、零误报、单例判定耗时 0.5 ms 以内；同时在验证过程中发现 6 处口径或埋点问题，
> 已定位到具体函数与修法。
