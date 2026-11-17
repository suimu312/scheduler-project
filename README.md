# 多智能体任务分解与编排系统（第 10 组）

> 课堂作业 2：**设计规格（design spec）→ PM Agent 开发任务分解 → 编排器生成任务编排 → 可视化**。
> 系统做成一套通用可定制的 SE 工作流：换一份设计规格（"我要做微信"或"我要做小红书"），同一套引擎重新跑出另一份任务清单与排期，具体产品只作为测试案例。

## 这条流程做什么

1. **P1 设计规格进场**：人给一份 `design_spec`（产品、范围、功能点、约束、模块），P1 只做规范化与校验，缺字段退回，不由模型猜。
2. **P2–P5 把口径吵清楚**：模块划分 → 分解口径讨论与裁决 → 三套分解方案（按模块 / 按里程碑 / 按风险）→ 对抗式评审。
3. **P6 WBS 定稿**：PM Agent 产出开发任务清单 `dev_task_list.json`，每张卡八项字段（任务号、模块、标题、依赖、工时、负责人、验收口径、来源引用）。
4. **P7 排期与冻结**：编排器纯代码排期——查环、分层定波次、算关键路径、处理同一负责人在同一波的资源冲突，产出 `schedule.json`；人签字后基线冻结。
5. **可视化**：七视图（时间轴 / 讨论 / 冲突 / 门禁预算 / 依赖甘特 / 覆盖矩阵 / 重放）导出静态页，断网可看。

## 模块划分

| 阶段 | 负责 | 代码 | 设计文档 | 状态 |
|---|---|---|---|---|
| P1 规格输入 / P2 角色讨论 / P7 排期编排 | A | `src/scheduler/` | `docs/architecture.md` | ✅ 引擎跑通（mock 全流程） |
| P3 三段式讨论与防趋同 | B | `src/agents/` | `docs/role-discussion.md` | ⚠️ 逻辑完成，缺 `run(ctx)` 入口 |
| P4 三套方案生成与对比 | C | `src/solution_gen/` | `docs/multi-solution.md` | ⬜ 待实现 |
| P5 冲突裁决与四维评审（成本仅估算） | D | `src/conflict_review/` | `docs/review-conflict.md` | ✅ 已并入 main，15 条用例；评审依据可从产物反算 |
| P6 任务卡生成与覆盖矩阵 | E | `src/req_builder/` | `docs/require-model.md` | ✅ 已并入 main，30 条用例 |
| 执行可视化（七视图） | E | `src/req_builder/viz/` | `docs/visualization.md` | ✅ 可渲染真实 `events.jsonl` |
| 基线变更与冻结 | F | `src/baseline_mgr/` | `docs/baseline-change.md` | ⬜ 待实现 |

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
docs/                各模块设计文档（定稿放这里）
examples/            设计规格示例（可直接拿来跑）
tests/               用例
runs/                运行产物，不进版本库
```

## 快速开始

编排器（默认 `--provider mock`，不调模型也能跑完整流程）：

```bash
# 1. 用设计规格起一次运行（也可以 --idea "一句话想法"）
python -m src.scheduler.cli start --run-id DEMO --design-spec examples/design_spec_wechat.json

# 2. 跑到阻塞点（P7 等人签字）
python -m src.scheduler.cli run --run-id DEMO

# 3. 签字并继续
python -m src.scheduler.cli human --run-id DEMO --action sign --reason "任务分解与排期可接受"
python -m src.scheduler.cli resume --run-id DEMO

# 4. 自检（16 条验收用例）
python -m src.scheduler.selfcheck
```

各模块与可视化：

```bash
export PYTHONPATH=src
python -m req_builder.demo                    # E：生成样例运行并渲染七视图页面
python -m req_builder.viz --run-dir runs/DEMO # 把编排器的事件流渲染成七视图
python src/conflict_review/demo.py            # D：冲突裁决 + 打分门禁演示
python -m unittest discover -s tests -v       # E 的用例
python src/agents/tests/test_agents.py        # B 的用例
```

Windows PowerShell：先 `$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"`，命令同上。

依赖：Python 3.10+、PyYAML（编排器读取 `flow.yaml` / `gate_rule.yaml` 用）。

## 协作约定

- `main` 受保护，特性分支命名 `feature/<组员代号>-<模块名>`，PR 需一名组员评审；
- 运行产物 `runs/` 不进版本库，演示数据由各模块的 demo 现场生成；
- 产物统一走 `{meta, data, extensions}` 信封，`stage` 与 `produced_by` 由生产模块填写；
- 五个模块一律在 `src/<模块>/__init__.py` 暴露 `run(ctx) -> StageResult`，供编排器加载。

## 文档

- 设计总览（入口）：[`docs/README-设计总览.md`](docs/README-设计总览.md)
- 架构与数据契约：[`docs/architecture.md`](docs/architecture.md)
- 其余模块文档：`docs/role-discussion.md`、`docs/multi-solution.md`、`docs/review-conflict.md`、`docs/require-model.md`、`docs/visualization.md`、`docs/baseline-change.md`
