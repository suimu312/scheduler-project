# E 模块 · 任务条目与执行可视化

第 10 组「多智能体任务分解与编排系统」E 部分实现：输入设计规格与采纳方案，产出**开发任务卡**与**覆盖矩阵**，并把整轮运行渲染成一张可回放的自包含页面。

负责人：张艺津（`zyj-1102`）｜分支：`feature/E-req-builder`

---

## 它解决什么问题

设计规格写得再细，落不成"能验收、能追到出处、能排期"的卡片，排期与评审就都是猜。本模块只做三件事：

1. **结构化**：把方案里的卡片草稿补成八字段的开发任务卡（`DEV-*`），字段缺什么补什么，编号乱什么重什么；
2. **校验**：八条硬规则逐条判（粒度、负责人、依赖、成环、验收、来源、模糊卡），并给出可复现的优先级；
3. **证明**：用覆盖矩阵说明规格的每一条都被谁承接，缺口在哪、权重多重；再把事件流转成七视图，让人两分钟看清这轮跑得怎么样。

## 目录

```
.
├── docs/
│   ├── require-model.md        # 任务卡模型、八条规则、覆盖公式、门禁分工
│   └── visualization.md        # 七视图、信号-字段映射、渲染函数约定
├── src/req_builder/
│   ├── contracts.py            # 阶段信封与产物 meta 壳（复刻 architecture.md §0.4/§10.2）
│   ├── models.py               # DevTask / AcceptanceCriterion / SourceRef / CoverageRow
│   ├── priority.py             # P1–P5 优先级（纯代码判定，可复现）
│   ├── validators.py           # T1–T8 八条硬规则 + Kahn 查环
│   ├── coverage.py             # 覆盖矩阵、加权覆盖率、缺口与门禁数据面
│   ├── task_list.py            # P6 主流程：build_task_list / run
│   ├── demo.py                 # 样例运行（8 张卡 / 13.5 人天 / 67 条事件）
│   └── viz/                    # 事件读取 + SVG 原语 + 七视图 + HTML 组装 + CLI
├── tests/test_req_builder.py   # 30 条用例
└── runs/                       # 运行产物（不进版本库）
```

## 快速开始

```bash
# 自检样例：生成 runs/R0001 并渲染 index.html
PYTHONPATH=src python -m req_builder.demo

# 只渲染（可指定回放截止序号）
python -m req_builder.viz --run-dir runs/R0001 --until 36

# 用例
PYTHONPATH=src python -m unittest discover -s tests -v
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="src"; $env:PYTHONIOENCODING="utf-8"
python -m req_builder.demo
python -m unittest discover -s tests -v
```

无第三方依赖，仅标准库。

## 交付物

| 文件 | 内容 |
|---|---|
| `runs/<run_id>/artifacts/dev_task_list.json` | `tasks[]`（八字段 + `priority` 与理由）+ `summary` + `hard_validation` |
| `runs/<run_id>/artifacts/task_coverage.json` | `rows[]` / `columns[]` / `gap_count` / `coverage_rate` / `gaps[]` / `gate` |
| `runs/<run_id>/viz/index.html` | 七视图自包含页面，SVG 绘制、无外部资源、断网可看 |

样例数据自检：8 张卡、13.5 人天、优先级 must 5 / should 2 / could 1 / wont 0、覆盖率 100%、`gap_count=0`、`G_P6_P7` 数据面通过、67 条事件。

## 与其它模块的接口

| 方向 | 内容 |
|---|---|
| 读 | `design_spec`（P1）、`solution_comparison`（C · P4）、`conflict_register`（D · P3）、`review_sheet`（D · P5）、`schedule.json`（A · P7，仅可视化用） |
| 写 | `dev_task_list` + `task_coverage` 两个产物信封，`budget_used = 0`（纯代码不计费） |
| 判 | **不判门禁**：`G_P6_P7` 由 A 按 `gap_count == 0` 与"非 Wont 任务均有验收"两条判定，E 只提供数据 |

## 设计取舍（评审时最容易被问的六点）

1. **优先级与覆盖都由代码算**，不调模型：让模型给自己写的卡排优先级，七成都会写成 Must；让它自查覆盖，结论永远是"已覆盖"。
2. **Wont 不进覆盖率分母**：Wont 是主动排除而不是没做完，塞进分母会导致"越诚实标注不做的团队覆盖率越低"。
3. **T8（同模块同文件不跨波并行）P6 判不了**：依赖 `schedule.json` 的波次，由 A 在 P7 校验；本模块明确标注未校验，不假装置信。
4. **覆盖矩阵的行必须四类齐全**（功能点 / 约束 / 冲突 / 质疑）：只统计功能点等于选择性覆盖，最难落实的约束和冲突正好被漏掉。
5. **界面是事件的投影**：渲染函数只吃事件数组与截止序号，实时与回放同一套代码；界面不回写运行状态，人工动作先落 `human.decision` 事件。
6. **V5 依赖图与甘特同屏**：只画甘特看不出为什么不能并行，只画依赖图感受不到工期压力；两者共用同一份 `schedule.json`，E 不自己重算。

## 分支与提交

- `main` 受保护，特性分支 `feature/E-req-builder`，PR 需一名组员评审（V2 §7.4）；
- `runs/`、`__pycache__/`、截图不进版本库，演示数据由 `demo.py` 现场生成。
