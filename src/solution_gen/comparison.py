from typing import Dict, List, Any

def _calc_parallel(tasks: List[Dict]) -> int:
    all_deps = set()
    for t in tasks:
        all_deps.update(t["depends_on"])
    return len(tasks) - len(all_deps)

def build_comparison(plans: Dict[str, Dict], feature_baseline: Dict) -> Dict[str, Any]:
    raw_features = feature_baseline.get("features", feature_baseline.get("feature_items", []))
    feature_list = []
    for i, f in enumerate(raw_features):
        if isinstance(f, str):
            feature_list.append({"feature_id": f"FEA-{i+1:03d}", "title": f})
        else:
            feature_list.append(f)

    plan_map = {
        "SOL_A": plans["a"],
        "SOL_B": plans["b"],
        "SOL_C": plans["c"]
    }

    rows = []
    for f in feature_list:
        fid = f["feature_id"]
        row = {"feature_id": fid, "title": f["title"]}
        cell_data = {}
        for pid, p in plan_map.items():
            ftasks = [t for t in p["tasks"] if t["feature_ref"] == fid]
            total_h = sum(t["estimate_days"] for t in ftasks)
            para = _calc_parallel(ftasks)
            risk = "low"
            if len(ftasks) >= 4 or total_h > 4:
                risk = "high"
            elif len(ftasks) >= 2:
                risk = "medium"
            cell_data[pid] = {
                "task_count": len(ftasks),
                "total_hours": total_h,
                "parallel": para,
                "risk": risk
            }
        row["data"] = cell_data
        scores = {k: v["total_hours"] for k, v in cell_data.items()}
        best = min(scores, key=scores.get)
        row["recommend"] = best
        row["blendable"] = True
        rows.append(row)

    summary = {}
    for pid, p in plan_map.items():
        summary[pid] = {
            "total_task": len(p["tasks"]),
            "total_day": p["total_days"],
            "avg_task_day": round(p["total_days"] / len(p["tasks"]), 2)
        }

    return {
        "feature_count": len(feature_list),
        "rows": rows,
        "summary": summary,
        "final_recommendation": "SOL_A",
        "blend_candidates": [r["feature_id"] for r in rows if r["blendable"]]
    }
