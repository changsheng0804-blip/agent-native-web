# -*- coding: utf-8 -*-
"""验证套件 6：schema / analyzer 验证（含人工可手算数据集）。

对应 H0_DEEPSEEK_PROMPT.md 第 7 节。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
PHASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PHASE_DIR))
from harness import protocol, schema_validator  # noqa: E402

ANALYZER = PHASE_DIR / "analyze_results.py"
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def row(run_id, task_id, arm, *, claimed=False, success=False, fp=False, premature=False,
        dup=False, recov_attempted=False, recov=None, invalid=False, reason=None, elapsed=1000):
    return {
        "run_id": run_id, "benchmark_id": "verification-value-benchmark-v1",
        "task_id": task_id, "arm": arm, "model": "test-model",
        "harness_version": "test", "started_at": "2026-01-01T00:00:00Z",
        "elapsed_ms": elapsed, "claimed_success": claimed, "task_success": success,
        "false_positive": fp, "premature_completion": premature,
        "unsafe_duplicate_effect": dup, "recovery_attempted": recov_attempted,
        "recovery_success": recov, "computer_use_actions": 1, "screenshots": 0,
        "retries": 0, "verifier_packets": 0, "gate_blocks": 0, "pending_wait_ms": 0,
        "tokens_in": None, "tokens_out": None, "final_model_text": "x",
        "first_success_claim_ms": 100 if claimed else None,
        "invalid_run": invalid, "invalid_reason": reason,
        "final_judge": {"terminal": True, "task_success": success, "authoritative_state": {}},
        "raw_trace_path": "raw/x.json",
    }


def run_analyzer(rows):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "runs.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run([sys.executable, str(ANALYZER), str(path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


def main():
    pd = protocol.load_protocol()
    schema = pd["schema"]

    print("=== 7.1 schema：合法样本通过 ===")
    good = row("R1", "silent-422", "A", claimed=True, success=False, fp=True)
    try:
        schema_validator.validate_row(good, schema)
        check("合法样本通过 schema", True)
    except SystemExit as exc:
        check("合法样本通过 schema", False, str(exc)[:200])

    print("=== 7.2 schema：缺 required 字段失败 ===")
    for field in ("task_success", "false_positive", "final_judge", "raw_trace_path"):
        bad = dict(good)
        bad.pop(field, None)
        try:
            schema_validator.validate_row(bad, schema)
            check(f"缺 {field} → 被拒", False, "未拒绝")
        except SystemExit:
            check(f"缺 {field} → 被拒", True)

    print("=== 7.3 schema：非法 arm/task/status 失败 ===")
    for field, value in (("arm", "D"), ("task_id", "not-a-task")):
        bad = dict(good)
        bad[field] = value
        try:
            schema_validator.validate_row(bad, schema)
            check(f"非法 {field}={value} → 被拒", False, "未拒绝")
        except SystemExit:
            check(f"非法 {field}={value} → 被拒", True)

    print("=== 7.4 invalid run 无 reason 失败；有 reason 通过 ===")
    bad = row("R2", "silent-422", "A", invalid=True, reason=None)
    try:
        schema_validator.validate_row(bad, schema)
        check("invalid_run 无 reason → 被拒", False, "未拒绝")
    except SystemExit:
        check("invalid_run 无 reason → 被拒", True)
    ok_inv = row("R3", "silent-422", "A", invalid=True, reason="模拟基础设施故障")
    try:
        schema_validator.validate_row(ok_inv, schema)
        check("invalid_run 带 reason → 通过", True)
    except SystemExit as exc:
        check("invalid_run 带 reason → 通过", False, str(exc)[:200])
    blank = row("R4", "silent-422", "A", invalid=True, reason="   ")
    try:
        schema_validator.validate_row(blank, schema)
        check("invalid_run 空白 reason → 被拒", False, "未拒绝")
    except SystemExit:
        check("invalid_run 空白 reason → 被拒", True)

    print("=== 7.5 analyzer：人工数据集手算核对 ===")
    # 手算设计：
    #  silent-422:A 2 个有效 run → success 0/2, fp 2/2
    #  silent-422:B 1 个有效 run → success 0/1, fp 0/1
    #  duplicate-danger:C 1 个有效 run → dup 1/1
    #  另 1 个 invalid run
    rows = [
        row("A1", "silent-422", "A", claimed=True, success=False, fp=True, elapsed=1000),
        row("A2", "silent-422", "A", claimed=True, success=False, fp=True, elapsed=3000),
        row("B1", "silent-422", "B", claimed=False, success=False, fp=False, elapsed=2000),
        row("C1", "duplicate-danger", "C", claimed=True, success=False, fp=True, dup=True, elapsed=4000),
        row("I1", "silent-422", "A", invalid=True, reason="模拟故障"),
    ]
    proc = run_analyzer(rows)
    check("analyzer 正常退出", proc.returncode == 0, proc.stderr[:200])
    if proc.returncode == 0:
        out = json.loads(proc.stdout)
        check("有效 runs=4，invalid=1（invalid 排除主统计但单列）",
              out["effective_runs"] == 4 and out["invalid_runs"] == 1,
              f"effective={out['effective_runs']} invalid={out['invalid_runs']}")
        g = out["groups"]["silent-422:A"]
        check("silent-422:A runs=2（invalid 已排除）", g["runs"] == 2, f"runs={g['runs']}")
        check("silent-422:A task_success_rate=0.0（手算 0/2）", g["task_success_rate"] == 0.0,
              str(g["task_success_rate"]))
        check("silent-422:A false_positive_rate=1.0（手算 2/2）", g["false_positive_rate"] == 1.0,
              str(g["false_positive_rate"]))
        check("silent-422:A mean_elapsed=2000（手算 (1000+3000)/2）", g["mean_elapsed_ms"] == 2000.0,
              str(g["mean_elapsed_ms"]))
        gb = out["groups"]["silent-422:B"]
        check("silent-422:B fp_rate=0.0（手算 0/1）", gb["false_positive_rate"] == 0.0, str(gb["false_positive_rate"]))
        gc = out["groups"]["duplicate-danger:C"]
        check("duplicate-danger:C unsafe_duplicate_effect_rate=1.0（手算 1/1）",
              gc["unsafe_duplicate_effect_rate"] == 1.0, str(gc["unsafe_duplicate_effect_rate"]))

    print("=== 7.6 analyzer：分组正确性 ===")
    if proc.returncode == 0:
        out = json.loads(proc.stdout)
        check("分组键为 task:arm 且数量正确", set(out["groups"]) == {"silent-422:A", "silent-422:B",
                                                              "duplicate-danger:C"},
              str(sorted(out["groups"])))

    print("=== 7.7 analyzer：异常/空输入明确报错 ===")
    with tempfile.TemporaryDirectory() as td:
        empty = Path(td) / "empty.jsonl"
        empty.write_text("", encoding="utf-8")
        p = subprocess.run([sys.executable, str(ANALYZER), str(empty)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        check("空输入明确报错（不静默输出误导 summary）",
              p.returncode != 0 and ("空" in (p.stdout + p.stderr) or "empty" in (p.stdout + p.stderr).lower()),
              (p.stdout + p.stderr)[:120])
        bad = Path(td) / "bad.jsonl"
        bad.write_text("{not json}\n", encoding="utf-8")
        p2 = subprocess.run([sys.executable, str(ANALYZER), str(bad)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        check("非法 JSON 明确报错", p2.returncode != 0, (p2.stdout + p2.stderr)[:120])
        no_reason = Path(td) / "nr.jsonl"
        no_reason.write_text(json.dumps(row("X", "silent-422", "A", invalid=True, reason=None)) + "\n",
                             encoding="utf-8")
        p3 = subprocess.run([sys.executable, str(ANALYZER), str(no_reason)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        check("invalid run 无 reason → analyzer 报错", p3.returncode != 0, (p3.stdout + p3.stderr)[:120])

    print("=== 7.8 analyzer：null/optional 字段不崩溃 ===")
    rows2 = [row("N1", "silent-422", "A", claimed=False, success=True)]
    rows2[0]["recovery_success"] = None
    rows2[0]["tokens_in"] = None
    rows2[0]["pending_wait_ms"] = 0
    proc2 = run_analyzer(rows2)
    check("null/optional 字段不导致崩溃", proc2.returncode == 0, proc2.stderr[:150])

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
