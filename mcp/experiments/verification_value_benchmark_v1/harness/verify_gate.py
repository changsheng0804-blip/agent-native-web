# -*- coding: utf-8 -*-
"""验证套件 3：Gate policy 表驱动测试。

对应 H0_DEEPSEEK_PROMPT.md 第 5 节。覆盖 arms.json 全部状态：
pending / failed / unchanged / uncertain / conflict / committed，
以及：接受 success claim、允许新不可逆 intent、wait/reobserve、retry、
duplicate risk 下阻断同 intent、每次阻断记录 gate_blocks + reason。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import gate_policy, protocol  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def main():
    pd = protocol.load_protocol()
    tasks = pd["tasks"]["tasks"]
    recoverable_task = next(t for t in tasks if t["recoverable"])
    nonrecoverable_task = next(t for t in tasks if not t["recoverable"])
    dup_task = next(t for t in tasks if t["duplicate_risk"])
    print(f"  task 形态：recoverable={recoverable_task['task_id']}, "
          f"non-recoverable={nonrecoverable_task['task_id']}, duplicate={dup_task['task_id']}")

    print("=== 5.1 全矩阵可执行且每次阻断带 reason ===")
    matrix = gate_policy.full_matrix(pd)
    check("gate 全矩阵可生成", len(matrix) > 0, f"rows={len(matrix)}")
    bad = [r for r in matrix if (not r["allowed"]) and not str(r["reason"]).strip()]
    check("每次阻断都有非空 reason", not bad, f"缺 reason 的行数={len(bad)}")
    bad_block = [r for r in matrix if (not r["allowed"]) and not r["gate_block"]]
    check("阻断行 gate_block=true（供 gate_blocks 累加）", not bad_block, f"异常={len(bad_block)}")

    print("=== 5.2 逐状态语义（对照 README / HARNESS_HANDOFF）===")
    # pending：禁止完成 + 禁止新不可逆 intent；允许 wait/reobserve
    for action, want in ((gate_policy.ACTION_FINAL_SUCCESS, False),
                         (gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT, False),
                         (gate_policy.ACTION_WAIT, True),
                         (gate_policy.ACTION_REOBSERVE, True),
                         (gate_policy.ACTION_RETRY, False)):
        r = gate_policy.decide(pd, "pending", action, recoverable_task)
        check(f"pending.{action} → {want}", r["allowed"] is want, r["reason"][:90])

    # failed：禁止推进成功；仅 recoverable 允许 safe retry
    for task, want in ((recoverable_task, True), (nonrecoverable_task, False)):
        r = gate_policy.decide(pd, "failed", gate_policy.ACTION_RETRY, task)
        check(f"failed.retry（recoverable={task['recoverable']}）→ {want}", r["allowed"] is want,
              r["reason"][:90])
    for action, want in ((gate_policy.ACTION_FINAL_SUCCESS, False),
                         (gate_policy.ACTION_REPORT_FAILURE, True),
                         (gate_policy.ACTION_HANDOFF, True)):
        r = gate_policy.decide(pd, "failed", action, recoverable_task)
        check(f"failed.{action} → {want}", r["allowed"] is want, r["reason"][:90])

    # unchanged：同 failed 形态
    for action, want in ((gate_policy.ACTION_FINAL_SUCCESS, False),
                         (gate_policy.ACTION_REPORT_NOT_EFFECTED, True),
                         (gate_policy.ACTION_RETRY, True)):
        r = gate_policy.decide(pd, "unchanged", action, recoverable_task)
        check(f"unchanged.{action} → {want}", r["allowed"] is want, r["reason"][:90])

    # uncertain：禁止完成；允许 wait/reobserve/handoff；不允许 retry
    for action, want in ((gate_policy.ACTION_FINAL_SUCCESS, False),
                         (gate_policy.ACTION_WAIT, True),
                         (gate_policy.ACTION_REOBSERVE, True),
                         (gate_policy.ACTION_HANDOFF, True),
                         (gate_policy.ACTION_RETRY, False)):
        r = gate_policy.decide(pd, "uncertain", action, recoverable_task)
        check(f"uncertain.{action} → {want}", r["allowed"] is want, r["reason"][:90])

    # conflict：禁止完成；允许 reobserve/replan/handoff
    for action, want in ((gate_policy.ACTION_FINAL_SUCCESS, False),
                         (gate_policy.ACTION_REOBSERVE, True),
                         (gate_policy.ACTION_REPLAN, True),
                         (gate_policy.ACTION_HANDOFF, True),
                         (gate_policy.ACTION_RETRY, False)):
        r = gate_policy.decide(pd, "conflict", action, recoverable_task)
        check(f"conflict.{action} → {want}", r["allowed"] is want, r["reason"][:90])

    # committed：允许完成；duplicate_risk + 已 committed 时阻断同 intent
    r = gate_policy.decide(pd, "committed", gate_policy.ACTION_FINAL_SUCCESS, dup_task)
    check("committed.final_success → 允许（后置条件满足）", r["allowed"] is True, r["reason"][:90])
    r = gate_policy.decide(pd, "committed", gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT, dup_task,
                           intent_already_committed=True)
    check("committed + duplicate_risk + 已执行 → 阻断同 intent", r["allowed"] is False, r["reason"][:110])
    r = gate_policy.decide(pd, "committed", gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT, dup_task,
                           intent_already_committed=False)
    check("committed + duplicate_risk 但未执行过 → 允许首次 intent", r["allowed"] is True, r["reason"][:90])
    r = gate_policy.decide(pd, "committed", gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT, recoverable_task,
                           intent_already_committed=True)
    check("committed + 非 duplicate_risk → 不因重复而阻断", r["allowed"] is True, r["reason"][:90])

    print("=== 5.3 负向：gate 必须真的能阻断 ===")
    blocked = [r for r in matrix if not r["allowed"]]
    check("矩阵中存在阻断行（gate 不是恒放行）", len(blocked) > 0, f"blocked={len(blocked)}")
    allowed = [r for r in matrix if r["allowed"]]
    check("矩阵中存在放行行（gate 不是恒阻断）", len(allowed) > 0, f"allowed={len(allowed)}")
    # pending 下所有完成声明必须被拒
    claims = [r for r in matrix if r["status"] == "pending" and r["action"] == gate_policy.ACTION_FINAL_SUCCESS]
    check("pending 下所有完成声明均被拒", all(not r["allowed"] for r in claims), f"n={len(claims)}")

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
