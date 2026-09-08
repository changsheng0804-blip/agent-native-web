# -*- coding: utf-8 -*-
"""H0 最小能力自检：一次跑完 H0_DEEPSEEK_PROMPT.md 第 11 节的 12 项完成条件。

这是「仪器是否 ready」的单一入口；不产生任何关于 A/B/C 价值的结论。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
PHASE_DIR = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(PHASE_DIR))
from harness import arm_policy, protocol  # noqa: E402

RUNNER = PHASE_DIR / "runner.py"
ARTIFACTS = PHASE_DIR.parent / "artifacts" / "verification_value_benchmark_v1"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=ENV, cwd=str(PHASE_DIR))


def suite(name, script):
    proc = run([sys.executable, str(HERE / script)])
    lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    summary = next((l for l in reversed(lines) if "/" in l and "通过" in l), "")
    return proc.returncode == 0, summary.strip()


def main():
    print("[H0 smoke] 逐项核对完成条件 1–12")
    pd = protocol.load_protocol()

    # 1 四 fixture reset / timer 隔离
    ok, s = suite("reset", "verify_fixtures.py")
    check("① 四 fixture reset / timer 隔离", ok, s)
    # 2 四场景真值机制正负证据
    ok, s = suite("scenarios", "verify_scenarios.py")
    check("② 四场景真值机制正/负证据", ok, s)
    # 3 A/B/C 隔离 + 越权无副作用
    ok, s = suite("isolation", "verify_isolation.py")
    check("③ A/B/C 隔离与越权无副作用", ok, s)
    # 4/5 B/C verifier 等价 + C 唯一多 gate
    caps = arm_policy.isolation_matrix(pd)
    same_except_gate = all(
        caps["B"][k] == caps["C"][k] for k in ("tools", "model_capabilities",
                                               "verifier_packet_visible", "hidden_judge_visible"))
    check("④⑤ B/C verifier 等价且 C 唯一多 gate",
          same_except_gate and caps["B"]["gate_enforced"] is False and caps["C"]["gate_enforced"] is True,
          "B/C 除 gate 外能力指纹一致")
    # 6 hidden judge 零泄漏（由 isolation 套件覆盖，此处做结构断言）
    check("⑥ hidden judge 零泄漏（三臂均不可见）",
          all(caps[a]["hidden_judge_visible"] is False for a in ("A", "B", "C")))
    # 7 Gate 全状态表驱动
    ok, s = suite("gate", "verify_gate.py")
    check("⑦ Gate 全状态表驱动测试", ok, s)
    # 8 P0 classifier 正负对照
    ok, s = suite("classifier", "verify_classifier.py")
    check("⑧ P0 classifier 正/负对照", ok, s)
    # 9 schema / analyzer
    ok, s = suite("schema", "verify_schema.py")
    check("⑨ schema / analyzer 验证", ok, s)
    # 10 artifact auditor 篡改 + 0 findings
    ok, s = suite("auditor", "verify_auditor.py")
    check("⑩ artifact auditor 抓篡改且未篡改 0 findings", ok, s)

    # 11 dry-run 产物审计 0 findings
    dry = ARTIFACTS / "H0-dryrun"
    if dry.exists():
        proc = run([sys.executable, str(RUNNER), "audit", str(dry)])
        audit = json.loads((dry / "audit.json").read_text(encoding="utf-8"))
        fails = [f for f in audit["findings"] if f["level"] == "FAIL"]
        check("⑪ dry-run 产物审计 0 findings",
              proc.returncode == 0 and not fails,
              f"{audit['rows']} rows / {len(fails)} findings")
    else:
        check("⑪ dry-run 产物审计 0 findings", False, "缺少 dry-run 产物")

    # 12 无设计阻断项（结构自检：协议完整、阈值唯一来源）
    frozen_ok = len(pd["frozen_hashes"]) >= 5
    thresholds_ok = protocol.stability_window_ms(pd) == int(pd["budgets"]["stability_window_ms"])
    check("⑫ 无使 H1 失真的设计阻断项（结构自检）", frozen_ok and thresholds_ok,
          f"frozen_files={len(pd['frozen_hashes'])} stability_ms={protocol.stability_window_ms(pd)}")

    passed = sum(1 for c in CHECKS if c["ok"])
    report = {
        "stage": "H0", "kind": "smoke", "created_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(CHECKS), "passed": passed, "failed": len(CHECKS) - passed,
        "checks": CHECKS,
        "warning": "H0 仅校准仪器；不得用于推断 Astra、A/B/C 或项目战略价值。",
    }
    out_dir = ARTIFACTS / "H0-smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "smoke_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[H0 smoke] {passed}/{len(CHECKS)} 完成条件通过 → {out_dir / 'smoke_report.json'}")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
