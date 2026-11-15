# -*- coding: utf-8 -*-
"""四条断言，覆盖证据优先、加权投票、挂起、归一化打分。

运行： python test_rules.py
"""

from rules import detect_conflicts, adjudicate, weighted_total, stddev, gate


def st(agent, topic, position, ev=()):
    return {"agent_id": agent, "topic_id": topic, "position": position,
            "proposal": position + "_" + agent, "evidence_refs": list(ev),
            "quote_ref": "q"}


def test_evidence_first():
    """一方有依据、另一方没有 -> L1，直接判，不投票。"""
    c = detect_conflicts([st("A2", "t1", "for"), st("A3", "t1", "against", ["FEA-001"])])[0]
    r = adjudicate(c)
    assert r["level"] == "L1_evidence", r
    assert r["resolution"]["winner"] == "A3", r


def test_weighted_vote():
    """双方都无依据 -> 按权重算票，A2(1.5)+A1(1.0) 对 A3(1.0) = 1.5，判定成立。"""
    c = detect_conflicts([st("A2", "t2", "for"), st("A1", "t2", "for"),
                          st("A3", "t2", "against")])[0]
    r = adjudicate(c)
    assert r["level"] == "L2_weighted_vote", r
    assert r["resolution"]["winner"] == "A2", r


def test_escalate():
    """A1(1.0) 对 A2(1.5) -> -0.5，未达 1.0，挂起交人。"""
    c = detect_conflicts([st("A1", "t3", "for"), st("A2", "t3", "against")])[0]
    r = adjudicate(c)
    assert r["level"] == "L3_escalate", r
    assert r["status"] == "suspended", r


def test_normalized_score():
    """四维权重和为 90，必须归一化到百分制；不能再乘 100。"""
    s = {"functional": 92, "technical": 78, "risk": 85, "testability": 88}
    assert weighted_total(s) == 85.5, weighted_total(s)
    assert 0 <= stddev(s) <= 100
    ok = gate(s)
    assert ok["pass"] is True, ok
    bad = gate(s, veto=True)
    assert bad["pass"] is False and "否决" in bad["reasons"][0], bad


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
            print("PASS  %s  %s" % (name, (fn.__doc__ or "").split("\n")[0]))
    print("\n%d 条全部通过" % n)
