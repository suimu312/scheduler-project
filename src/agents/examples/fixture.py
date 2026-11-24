"""校园二手交易案例：用于独立验收 B，不冒充 A/C/D/E/F 实现。"""
from ..core import artifact


def make_context(stage="P3"):
    run_id = "B-DEMO-001"
    def wrap(name, data):
        out = artifact(run_id, "P1" if name == "design_spec" else "P6" if name == "dev_task_list" else "P2", name, data)
        out["meta"]["producer"] = "E-DEMO-FIXTURE" if name == "dev_task_list" else "A-DEMO-FIXTURE"
        return out
    spec = {"spec_id": "SPEC-001", "product": "校园二手交易平台",
            "scope_in": ["发布商品", "浏览商品", "交易联系", "高级筛选"],
            "modules": [{"module_id": "MOD-001", "name": "商品"}, {"module_id": "MOD-002", "name": "交易"}],
            "features": [
                {"feature_id": "FEA-001", "description": "学生发布商品", "core_journey": True},
                {"feature_id": "FEA-002", "description": "学生浏览并联系卖方", "core_journey": True},
                {"feature_id": "FEA-003", "description": "按多个条件高级筛选", "user_value": "high"},
                {"feature_id": "FEA-004", "description": "页面主题装饰", "user_value": "low"}],
            "constraints": [{"constraint_id": "CON-001", "description": "发布与联系必须具备身份校验", "mandatory": True}]}
    materials = {"items": [
        {"source_ref": "material_catalog:MAT-001", "category": "spec", "text": "一期支持发布、浏览和联系；讨论先完成交易闭环再增强筛选的切法。", "source_refs": ["design_spec:SPEC-001"]},
        {"source_ref": "material_catalog:MAT-002", "category": "business", "text": "演示目标是完成发布到联系的流程，不要求一次交付所有增强项。", "source_refs": ["design_spec:FEA-001", "design_spec:FEA-002"]},
        {"source_ref": "material_catalog:MAT-003", "category": "user_feedback", "text": "测试用户首先关心商品能否找到、能否联系卖方，希望按真实流程验收。", "source_refs": ["design_spec:FEA-002"]},
        {"source_ref": "material_catalog:MAT-004", "category": "architecture", "text": "发布和联系依赖统一身份校验及接口契约，后续任务必须注明前置关系。", "source_refs": ["design_spec:CON-001"]},
        {"source_ref": "material_catalog:MAT-005", "category": "risk", "text": "异常输入、越权联系需要独立验证，不能用正常流程通过替代。", "source_refs": ["design_spec:CON-001"]},
        {"source_ref": "material_catalog:MAT-006", "category": "acceptance_library", "text": "验收要能观察结果并包含失败情况。", "source_refs": ["design_spec:SPEC-001"]},
        {"source_ref": "material_catalog:MAT-007", "category": "confirmation", "text": "签署必须由人执行并关联基线。", "source_refs": ["design_spec:SPEC-001"]}
    ]}
    classification = {"items": [
        {"source_ref": "design_spec:FEA-001", "categories": ["functional"], "confidence": 0.95, "owner_role": "A2", "responsibility": "发布链路"},
        {"source_ref": "design_spec:FEA-002", "categories": ["functional", "risk"], "confidence": 0.60, "owner_role": "A2", "responsibility": "联系链路"},
        {"source_ref": "design_spec:FEA-003", "categories": ["technical"], "confidence": 0.90, "owner_role": "A3", "responsibility": "筛选接口"},
        {"source_ref": "design_spec:FEA-004", "categories": ["cost"], "confidence": 0.90, "owner_role": "A1", "responsibility": "增强体验"}
    ]}
    tasks = {"items": [
        {"task_id": "DEV-001", "source_refs": ["design_spec:FEA-001"], "estimate_days": 2, "acceptance": ["学生可提交商品并在列表查看"]},
        {"task_id": "DEV-002", "source_refs": ["design_spec:FEA-002", "design_spec:CON-001"], "estimate_days": 2, "acceptance": ["身份校验后能联系卖方；未登录不能联系"]},
        {"task_id": "DEV-003", "source_refs": ["design_spec:FEA-003"], "estimate_days": 2, "acceptance": ["筛选结果符合指定条件"]},
        {"task_id": "DEV-004", "source_refs": ["design_spec:FEA-004"], "estimate_days": 1, "acceptance": ["可切换主题"]}
    ]}
    for index, task in enumerate(tasks["items"]):
        task.update(module_id="MOD-002" if index == 1 else "MOD-001", depends_on=["DEV-001"] if index == 1 else [],
                    owner_role="A3", priority="M" if index < 2 else "S" if index == 2 else "C")
    return {"run_id": run_id, "stage": stage, "budget_left": 12,
            "artifacts": {name: wrap(name, data) for name, data in {
                "design_spec": spec, "material_catalog": materials, "classification": classification, "dev_task_list": tasks}.items()},
            "config": {"protocol_step": "statement", "round": 1,
                       "issue": {"issue_id": "ISS-001", "topic_id": "granularity", "description": "是否先按最小交易闭环交付，再补充高级筛选等增强功能？"}}}


class ScriptedModel:
    """固定脚本，验证协议与数据流，不测量真实大模型的质量。"""
    claims = {
        "A1": ("FOR", "采用阶段性交付控制范围，先验证交易闭环的业务收益，增强项单独进入下一批验收。"),
        "A2": ("FOR", "沿学生操作顺序组织验收，让发布商品和联系卖方形成连贯体验，避免只有接口没有完整场景。"),
        "A3": ("AGAINST", "当前切法遗漏身份认证的接口前置，需先明确公共契约及依赖边，再讨论业务链路如何组合。"),
        "A4": ("AGAINST", "正常流程演示不足以证明安全，越权拦截与异常输入要列为独立验证工作，不能推迟到增强阶段。")}
    answers = {
        "A1": "我接受把必要保障纳入第一阶段，范围控制只适用于增强项；以该阶段业务验收记录检查交付收益。",
        "A2": "体验验收需要涵盖失败反馈，在交易路径中设置可观察结果；不能用模块完成状态代替用户操作成功。",
        "A3": "先确定身份接口契约，再允许发布和联系实现并行；通过集成测试确认前置返回值满足调用约定。",
        "A4": "把非法输入和未授权访问作为明确的负向用例，只有拦截行为与错误反馈都符合标准才能验收。"}
    reasons = {
        "A1": "支持优先交付闭环，并将必要保障列入同批范围；增强项另行安排可控制投入。",
        "A2": "支持沿场景验收，但要求保留失败反馈；这样能直接观察学生是否完成交易联系。",
        "A3": "在依赖契约补齐后支持该方向；执行顺序仍须由编排器按真实前置关系计算。",
        "A4": "仍反对当前未写清风险验收的命题；应补齐负向用例后再交 D 判断。"}

    def __call__(self, request, timeout=120):
        aid, step = request["agent_id"], request["step"]
        view = __import__("json").loads(request["user"])
        private = next(m for m in view["visible_materials"] if m["category"] != "spec")
        refs = [private["source_ref"]]
        if step == "statement":
            position, claim = self.claims[aid]
            return {"position": position, "claim": claim, "source_refs": refs}
        if step == "query":
            return {"answer": self.answers[aid], "source_refs": refs}
        return {"ballot": "AGAINST" if aid == "A4" else "FOR", "reason": self.reasons[aid], "source_refs": refs}
