# 多智能体任务分解与编排系统（第 10 组）

输入一份设计规格（design spec），由多智能体分工完成：需求澄清 → 三套方案 → 冲突裁决 → 开发任务分解 → 排期编排 → 全程可视化。目标是一套**通用可定制的软件工程流程**，具体产品（如校园二手交易平台）只是测试案例。

## 模块划分

| 阶段 | 负责 | 代码 | 设计文档 |
|---|---|---|---|
| P1 规格输入 / P2 角色讨论 / P7 排期编排 | A | `src/scheduler/` | `docs/architecture.md` |
| P3 三段式讨论与防趋同 | B | `src/agents/` | `docs/role-discussion.md` |
| P4 三套方案生成与对比 | C | `src/solution_gen/` | `docs/multi-solution.md` |
| P5 冲突裁决与五维打分 | D | `src/conflict_review/` | `docs/review-conflict.md` |
| P6 任务卡生成与覆盖矩阵 | E | `src/req_builder/` | `docs/require-model.md` |
| 执行可视化（七视图） | E | `src/req_builder/viz/` | `docs/visualization.md` |
| 基线变更与冻结 | F | `src/baseline_mgr/` | `docs/baseline-change.md` |

设计总览见 `docs/README-设计总览.md`。

## 运行

```bash
export PYTHONPATH=src
python -m req_builder.demo                 # E：生成样例运行并渲染七视图页面
python -m unittest discover -s tests -v    # 用例
```

Windows PowerShell：先 `$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"`，命令同上。

无第三方依赖，仅标准库。

## 目录

```
src/
├── scheduler/        # A 编排引擎：状态机、闸门、调度、预算
├── agents/           # B 角色配置
├── solution_gen/     # C 方案生成
├── conflict_review/  # D 冲突与评审
├── req_builder/      # E 任务卡 + 覆盖矩阵 + 七视图渲染
└── baseline_mgr/     # F 基线管理
docs/                 # 各模块设计文档（定稿放这里）
tests/                # 用例
runs/                 # 运行产物，不进版本库
```

## 协作约定

- `main` 受保护，特性分支命名 `feature/<组员代号>-<模块名>`，PR 需一名组员评审；
- 运行产物 `runs/` 不进版本库，演示数据由各模块的 demo 现场生成；
- 产物统一走 `{meta, data, extensions}` 信封，`stage` 与 `produced_by` 由生产模块填写。
