# 多智能体任务分解与编排系统（第 10 组）

> 课堂作业 2：**设计规格（design spec）→ PM Agent 开发任务分解 → 编排器生成任务编排 → 可视化**。
> 系统做成一套通用可定制的 SE 工作流：换一份设计规格（"我要做微信"或"我要做小红书"），同一套引擎重新跑出另一份任务清单与排期，具体产品只作为测试案例。

## 这条流程做什么

1. **P1 设计规格进场**：人给一份 `design_spec`（产品、范围、功能点、约束、模块），P1 只做规范化与校验，缺字段退回，不由模型猜。
2. **P2–P5 把口径吵清楚**：模块划分 → 分解口径讨论与裁决 → 三套分解方案（按模块 / 按里程碑 / 按风险）→ 对抗式评审。
3. **P6 WBS 定稿**：PM Agent 产出开发任务清单 `dev_task_list.json`，每张卡八项字段（任务号、模块、标题、依赖、工时、负责人、验收口径、来源引用）。
4. **P7 排期与冻结**：编排器纯代码排期——查环、分层定波次、算关键路径、处理同一负责人在同一波的资源冲突，产出 `schedule.json`；人签字后基线冻结。
5. **可视化**：七视图（时间轴 / 讨论 / 冲突 / 门禁预算 / 依赖甘特 / 覆盖矩阵 / 重放）导出静态页，断网可看。

## 模块划分与状态

状态只用三态（W2 口径，F 维护）：

- **已实现**：代码/设计文档在库内，自检或单测通过；证据挂源码、用例或文档路径。
- **已跑通**：`examples/results/` 下有该模块的真实运行产物；证据挂具体 JSON 路径。
- **已交付**：产物已合入 `main`、对应门验收通过且可从事件流与产物文件追溯；证据挂门验收事件与实际产物。

没有产物的一律降级，不写无法核对的模糊状态词。

| 阶段 | 负责 | 代码 | 设计文档 | 状态 | 产物路径 / 证据 |
|---|---|---|---|---|---|
| P1 规格规范化 / P2–P7 编排 / 排期 | A | `src/scheduler/` | `docs/architecture.md` | 已跑通 | `examples/results/REAL/artifacts/schedule.json` |
| P2 分类召集 / P3 三段式讨论与防趋同 | B | `src/agents/` | `docs/role-discussion.md` | 已跑通 | `examples/results/REAL/artifacts/discussion_minutes.json` |
| P4 三套方案生成与对比 | C | `src/solution_gen/` | `docs/multi-solution.md` | 已跑通 | `examples/results/REAL/artifacts/solution_comparison.json` |
| P5 冲突裁决与五维打分 | D | `src/conflict_review/` | `docs/review-conflict.md` | 已跑通 | `examples/results/REAL/artifacts/review_sheet.json` |
| P6 任务卡、需求条目与覆盖矩阵 | E | `src/req_builder/` | `docs/require-model.md` | 已跑通 | `examples/results/REAL/artifacts/dev_task_list.json` |
| 执行可视化（七视图） | E | `src/req_builder/viz/` | `docs/visualization.md` | 已实现 | `src/req_builder/viz/`（页面未入库，W7 待补） |
| P7 确认单、基线与变更 | F | `src/baseline_mgr/` | `docs/baseline-change.md` | 已跑通 | `examples/results/REAL/artifacts/baseline.json` |

### 交付物验收状态（W1–W7 / O1–O3）

| 编号 | 交付物 | 主责 | 状态 | 产物路径 / 证据 | 缺口 |
|---|---|---|---|---|---|
| W1 | 产物入库 | A | 已跑通 | `examples/results/DEMO/artifacts/`、`examples/results/REAL/artifacts/` | 当前产物在 `artifacts/` 子目录，W1 要求的 15 份平铺路径待 A 确认 |
| W2 | README 状态表重写 | F | 已跑通 | 本文件 + `examples/results/REAL/artifacts/baseline.json` | 合入 `main` 后升级为已交付 |
| W3 | P4 真实现 | C | 已跑通 | `examples/results/REAL/artifacts/solution_comparison.json` | 字段 `feature_items/solutions/rows[].options[].cost_estimate/tasks[]` 已核对 |
| W4 | P3 真实产出 | B | 已跑通 | `examples/results/REAL/artifacts/discussion_minutes.json`、`conflict_register.json` | 4 条发言 / 4 票 / 4 轮问答已在产物内核对 |
| W5 | P7 编排结果补全 | A | 已跑通 | `examples/results/REAL/artifacts/schedule.json` | 波次、关键路径、资源冲突、总工期字段齐备 |
| W6 | 真实链路一次跑通 | A | 已实现 | `examples/results/REAL/events.jsonl` | 事件流无 `model.call`，W6 验收未达标 |
| W7 | 演示页 V3 | E | 已实现 | `src/req_builder/viz/` | 仓库内无 `多智能体工作流-v3.html`，待 E 生成 |
| O1 | 观测埋点与数据模型 | A + C | 已实现 | `src/scheduler/events.py` | 缺 `obs-schema.json` 与 `spans.jsonl` |
| O2 | 三视图 + 三动作 | E + A | 已实现 | `src/req_builder/viz/` | 缺健康条/瀑布/告警页与 fork 运行 |
| O3 | 判据与检出表 | D | 已实现 | `src/conflict_review/rules.py` | 缺 `signals.yaml`、故障注入检出表与 MTTD |

设计总览见 [`docs/README-设计总览.md`](docs/README-设计总览.md)。

## 目录

```
src/
  scheduler/       编排引擎：状态机、闸门、预算、排期、CLI（A）
  agents/          角色与讨论（B）
  solution_gen/    三套分解与比选（C）
  conflict_review/ 评审、打分、裁决（D）
  req_builder/     任务卡、覆盖矩阵、可视化渲染（E）
  baseline_mgr/    确认单、基线、变更（F）
docs/                各模块设计文档
examples/            设计规格示例 + results/ 基线运行产物（入库，clone 可见）
  results/DEMO/      mock 全流程产物
  results/REAL/      real 全流程产物（含 F 的确认单、意见处理表、基线）
  results/compare.html  跨 run 对比页（F，自包含、断网可看）
tests/               用例
runs/                临时运行产物，不进版本库
```

## 快速开始

先安装依赖（Python 3.10+）：

```bash
python -m pip install -r requirements.txt
```

### 1. mock 全流程（不调模型，验状态机与闸门）

```bash
python -m src.scheduler.cli --run-root examples/results start --run-id DEMO --design-spec examples/design_spec_wechat.json
python -m src.scheduler.cli --run-root examples/results run --run-id DEMO
python -m src.scheduler.cli --run-root examples/results human --run-id DEMO --action sign --reason "任务分解与排期可接受（DEMO 基线）"
python -m src.scheduler.cli --run-root examples/results resume --run-id DEMO
python -m src.scheduler.cli selfcheck
```

### 2. real 全流程（真实 B/C/D/E/F + 离线脚本化模型）

```bash
python -m src.scheduler.cli --run-root examples/results start --run-id REAL --design-spec examples/design_spec_wechat.json \
  --provider real --llm-provider scripted
python -m src.scheduler.cli --run-root examples/results run --run-id REAL
python -m src.scheduler.cli --run-root examples/results human --run-id REAL --action sign --reason "任务分解与排期可接受（REAL 基线）"
python -m src.scheduler.cli --run-root examples/results resume --run-id REAL
PYTHONPATH=.:src python -m req_builder.viz --run-dir examples/results/REAL --out examples/results/REAL/viz/index.html
python -m src.baseline_mgr.compare --run-root examples/results --out examples/results/compare.html
```

`scripted` 只提供结构合法的 JSON 模型响应，不联网、不代表模型质量；路线、闸门、冲突裁决、覆盖矩阵与排期仍由各组真实代码计算。接真实模型时把 `--llm-provider` 换成 `agents` 并配置 `B_MODEL_ENDPOINT`/`B_MODEL_TOKEN`。

### 3. 多组数据单页切换

先用不同 `--run-id` 跑出多组结果，再生成一个带运行选择器的总页面：

```bash
export PYTHONPATH=.:src

# 指定若干运行实例
python -m req_builder.viz --run-root runs --run-ids REAL,VIZREAL

# 或自动扫描 runs/ 下所有含 events.jsonl 的运行目录
python -m req_builder.viz --run-root runs --all-runs

# 自定义输出路径
python -m req_builder.viz --run-root runs --all-runs --out runs/all.html
```

默认输出 `runs/index.html`。打开后顶部下拉框切换运行实例；每组运行仍显示原来的完整七视图，并各自保留独立的进度条回放能力。页面自包含、断网可看。

F 的 [`examples/results/compare.html`](examples/results/compare.html) 是跨 run 的数字对比页：指标直接读 `state.json`、`artifacts/*.json`、`events.jsonl`，列出两组运行的产物数、任务数、工期、覆盖率、模型调用与基线状态，并可点开每个 JSON。生成命令见上一节最后一行；重跑会覆盖同名文件。

各模块单独验证：

```bash
export PYTHONPATH=.:src
python -m req_builder.demo                         # E：任务卡 + 七视图样例
python -m conflict_review.test_rules                # D：12 条规则用例
python -m unittest discover -s src/agents/tests -v  # B：37 条用例
python -m unittest discover -s tests -v             # E 与真实模块集成用例
python -m src.scheduler.cli selfcheck               # A：17 条编排器用例
```

Windows PowerShell：先 `$env:PYTHONPATH=".;src"; $env:PYTHONIOENCODING="utf-8"`，命令同上。

依赖：Python 3.10+、PyYAML（编排器读取 `flow.yaml` / `gate_rule.yaml` 用）。

## 协作约定

- `main` 受保护，特性分支命名 `feature/<组员代号>-<模块名>`，PR 需一名组员评审；
- 临时运行产物 `runs/` 不进版本库；基线产物放 `examples/results/<run_id>/`，按要求入库供 clone 后直接核对；
- 产物统一走 `{meta, data, extensions}` 信封，`stage` 与 `produced_by` 由生产模块填写；
- 五个模块一律在 `src/<模块>/__init__.py` 暴露 `run(ctx) -> StageResult`，供编排器加载。

## 文档

- 设计总览（入口）：[`docs/README-设计总览.md`](docs/README-设计总览.md)
- 架构与数据契约：[`docs/architecture.md`](docs/architecture.md)
- 其余模块文档：`docs/role-discussion.md`、`docs/multi-solution.md`、`docs/review-conflict.md`、`docs/require-model.md`、`docs/visualization.md`、`docs/baseline-change.md`
- F 的项目内产物：[`examples/results/compare.html`](examples/results/compare.html)（跨 run 对比页）、`src/baseline_mgr/compare.py`（生成器）。答辩、审计、过程表与录屏脚本在桌面交付包，不入库。
