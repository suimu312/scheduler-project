# B 成员作业 2 与作业 3 交付说明

依据《第10组 作业2与作业3执行方案》：B 负责 W4 的 P3 真实产出，以及发言、引用、投票、插嘴信号和脱敏。B 不修改 E 的 HTML，不代替 D 裁决冲突，不声称全组 P1–P7 或 15 项 JSON 已验收。

## 已实现与文件位置

2026 年 11 月 24 日交付结果：W4 已完成一次真实 P3 产物验收。最终包为 `examples/results/B-REAL-BOUNDARY-20261124-recovered/`，包含四份陈述、四组问答、四份投票、冲突原话证据，以及真实模型调用和耗时记录。可阅读记录为该目录下的 `运行记录.md`；`acceptance.json` 为 `real / passed`，只读复验通过。

本次真实 API 实际尝试共 21 次：第一次 8 次，立场一致未出现冲突；第二次 12 次，出现真实冲突但 A4 投票超时；最后只补 A4 一次。两次授权总上限为 24 次。最终议题的 13 次尝试包含一次超时，按项目规则超时不扣调用预算，因此计费账本为 12 次。这里的“计费”是项目预算口径，不等同于供应商账单。

保留两份失败原包 `B-REAL-W4-20261124/` 与 `B-REAL-BOUNDARY-20261124/`，不覆盖或重写历史。修复后的包是同一逻辑运行的补票修订，不是作业 3 所要求的 `run_id.fork.1` 命令重试实现，后者归 A/E。

| 内容 | 文件与字段 |
|---|---|
| 四角色独立陈述 | `core.py` 先固定四个请求再提交；`discussion_minutes.data.statements` |
| 请求隔离记录 | `discussion_minutes.data.extensions.prompt_snapshots`：角色、步骤、可见引用、公开陈述 ID、请求 SHA256；首轮公开陈述列表必须为空 |
| 投票矩阵 | `discussion_minutes.data.vote_matrix`；导出包另存 `vote_matrix.json`，含四角色票型、理由、引用与投票 ID |
| 会议纪要 | `discussion_minutes.data.meeting_summary`，保留原有 `statements / qa / votes / tally` |
| 冲突原话追溯 | `agent_signals.data.conflict_evidence`：冲突 ID → 原始陈述 ID → 两句原话 → 材料引用；不把投票后的理由冒充原陈述 |
| 发言引用证据链 | `agent_signals.data.records`：发言、问答、投票 ID 与来源/目标/冲突引用 |
| 插嘴与挂起信号 | `agent_signals.data.interruptions`，源自已有防趋同、缺席、预算、投票不足等信号；是否处理仍由 A 决定 |
| 脱敏 | `agent_signals.redact`，用于交付包文件写入边界；日志不保存原始 prompt、响应或供应商异常 |
| 调用观测 | `evidence.ObservedProvider`：真实请求发 `model.call`，离线测试发 `fixture.call`；耗时和关联 ID 存 `spans.jsonl` |
| 一键验收 | `python -m src.agents.evidence`，导出两项核心产物、证据、事件、耗时和可阅读运行记录 |

快照证明的是本程序实际构造的请求内容；不是模型内部思维的证明。SHA256 用于复核同一请求，不能单独证明材料的业务真实性。旧运行没有快照时标记 `not_recorded`，禁止事后补造“已验证”。

## 本机运行

从仓库根目录执行，使用 Python 3.10+，并安装 `src/agents/requirements.txt`。下列命令使用正斜杠，避免 PowerShell 复制路径丢失反斜杠。

```powershell
& './.venv/Scripts/python.exe' -m unittest discover -s src/agents/tests -v

# 不联网的协议验收；明确标为 offline，不算 W4 真实模型验收
& './.venv/Scripts/python.exe' -m src.agents.evidence --mode offline --output examples/results/B-OFFLINE-W4-new

# 真实请求：仅在已确认发送虚构样例、API 费用且本机配置凭据后执行
# 每次最多尝试 12 次，包括失败或超时；目录必须为空，不覆盖旧记录
& './.venv/Scripts/python.exe' -m src.agents.evidence --mode real --sample conflict --output examples/results/B-REAL-W4-new

# 另一虚构议题：功能与安全要求不变，只讨论按用户流程或公共依赖划任务边界
& './.venv/Scripts/python.exe' -m src.agents.evidence --mode real --sample boundary --output examples/results/B-REAL-BOUNDARY-new

# 只读复验导出文件；不再调用模型
& './.venv/Scripts/python.exe' -m src.agents.evidence --verify --output examples/results/B-OFFLINE-W4-20261124

# 检查本次已完成的真实交付包，同样不联网
& './.venv/Scripts/python.exe' -m src.agents.evidence --verify --output examples/results/B-REAL-BOUNDARY-20261124-recovered
```

`conflict` 样例在虚构业务诉求与架构/风险约束间设置真实的取舍问题。模型自行表态，不强制某个角色投反对票。若全员同意或全员反对，冲突表可以为空，但这次运行会明确验收失败，不能声称已经证明“两句原话”的冲突追溯。已有结果仍落盘，保留失败证据。

## 交付目录和验收

| 路径 | 模式 | 结论 |
|---|---|---|
| `examples/results/B-REAL-BOUNDARY-20261124-recovered/` | 真实模型 | 最终 W4 交付包，复验通过 |
| `examples/results/B-REAL-BOUNDARY-20261124/` | 真实模型 | 保留原始超时记录，缺 A4 投票，未通过 |
| `examples/results/B-REAL-W4-20261124/` | 真实模型 | 立场一致，不能证明冲突追溯，未通过 |
| `examples/results/B-OFFLINE-W4-20261124/` | 固定脚本 | 协议和字段验收通过，不算真实调用 |

`examples/results/B-OFFLINE-W4-20261124/` 已保存离线验收包，供 clone 后直接检查字段。真实产物必须另存以 `B-REAL-` 开头的目录，并在 `acceptance.json` 中同时满足：

- `mode` 为 `real`，`actual_model_attempts` 大于 0。
- `status` 为 `passed`，`errors` 为空。
- 四角色陈述和四角色投票齐全，三个讨论步骤完成，未挂起。
- 首轮隔离快照齐全；至少一个冲突可追溯到相反立场的原始陈述。
- `events.jsonl` 有真实 `model.call`，并可关联到 `spans.jsonl` 与讨论产物。

B 的包只包含 P3 及其辅助证据，不用补假文件凑齐全组 W1 要求的 15 个 JSON。全组产物汇总与真实 P1–P7 运行由 A 负责。

## A E D C 接入约定

A：`src.scheduler.agents_bridge.run` 保持原入口，新增可选 `agent_signals` 产物。唯一的调度层修改是在 D 完成登记/裁决后调用 B 的 `build_signals`，使证据包含最终冲突。B 不修改阶段和预算的全局规则。`ObservedProvider(provider, run_id, mode, emit=统一出口)` 可以包裹现有 provider；不要与另一层重复发射 `model.call`。独立导出器的 `seq` 只属于该导出包，接入 A 后由 A 统一分配。

E：读取 `discussion_minutes.data.statements / qa / vote_matrix / meeting_summary` 展示讨论抽屉；从 `agent_signals.data.conflict_evidence[].quotes` 展示冲突两边原话；点击 `statement_ref / source_refs / target_ref` 跳转证据。脱敏后的日志不包含角色私有材料全文，引用全文展示必须遵守权限边界。

D：冲突登记和加权裁决仍归 D，B 不更改票重与打分公式。原始陈述在 `conflict_register.data.conflicts[].extensions.original_stances` 保留，证据链由此恢复原话。

C：`spans.jsonl` 当前 `schema_version` 为 `b-agent-span-1.0`，是 B 适配格式，尚未声称通过最终 `obs-schema.json`。请对齐时间、trace/span、角色、步骤、状态和耗时字段；观测失败计数应从实际尝试计算，不从成功票数反推。

## 脱敏边界

自动隐藏凭据字段、Bearer 值、常见 `sk-` / `ark-` 密钥、手机号和邮箱。它不是通用个人信息识别模型，不承诺识别姓名、地址或任意供应商密钥；正式数据入库前需要按团队数据约定检查。核心执行期保留原字段供规则校验，P3 返回给 A 的原生 StageResult 与公开交付包写入时统一脱敏，产物 ID 和引用保持稳定。P2 的角色私有材料仍属受限内部上下文，不应直接用于页面或公开日志。

## 提交范围

提交 `src/agents/agent_signals.py`、`evidence.py`、`recover_vote.py`、`core.py`、`stage.py`、新增测试、本说明与 README 更新；另提交 `src/scheduler/agents_bridge.py` 的证据链适配，以及 `examples/results/B-*/` 中确认可公开的交付包。未自动 commit 或 push，也未创建分支。

`recover_vote.py` 仅用于有明确超时记录、仅缺一张投票且其余步骤完整的虚构边界案例；每次只尝试一次并另存目录，保持原始超时信号和历史事件。它不实现任意单卡重试、不删除故障证据、不突破 P3 的 12 次预算。使用真实模式仍需确认对应 API 额度与样例发送授权。
