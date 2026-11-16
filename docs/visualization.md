# 执行可视化：七个视图与渲染函数约定

> 负责人：张艺津（E · 需求分析师）｜对应阶段：全程监控 + P7 导出｜代码：`src/req_builder/viz/`
>
> 分支：`feature/E-req-builder`｜文档版本：v1.0（对齐《设计方案修订版 V2》第 6 章与 architecture.md §10）｜2026-11-16

---

## 1. 一条原则：界面是事件的投影

渲染函数的输入只有**事件数组 + 截止序号**，输出是界面，本身是纯函数：

```python
render_views(events, until_seq=None, artifacts=None) -> str   # 返回自包含 HTML
```

由此得到三个必然结果，也是本模块不接受的设计变更：

| 要求 | 为什么 |
|---|---|
| 实时与回放共用同一套代码 | "实时"只是截止序号取最新值，"回放"由进度条或 `--until` 给；两套代码必然出现两份真相 |
| 界面不持有业务状态 | 如果页面自己记"我正显示第 2 轮"，出问题时无法判断是引擎错还是界面错 |
| 不自己重算依赖与工期 | 图上的顺序必须等于 `schedule.json` 里的顺序，否则答辩时"图与数对不上" |

**界面不得回写运行状态。** 所有人工动作先由 A 落成 `human.decision` 事件，再进编排器；E 只负责把事件画出来。这条是架构约束（§10.3），不是风格偏好。

---

## 2. 输入契约

| 输入 | 来源 | 本模块怎么用 |
|---|---|---|
| `runs/<run_id>/events.jsonl` | A 追加写 | 唯一事实来源；按 `seq` 单调递增重放，缺 `seq` 时按行号补 |
| `artifacts/schedule.json` | A（P7 排期） | V5 的唯一数据源：波次、起止天、关键路径、并行度 |
| `artifacts/task_coverage.json` | E（P6） | V6 的行（规格来源）与列（非 Wont 任务） |
| `artifacts/dev_task_list.json` | E（P6） | 兜底：读不到 `schedule.json` 时给出示意甘特，并在页面上标注"兜底" |

**事件字段的容忍策略。** docx 表 17 是按字段名列信号（`agent_id`、`立场`、`字数`），而 architecture §10.2 的 `RunEvent` 只有 `payload_ref`。两种写法都可能出现，读入时统一归一：`payload` 内联对象优先，平铺在行首的字段次之，信封字段最后。渲染器只读事件，不回溯产物文件。

---

## 3. 七个视图

| 视图 | 回答什么 | 主区域 | 实现 |
|---|---|---|---|
| V1 运行时间轴 | 现在跑到哪了、谁在说话 | 横轴为事件序号，泳道为编排器与 A1–A6；发言块长度按字数 | `views/v1_timeline.py` |
| V2 讨论看板 | 三段各自进行到哪 | 三栏：并行陈述 / 定向质询 / 投票 | `views/v2_discussion.py` |
| V3 冲突矩阵 | 哪些议题未裁决、谁与谁不一致 | 行是议题，列是角色，格是立场 + 证据圆点 | `views/v3_conflict.py` |
| V4 闸门灯与预算条 | 能不能过、还剩几次调用 | 顶部固定横条，G1–G7 灯 + `global_used/limit` | `views/v4_gate_budget.py` |
| V5 任务依赖图与甘特 | 任务谁先谁后、哪些并行、哪条链最紧 | 左依赖图（按波次分层）+ 右甘特（横轴为天）同屏 | `views/v5_dag_gantt.py` |
| V6 覆盖热图 | 设计规格的哪条要求没被任务覆盖 | 行是规格来源，列是非 Wont 任务，空缺留白 | `views/v6_coverage.py` |
| V7 回放控制 | 那一刻到底发生了什么 | 底部进度条、单步、跳到下一异常 | `views/v7_replay.py` + `render.py` 内联脚本 |

**V5 是硬要求（V2 §6.3）。** 依赖图解决"看得懂结构"，甘特解决"看得懂时间"，两者共用同一份 `schedule.json`：节点列坐标按波次、条长按工时、关键路径加粗描边、并行度超上限的波次在右上方标注"顺延 N 张卡"。只画甘特不画依赖图，人看不出为什么这两张卡不能并行；只画依赖图不画甘特，人感受不到工期压力。

---

## 4. 信号与字段的映射

这是实现与事件契约之间的唯一桥梁，改字段名必须同时改这张表。

| 事件 | 关键字段 | 界面元素 | 视图 |
|---|---|---|---|
| `run.start` | `run_id`、规格长度 | 时间轴起点、初始状态 | V1 |
| `stage.enter` | `stage`、`from_stage` | 泳道分隔线、阶段标题 | V1、V2 |
| `agent.on_stage` | `agent_id`、`family` | 泳道激活、角色灯亮 | V1 |
| `agent.statement` | `agent_id`、`stance`、`word_count` | 发言块（长度按字数） | V1、V2 |
| `agent.query` | `from`、`to`、`quote` | 两泳道之间的连线 | V1、V2 |
| `agent.vote` | `agent_id`、`vote`、`reason` | 票型徽标 | V2、V3 |
| `conflict.detected` | `conflict_id`、`topic_id`、`stances[]` | 矩阵新增行与立场格 | V3 |
| `conflict.resolved` | `resolution.type`、`resolution.winner` | 裁决结果列 | V3 |
| `task.dispatched` | `task_id`、`module_id`、`wave_no` | 甘特条起点、节点高亮 | V5 |
| `task.completed` | `task_id`、`estimate_days` | 甘特条结束、节点转完成色 | V5 |
| `gate.evaluated` | `gate_id`、`action` | 顶部闸门灯 | V4 |
| `budget.charged` | `global_used`、`limit` | 预算条 | V4 |
| `guard.signal` | `value`（相似度）、`invalid_block` | 灰蒙层 + 数值标注 | V1、V7 |
| `human.decision` | `action`、`scope`、`decided_by` | 决策标记与审计行 | V1、V7 |

同一闸门多次判定取最后一次（例如 P5 回炉后重判），否则 V4 会停在旧结论上。

---

## 5. 三处必须做对的细节

1. **有证据必须可见**：V3 的格子右上角点一个小圆点。没有这个点，人看不懂"为什么这么判"；有证据的一方胜出是本系统的核心裁决机制（一级证据优先）。
2. **空缺用留白表达，不涂满红色**：V6 的空缺行只用斜纹底 + 左侧红色虚线 + "空缺"二字。满屏红会被无视，而覆盖缺口恰恰是最该被看见的东西。
3. **异常必须可见**：发言趋同的块叠灰色蒙层并标注相似度数值（如 `相似度 0.88`）。这是系统里唯一"否定智能体输出"的地方，不标注会被当成程序错误。

---

## 6. 渲染函数约定

```python
# 每个视图模块统一签名
render(window: ReplayWindow, artifacts: dict, width: float) -> tuple[str, float]
#   window    : 已按 until_seq 截断的事件窗口
#   artifacts : {"schedule": 信封, "task_coverage": 信封, "dev_task_list": 信封, ...}
#   width     : 视图可用宽度（双栏时传半宽，避免 viewBox 被整体缩放成看不清的小字）
#   返回      : (SVG 片段, 高度)
```

| 约定 | 说明 |
|---|---|
| 不读文件 | 视图只吃内存对象，读盘统一在 `cli.py`（`load_events` / `load_artifacts`） |
| 文本必须转义 | 一切进入 SVG 的文本走 `svg.esc()`；事件里出现 `<script>` 也不能变成可执行内容 |
| 不引入前端框架 | 纯 SVG + 内联 CSS；页面自包含，断网双击可看，可直接截图进答辩材料 |
| 回放靠 `data-seq` | 每个图元带 `data-seq`，进度条只按截止序号隐藏，不改数据、不重算 |
| 宽度自适应 | 传入宽度决定列宽与字宽预估，双栏/单栏都能读 |

规模参考：`python -m req_builder.demo` 生成 67 条事件、8 张卡、7.5 天工期，页面约 240 KB，单文件无外部依赖。

---

## 7. 落地方式

命令行优先，图形界面与命令行共用同一套渲染输入：

```bash
python -m req_builder.viz --run-dir runs/R0001                 # 实时视图（截止序号=最新）
python -m req_builder.viz --run-dir runs/R0001 --until 36       # 回放到第 36 条
python -m req_builder.viz --events runs/R0001/events.jsonl --artifacts runs/R0001/artifacts
```

输出默认 `<run-dir>/viz/index.html`。页面四个区块：阶段泳道时间轴、冲突矩阵、任务依赖图与甘特、覆盖热图；V4 常驻顶部、V7 常驻底部（与 docx 图 1 的线框一致）。

`runs/` 不进版本库：演示数据由 `demo.py` 现场生成，避免中间状态污染提交记录。

---

## 8. 与人的分工

| 动作 | 谁做 | 落点 |
|---|---|---|
| 暂停 / 恢复 / 批注 / 打回 / 签署 / 裁决 / 中止 | A 落事件 | `human.decision`（含 `action`、`scope`） |
| 把事件画成界面 | E | 本模块，只读 |
| 审计行（谁在何时干预、影响了哪些卡） | E 渲染、A 提供事件 | V1 的 H 标记 + V7 的异常跳转 |

裁决必须带冲突号、批注必须定位到具体任务或冲突，否则等于给人开了"随时改需求"的后门；E 侧只做展示，不做校验。

---

## 9. 边界与已知限制（写清而不是假装没有）

1. **T8（同模块同文件不跨波并行）不在 P6 判定**：这条依赖 `schedule.json` 的波次，P6 阶段拿不到，由 A 在 P7 校验；E 的校验器把该项置为 `True` 并在文档注明，不假装校验过。
2. **V2 只画 P3 的三段式**：P5 的五维打分是评审行为，不是 P3 的"讨论三段"，混在同一块里会让人误以为协议只跑一次；P5 的打分事件在 V1/V3 可见。
3. **读不到 `schedule.json` 时 V5 退化为兜底示意**：按任务顺序串行排天，并在图上明写"未读到 schedule.json，当前为按任务顺序的兜底示意"，不臆造波次。
4. **并行度上限来自 `schedule.json` 的 `parallel_limit`**，E 不重算；缺失时按 3 显示。
5. **不生成 PNG/PDF**：答辩截图由人完成；导出图片属于交付环节，不属于渲染函数的职责。

---

## 10. 自检方式

```bash
python -m req_builder.demo                 # 生成 runs/R0001 并渲染，打印覆盖率与门禁数据
python -m unittest discover -s tests -v    # 30 条用例：优先级、八条规则、覆盖、事件、渲染、CLI
```

自检要看的四组数：任务数与总人天、优先级分布、覆盖率与空缺数、`G_P6_P7` 数据面（`gap_count` 与 `all_have_acceptance`）。四组数都对得上，这一版可视化才算可用。
