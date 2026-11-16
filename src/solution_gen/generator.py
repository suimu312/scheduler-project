from typing import Dict, List, Any

def _make_task(task_idx: int, feature_id: str, module_id: str, title: str,
               depends: List[str], days: float, owner: str,
               acceptance: str, source: str) -> Dict[str, Any]:
    return {
        "task_id": f"DEV-{task_idx:03d}",
        "feature_ref": feature_id,
        "module_id": module_id,
        "title": title,
        "depends_on": depends,
        "estimate_days": days,
        "owner_role": owner,
        "acceptance_criteria": [acceptance],
        "source_refs": [source]
    }

def _normalize_features(features: List[Any]) -> List[Dict]:
    normalized = []
    default_modules = ["MOD-001", "MOD-002", "MOD-003"]
    default_priority = ["must", "should", "could"]
    default_risk = ["medium", "low", "high"]

    for i, feat in enumerate(features):
        if isinstance(feat, str):
            normalized.append({
                "feature_id": f"FEA-{i+1:03d}",
                "title": feat,
                "module_id": default_modules[i % len(default_modules)],
                "priority": default_priority[i % len(default_priority)],
                "risk_level": default_risk[i % len(default_risk)]
            })
        else:
            normalized.append(feat)
    return normalized

def generate_breakdowns(feature_baseline: Dict, design_spec: Dict,
                        conflict_register: Dict) -> Dict[str, Any]:
    raw_features = feature_baseline.get("features", feature_baseline.get("feature_items", []))
    features = _normalize_features(raw_features)
    feature_ids = [f["feature_id"] for f in features]

    plans = {}

    # SOL_A 按模块细分
    tasks_a = []
    idx = 1
    for feat in features:
        src = f"feature_list:{feat['feature_id']}"
        mod = feat["module_id"]
        tasks_a.append(_make_task(
            idx, feat["feature_id"], mod,
            f"{feat['title']} 前端页面开发", [], 1.0, "frontend",
            f"完成{feat['title']}对应页面，交互字段完整符合设计", src
        ))
        idx += 1
        tasks_a.append(_make_task(
            idx, feat["feature_id"], mod,
            f"{feat['title']} 后端接口开发", [f"DEV-{idx-1:03d}"], 1.5, "backend",
            f"完成{feat['title']}对应接口，返回字段符合契约", src
        ))
        idx += 1
        tasks_a.append(_make_task(
            idx, feat["feature_id"], mod,
            f"{feat['title']} 联调与自测", [f"DEV-{idx-1:03d}"], 0.5, "fullstack",
            f"前后端联调通过，自测用例全部通过", src
        ))
        idx += 1

    plans["a"] = {
        "plan_id": "SOL_A",
        "plan_type": "module_based",
        "description": "按模块细分，最大化并行开发效率",
        "feature_ids": feature_ids,
        "tasks": tasks_a,
        "total_days": round(sum(t["estimate_days"] for t in tasks_a), 1)
    }

    # SOL_B 按里程碑切分
    tasks_b = []
    idx = 1
    milestones = [
        ("M1 基础框架", [f for f in features if f["priority"] == "must"]),
        ("M2 核心功能", [f for f in features if f["priority"] == "should"]),
        ("M3 完善优化", [f for f in features if f["priority"] == "could"])
    ]
    for ms_name, ms_feats in milestones:
        if not ms_feats:
            continue
        src_list = [f"feature_list:{f['feature_id']}" for f in ms_feats]
        tasks_b.append({
            "task_id": f"DEV-{idx:03d}",
            "milestone": ms_name,
            "title": f"{ms_name} 方案评审与启动",
            "depends_on": [],
            "estimate_days": 0.5,
            "owner_role": "pm",
            "acceptance_criteria": [f"{ms_name}技术方案评审通过，输出评审记录"],
            "source_refs": src_list
        })
        idx += 1
        for feat in ms_feats:
            tasks_b.append(_make_task(
                idx, feat["feature_id"], feat["module_id"],
                f"{feat['title']} 全栈实现", [f"DEV-{idx-1:03d}"], 2.0, "fullstack",
                f"{feat['title']}端到端可用，符合验收标准",
                f"feature_list:{feat['feature_id']}"
            ))
            idx += 1

    plans["b"] = {
        "plan_id": "SOL_B",
        "plan_type": "milestone_based",
        "description": "按里程碑切分，适配阶段交付与验收",
        "feature_ids": feature_ids,
        "tasks": tasks_b,
        "total_days": round(sum(t["estimate_days"] for t in tasks_b), 1)
    }

    # SOL_C 按风险优先
    tasks_c = []
    idx = 1
    risk_order = sorted(features, key=lambda x: x.get("risk_level", "low"), reverse=True)
    for feat in risk_order:
        src = f"feature_list:{feat['feature_id']}"
        if feat.get("risk_level") == "high":
            tasks_c.append(_make_task(
                idx, feat["feature_id"], feat["module_id"],
                f"{feat['title']} 技术预研与原型验证", [], 1.5, "backend",
                f"输出技术验证报告，确认方案可行性", src
            ))
            idx += 1
            deps = [f"DEV-{idx-1:03d}"]
        else:
            deps = []
        tasks_c.append(_make_task(
            idx, feat["feature_id"], feat["module_id"],
            f"{feat['title']} 功能开发与自测", deps, 1.5, "fullstack",
            f"功能完整实现，自测通过", src
        ))
        idx += 1

    plans["c"] = {
        "plan_id": "SOL_C",
        "plan_type": "risk_first",
        "description": "按风险优先，高风险项前置验证",
        "feature_ids": feature_ids,
        "tasks": tasks_c,
        "total_days": round(sum(t["estimate_days"] for t in tasks_c), 1)
    }

    return plans
