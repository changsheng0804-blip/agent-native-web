# -*- coding: utf-8 -*-
"""H0 一键复核：跑全部验证套件 + dry-run 审计，并汇总。

用法：python verify_all.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
RUNNER = HERE / "runner.py"
DRYRUN = HERE.parent / "artifacts" / "verification_value_benchmark_v1" / "H0-dryrun"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

SUITES = [
    ("fixture reset / 交互通道", [sys.executable, str(HERE / "harness" / "verify_fixtures.py")]),
    ("A/B/C 隔离 / verifier 等价", [sys.executable, str(HERE / "harness" / "verify_isolation.py")]),
    ("四场景真值", [sys.executable, str(HERE / "harness" / "verify_scenarios.py")]),
    ("gate policy 表驱动", [sys.executable, str(HERE / "harness" / "verify_gate.py")]),
    ("P0 classifier", [sys.executable, str(HERE / "harness" / "verify_classifier.py")]),
    ("schema / analyzer", [sys.executable, str(HERE / "harness" / "verify_schema.py")]),
    ("脚本化正/负对照", [sys.executable, str(HERE / "harness" / "verify_controls.py")]),
    ("artifact auditor 负向对照", [sys.executable, str(HERE / "harness" / "verify_auditor.py")]),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=ENV, cwd=str(HERE))


def main():
    results = []
    for name, cmd in SUITES:
        proc = run(cmd)
        lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
        summary = next((l for l in reversed(lines) if "/" in l and "通过" in l), lines[-1] if lines else "")
        ok = proc.returncode == 0
        results.append({"suite": name, "ok": ok, "exit": proc.returncode, "summary": summary.strip()})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {summary.strip()}")

    # dry-run 审计
    if DRYRUN.exists():
        proc = run([sys.executable, str(RUNNER), "audit", str(DRYRUN)])
        audit = json.loads((DRYRUN / "audit.json").read_text(encoding="utf-8"))
        rows = audit["rows"]
        fails = [f for f in audit["findings"] if f["level"] == "FAIL"]
        ok = proc.returncode == 0 and audit["ok"]
        results.append({"suite": "dry-run 产物审计", "ok": ok, "exit": proc.returncode,
                        "summary": f"{rows} rows / {len(fails)} findings"})
        print(f"[{'PASS' if ok else 'FAIL'}] dry-run 产物审计: {rows} rows / {len(fails)} findings")
    else:
        results.append({"suite": "dry-run 产物审计", "ok": False, "exit": -1, "summary": "缺少 dry-run 产物"})
        print("[FAIL] dry-run 产物审计: 缺少产物（先运行 runner.py run）")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n合计 {passed}/{len(results)} 套件通过")
    print("⚠ H0 仅校准仪器；不得用于推断 Astra、A/B/C 或项目战略价值。")
    out = HERE.parent / "artifacts" / "verification_value_benchmark_v1" / "verify_all.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"results": results, "passed": passed, "total": len(results)},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
