# 第10组 · 多智能体任务分解与编排系统

> 课堂作业2：**设计规格（design spec）→ PM Agent 开发任务分解 → 编排器生成任务编排 → 可视化**。
> 系统做成一套通用可定制的 SE 工作流：换一份设计规格（"我要做微信"或"我要做小红书"），同一套引擎重新跑出另一份任务清单与排期，具体产品只作为测试案例。

## 这条流程做什么

1. **P1 设计规格进场**：人给一份 `design_spec`（产品、范围、功能点、约束、模块），P1 只做规范化与校验，缺字段退回，不由模型猜。
2. **P2–P5 把口径吵清楚**：模块划分 → 分解口径讨论与裁决 → 三套分解方案（按模块 / 按里程碑 / 按风险）→ 对抗式评审。
3. **P6 WBS 定稿**：PM Agent 产出开发任务清单 `dev_task_list.json`，每张卡八项字段（任务号、模块、标题、依赖、工时、负责人、验收口径、来源引用）。
4. **P7 排期与冻结**：编排器纯代码排期——查环、分层定波次、算关键路径、处理同一负责人在同一波的资源冲突，产出 `schedule.json`；人签字后基线冻结。
5. **可视化**：`viz --html` 导出甘特图 + 依赖图，关键路径高亮，断网可看。

## 目录

```
docs/        设计文档（architecture.md 由 A 统一定义数据契约）
src/
  scheduler/       编排引擎：状态机、闸门、预算、排期、CLI（A）
  agents/          角色与讨论（B）
  solution_gen/    三套分解与比选（C）
  conflict_review/ 评审、打分、裁决（D）
  req_builder/     任务卡、覆盖矩阵、可视化渲染（E）
  baseline_mgr/    确认单、基线、变更（F）
examples/    设计规格示例（可直接拿来跑）
runs/        运行产物，不进版本库
```

## 快速开始

```bash
# 1. 用设计规格起一次运行（也可以 --idea "一句话想法"）
python -m src.scheduler.cli start --run-id DEMO --design-spec examples/design_spec_wechat.json

# 2. 跑到阻塞点（P7 等人签字）
python -m src.scheduler.cli run --run-id DEMO

# 3. 签字并继续
python -m src.scheduler.cli human --run-id DEMO --action sign --reason "任务分解与排期可接受"
python -m src.scheduler.cli resume --run-id DEMO

# 4. 导出可视化（甘特 + 依赖图）
python -m src.scheduler.cli viz --run-id DEMO --html

# 5. 自检（16 条验收用例）
python -m src.scheduler.selfcheck
```

依赖：Python 3.10+、PyYAML。

## 当前进度

| 模块 | 状态 |
| --- | --- |
| `src/scheduler/` | 已完成并跑通：七阶段状态机、12 行路由、预算与阈值、人工闸门、任务图校验、排期、CLI、自检 16/16 |
| `src/agents/` `src/solution_gen/` `src/conflict_review/` `src/req_builder/` `src/baseline_mgr/` | 目录已建，模块实现待各责任人补齐；引擎用 mock 驱动，契约先跑通 |

## 文档

- 设计总览（入口）：[`docs/README-设计总览.md`](docs/README-设计总览.md)
- 架构与数据契约：`docs/architecture.md`
- 其余模块文档：`docs/agent-roles.md`、`multi-solution.md`、`review-conflict.md`、`require-model.md`、`visualization.md`、`baseline-change.md`

## 协作规则

`main` 受保护，禁止直推；每人开 `feature/<组员代号>-<模块名>` 分支，提 PR 至少 1 人 review 后合并。文档进 `docs/`，代码进 `src/`，运行产物进 `runs/`（不入库）。
