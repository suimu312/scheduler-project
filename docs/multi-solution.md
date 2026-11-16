# multi-solution.md 多方案任务分解设计

## 一、模块定位
本模块对应系统 **P4 阶段**，输入为 P3 裁决后的功能点基线、设计规格、冲突登记结果；输出三套不同切分口径的开发任务分解方案（WBS）与逐功能点比选表。输出产物供 P5 对抗式评审作为比对对象，采纳后的方案作为 P6 生成最终 WBS 的输入蓝本。

本模块严格遵守系统三条顶层设计原则：
1. **控制权在代码，不在模型**：三套方案的整体结构、校验规则由代码固化；模型仅负责内容填充，不能决定方案取舍、流程跳转。
2. **一切皆事件**：方案生成完成、比选完成均会输出事件，写入全局事件流，支持回放、审计。
3. **可计算的才算条件**：方案合法性校验、C4一致性校验、依赖合法性全部为纯函数；校验失败直接抛出异常，阻止非法产物流向下游。

> 模块代号：C（技术架构师）
> 代码目录：`src/solution_gen/`

### 模型依赖说明
1. 本模块复用项目统一网关 `llm_gateway`，通过 `set_provider()` 注入模型实例，不改动网关底层代码。
2. 模型调用能力直接复用B模块已实现的 `HttpModel` 传输适配类，导入路径：`from agents.core import HttpModel`。
3. 模型连接信息由环境变量注入，无需硬编码：
   - B_MODEL_ENDPOINT：模型网关地址
   - B_MODEL_TOKEN：网关鉴权令牌
4. 调用方式：使用 `HttpModel.from_env()` 自动读取环境变量完成实例化，直接注入网关。
5. 约束：模型仅负责填充三套方案的文本内容；方案结构、校验规则、输出格式全部由代码固化，严格遵循“控制权在代码，不在模型”原则。

## 二、三套任务分解方案设计
> **硬规则 C4：三套方案必须使用完全相同的 feature_ids 集合，ID 顺序、数量完全一致，保证可以逐行做功能点级别比选。**

### 2.1 方案A：按模块细分（SOL_A）
- **切分逻辑**：以业务模块边界为第一优先级；同一模块内的功能点再拆分为前端、后端、联调自测子任务。
- **适用场景**：团队角色划分清晰（前端/后端分开），追求最大并行开发效率。
- **特征**
  - 单个功能点拆分为：前端页面开发 → 后端接口开发 → 联调自测；
  - 任务粒度控制在 0.5-1.5 人天；
  - 跨模块依赖尽量少，职责归属清晰。
- **优点**：并行度高，适合多人同时开工；按模块验收方便。
- **缺点**：整体系统级联调集中在后期。

### 2.2 方案B：按里程碑切分（SOL_B）
- **切分逻辑**：基于功能优先级划分里程碑；Must级进入M1基础框架，Should级进入M2核心功能，Could级进入M3完善优化。每个里程碑包含完整可演示的端到端功能。
- **适用场景**：项目存在明确阶段性交付、演示、外部验收节点。
- **特征**
  - 每个里程碑前置一个评审启动任务；
  - 功能不再拆分前后端，使用全栈任务；
  - 单任务工时约 2.0 人天。
- **优点**：每一个里程碑结束都有可演示版本，风险显性化。
- **缺点**：对开发人员全栈能力有一定要求，单任务粒度偏大。

### 2.3 方案C：按风险优先（SOL_C）
- **切分逻辑**：按风险等级对功能点重排序；高风险功能优先做技术预研原型验证，验证通过后再正式开发；低风险功能直接进入开发。
- **适用场景**：项目包含新技术、未知第三方依赖、探索性需求。
- **特征**
  - high风险功能会额外增加"技术预研与原型验证"前置任务；
  - 任务执行顺序按照风险从高到低排列。
- **优点**：技术风险前置暴露，避免后期大规模返工。
- **缺点**：项目前期进度看起来较慢，预研存在不确定性。

## 三、方案比选表设计
### 3.1 比选维度
逐功能点对比三套方案，一行对应一个功能点：

|维度|说明|
|---|---|
|task_count|该功能点下拆分出多少张任务卡|
|total_hours|该功能点预估总人天|
|parallel|可并行执行的任务数量|
|risk|风险等级 low / medium / high|

### 3.2 融合规则
1. **单功能点推荐**：优先选择总工时最小的方案；
2. **禁止同一功能点混用多套切分逻辑**，会造成依赖混乱；
3. **blendable（可融合标记）**：代表该功能点可以独立选用某一套方案，和其他功能点做拼接；
4. **融合禁令**：融合后如果出现依赖悬空、依赖环，则直接标记为不可融合，不进入候选；
5. **final_recommendation**：全局给出一套默认推荐方案，本模块默认输出 SOL_A。

### 3.3 solution_comparison.json 输出字段
```json
{
  "feature_count": 3,
  "rows": [
    {
      "feature_id": "FEA-001",
      "title": "xxx",
      "data": {
        "SOL_A": {"task_count": 3, "total_hours": 3.0, "parallel": 1, "risk": "medium"},
        "SOL_B": {"task_count": 1, "total_hours": 2.0, "parallel": 1, "risk": "low"},
        "SOL_C": {"task_count": 1, "total_hours": 1.5, "parallel": 1, "risk": "low"}
      },
      "recommend": "SOL_C",
      "blendable": true
    }
  ],
  "summary": {
    "SOL_A": {"total_task": 9, "total_day": 9.0, "avg_task_day": 1.0},
    "SOL_B": {"total_task": 6, "total_day": 7.5, "avg_task_day": 1.25},
    "SOL_C": {"total_task": 4, "total_day": 6.0, "avg_task_day": 1.5}
  },
  "final_recommendation": "SOL_A",
  "blend_candidates": ["FEA-001", "FEA-002", "FEA-003"]
}
```

## 四、任务卡数据模型
所有三套方案输出的任务卡统一遵守下面字段规范（DEV任务卡）：

|字段|类型|必填|说明|
|---|---|---|---|
|task_id|string|是|DEV-001，全局唯一|
|feature_ref|string|是|关联的功能点ID FEA-XXX，用于追溯链|
|module_id|string|是|所属业务模块 MOD-XXX|
|title|string|是|任务标题，一卡一事|
|depends_on|array[string]|是|依赖task_id列表，空数组代表无依赖|
|estimate_days|number|是|工时，单位人天，范围 0.5 ~ 3.0|
|owner_role|string|是|负责人角色，单卡只允许一个负责人|
|acceptance_criteria|array[string]|是|验收条件，至少一条，必须可观测|
|source_refs|array[string]|是|来源引用，指向feature_list:FEA-xxx，保障追溯链完整|

## 五、模块接口契约
### 5.1 统一入口
模块对外唯一入口：
```python
def run(ctx: Dict[str, Any]) -> Dict[str, Any]:
```
由编排器 scheduler(A) 调用。

### 5.2 输入产物（从 ctx["artifacts"] 获取）
|产物|来源阶段|说明|
|---|---|---|
|design_spec|P1|规范化设计规格，modules、scope、constraints等|
|feature_baseline|P3|冻结的功能点清单 feature_items / features|
|conflict_register|P3|P3冲突裁决结果，作为分解约束|

> 本模块**禁止自行读写 runs/ 目录**，禁止调用其他 B/D/E/F 模块，只读取上下文输入，输出交给编排器落盘。

### 5.3 输出产物
输出放在返回值的 artifacts 中：
- breakdown_a：SOL_A整套WBS，携带meta
- breakdown_b：SOL_B整套WBS，携带meta
- breakdown_c：SOL_C整套WBS，携带meta
- solution_comparison：三套方案比选表，携带meta

每个产物都包含 meta 对象：schema_version、run_id、stage、produced_by、created_at。

## 六、内置校验规则（models.py）
模块内部执行如下校验，失败直接抛异常，阻断流程：
1. **C4一致性校验**：三套方案feature_ids必须完全一致；
2. **任务卡schema校验**：jsonschema校验全部必填字段、工时范围；
3. **依赖合法性校验**：所有depends_on中的task_id必须在本方案tasks内存在，不允许悬空依赖；
4. **追溯完整性校验**：每张任务卡必须携带source_refs。

## 七、上下游对接
- **上游**：scheduler(A)编排器调用；输入来自P1/P3产物。
- **下游**：输出给到 conflict_review(D) 用于P5评审打分；同时给到 req_builder(E) 作为P6最终WBS的蓝本。
- **数据流原则**：单向数据流；模块之间不互相调用；所有跨模块数据由编排器注入上下文。

## 八、模块内部执行流程
1. run() 接收编排器传入的上下文ctx
2. 导入 `HttpModel`，读取环境变量，注入 llm_gateway。generate_breakdowns() 生成 SOL_A / SOL_B / SOL_C 三套WBS
3. validate_breakdown() 做schema、C4、依赖合法性校验
4. build_comparison() 生成solution_comparison比选表
5. 打包artifacts、events、usage计数，返回给编排器
