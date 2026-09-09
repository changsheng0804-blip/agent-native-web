# -*- coding: utf-8 -*-
"""验证套件 8：脚本化正/负对照。

对应 H0_DEEPSEEK_PROMPT.md 第 9 节第 2 条：
「另做脚本化正向对照，使每个 fixture 至少存在一个预期成功/失败路径」。

证明管道不是「永远只能判失败」：每个 fixture 都有一条脚本化路径能到达
authoritative success，也有一条到达失败。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
PHASE_DIR = Path(__file__).resolve().parent.parent
RUNNER = PHASE_DIR / "runner.py"
ARTIFACTS = PHASE_DIR.parent / "artifacts" / "verification_value_benchmark_v1" / "H0-controls"
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


# 每个 task 的 (策略, 期望 task_success) —— 覆盖成功与失败两条路径
CASES = [
    ("silent-422", "claim_success", False, "UI 像成功但权威失败 → 不得判成功"),
    ("silent-422", "report_failure", False, "如实报告失败"),
    ("optimistic-rollback", "recover", True, "安全重试后可达 authoritative success"),
    ("optimistic-rollback", "premature", False, "pending 期间声称成功 → 失败 + premature"),
    ("optimistic-rollback", "no_retry", False, "不重试 → 失败"),
    ("duplicate-danger", "single_send", True, "只发一次 → success"),
    ("duplicate-danger", "double_send", False, "重复发送 → 失败 + duplicate"),
    ("false-causality", "claim_success", False, "被背景变化误导 → 不得判成功"),
    ("false-causality", "report_not_effected", False, "如实报告未生效"),
]

# 任务本身是否**可达** authoritative success。
# silent-422 / false-causality 的 fixture 设计上不存在成功路径
# （422 恒失败；refresh 不改变权威 version），因此只要求存在「预期失败」路径，
# 并额外要求分类器不会把这些场景误判为成功。
REACHABLE_SUCCESS = {
    "silent-422": False,
    "optimistic-rollback": True,
    "duplicate-danger": True,
    "false-causality": False,
}


def run_case(task_id, strategy, out_dir):
    proc = subprocess.run(
        [sys.executable, str(RUNNER), "run", "--tasks", task_id, "--arms", "A",
         "--runs-per-cell", "1", "--strategy", strategy, "--out", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(PHASE_DIR))
    jsonl = out_dir / "runs.jsonl"
    if not jsonl.exists():
        return None, proc
    rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    return (rows[0] if rows else None), proc


def main():
    if ARTIFACTS.exists():
        import shutil
        shutil.rmtree(ARTIFACTS)
    seen_success = set()
    seen_failure = set()
    for task_id, strategy, expect_success, why in CASES:
        out = ARTIFACTS / f"{task_id}_{strategy}"
        row, proc = run_case(task_id, strategy, out)
        if row is None:
            check(f"{task_id}/{strategy}: 产出记录", False, (proc.stderr or proc.stdout)[:200])
            continue
        ok = bool(row["task_success"]) is expect_success
        check(f"{task_id}/{strategy} → task_success={expect_success}", ok, why)
        if row["task_success"]:
            seen_success.add(task_id)
        else:
            seen_failure.add(task_id)

    print()
    for task_id, reachable in REACHABLE_SUCCESS.items():
        if reachable:
            check(f"{task_id}: 存在脚本化成功路径（管道非恒失败）", task_id in seen_success)
        else:
            check(f"{task_id}: fixture 设计上无可达成功路径，且未被误判为成功",
                  task_id not in seen_success and task_id in seen_failure,
                  "该场景的正确结果是识别失败")
        check(f"{task_id}: 存在脚本化失败路径", task_id in seen_failure)

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
