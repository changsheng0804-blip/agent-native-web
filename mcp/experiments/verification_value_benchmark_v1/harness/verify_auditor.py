# -*- coding: utf-8 -*-
"""验证套件 7：artifact auditor 篡改负向对照 + 未篡改 0 findings。

对应 H0_DEEPSEEK_PROMPT.md 第 8 节。每一行都必须是审计器**真的**抓到的，
而不是"测试自己知道答案"。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
PHASE_DIR = Path(__file__).resolve().parent.parent
RUNNER = PHASE_DIR / "runner.py"
sys.path.insert(0, str(PHASE_DIR))
from harness import protocol  # noqa: E402

CHECKS = []
# 用真实 dry-run 产物作为基线（由 verify_all.py 保证先生成）
BASELINE = PHASE_DIR.parent / "artifacts" / "verification_value_benchmark_v1" / "H0-dryrun"


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def audit(run_dir: Path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, str(RUNNER), "audit", str(run_dir)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    findings = []
    audit_file = run_dir / "audit.json"
    if audit_file.exists():
        findings = json.loads(audit_file.read_text(encoding="utf-8"))["findings"]
    return proc.returncode, findings


def mutate(mutator, label, expect_fail=True):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "run"
        shutil.copytree(BASELINE, tmp)
        (tmp / "audit.json").unlink(missing_ok=True)
        mutator(tmp)
        code, findings = audit(tmp)
        fails = [f for f in findings if f["level"] == "FAIL"]
        if expect_fail:
            return check(f"审计能抓到：{label}", code != 0 and bool(fails),
                         (fails[0]["message"][:150] if fails else "未报错"))
        return check(f"未篡改基线：{label}", code == 0 and not fails,
                     f"code={code} findings={len(findings)}")


def _rows(tmp: Path):
    return [json.loads(l) for l in (tmp / "runs.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _write_rows(tmp: Path, rows):
    (tmp / "runs.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _first_claimed(rows):
    return next((r for r in rows if r.get("claimed_success")), rows[0])


def main():
    if not BASELINE.exists():
        print(f"缺少基线产物 {BASELINE}；请先运行 runner.py run")
        return 1

    print("=== 8.0 正向对照：未篡改产物必须 0 findings ===")
    mutate(lambda tmp: None, "未篡改产物", expect_fail=False)

    print("=== 8.1 伪造 task_success ===")
    def m_task_success(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if not r["task_success"]), rows[0])
        target["task_success"] = True
        _write_rows(tmp, rows)
    mutate(m_task_success, "伪造 task_success=true")

    print("=== 8.2 伪造/抹掉 false_positive ===")
    def m_fp(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["false_positive"]), rows[0])
        target["false_positive"] = False
        _write_rows(tmp, rows)
    mutate(m_fp, "抹掉 false_positive")

    print("=== 8.3 隐藏 premature completion ===")
    def m_premature(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["premature_completion"]), None)
        if target is None:
            # 若基线无 premature 行，则构造：删掉 first_success_claim_ms 与 raw 记录
            target = _first_claimed(rows)
            target["premature_completion"] = True
            _write_rows(tmp, rows)
            raw = tmp / target["raw_trace_path"]
            data = json.loads(raw.read_text(encoding="utf-8"))
            data["first_success_claim"] = None
            raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return
        target["premature_completion"] = False
        _write_rows(tmp, rows)
    mutate(m_premature, "隐藏 premature completion（改 first claim 时间/记录）")

    print("=== 8.4 删除 duplicate side effect ===")
    def m_dup(tmp):
        """只改 runs.jsonl（更接近真实篡改），raw 保持真值 → 必须被发现不一致。"""
        rows = _rows(tmp)
        target = next((r for r in rows if r["unsafe_duplicate_effect"]), None)
        if target is None:
            target = _first_claimed(rows)
        target["unsafe_duplicate_effect"] = False
        _write_rows(tmp, rows)
    mutate(m_dup, "删除 duplicate side effect（只改 runs.jsonl）")

    def m_dup_raw(tmp):
        """同时改 raw 的 duplicate_effect_count → 必须被 authoritative_state 交叉核对抓到。"""
        rows = _rows(tmp)
        target = next((r for r in rows if r["unsafe_duplicate_effect"]), None)
        if target is None:
            return
        target["unsafe_duplicate_effect"] = False
        _write_rows(tmp, rows)
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        if data.get("ground_truth"):
            data["ground_truth"]["duplicate_effect_count"] = 0
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_dup_raw, "删除 duplicate side effect（同时改 raw，靠 authoritative 交叉核对）")

    print("=== 8.5 A 臂注入 verifier ===")
    def m_a_verifier(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["arm"] == "A"), rows[0])
        target["verifier_packets"] = 1
        _write_rows(tmp, rows)
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        data["verifier_packets"] = [{"step": 1, "packet": "{}"}]
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_a_verifier, "A 臂注入 verifier packet")

    print("=== 8.6 B 臂启用 gate ===")
    def m_b_gate(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["arm"] == "B"), None)
        if target is None:
            return
        target["gate_blocks"] = 1
        _write_rows(tmp, rows)
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        data["gate_decisions"] = [{"step": 1, "allowed": False, "reason": "x"}]
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_b_gate, "B 臂启用 gate")

    print("=== 8.7 B/C verifier packet 不一致 / C 收到额外策略提示 ===")
    def m_bc_mismatch(tmp):
        """B 与 C 在相同 task/状态下的 verifier packet 被改成不同 → 必须被发现。"""
        rows = _rows(tmp)
        # 找同一 task 的 B 与 C
        tasks_seen = {}
        for r in rows:
            tasks_seen.setdefault(r["task_id"], {})[r["arm"]] = r
        pair = next(((v.get("B"), v.get("C")) for v in tasks_seen.values()
                     if v.get("B") and v.get("C")), None)
        if pair is None:
            return
        b_row, c_row = pair
        b_raw = json.loads((tmp / b_row["raw_trace_path"]).read_text(encoding="utf-8"))
        c_raw = json.loads((tmp / c_row["raw_trace_path"]).read_text(encoding="utf-8"))
        if not (b_raw.get("verifier_packets") and c_raw.get("verifier_packets")):
            return
        # 篡改 C 的 packet（模拟 B/C 不等价）
        pkt = json.loads(c_raw["verifier_packets"][0]["packet"])
        pkt["status"] = "committed"  # 与 B 的真实状态不同
        c_raw["verifier_packets"][0]["packet"] = json.dumps(pkt, ensure_ascii=False, sort_keys=True)
        (tmp / c_row["raw_trace_path"]).write_text(
            json.dumps(c_raw, ensure_ascii=False, indent=2), encoding="utf-8")
    mutate(m_bc_mismatch, "B/C verifier packet 不一致")

    def m_c_advice(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["arm"] == "C" and r["verifier_packets"]), None)
        if target is None:
            return
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        if data.get("verifier_packets"):
            pkt = data["verifier_packets"][0]["packet"]
            data["verifier_packets"][0]["packet"] = pkt[:-1] + ', "advice": "你应该重试"}'
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_c_advice, "C 收到额外策略提示")

    print("=== 8.8 hiddenJudge 泄漏进模型输入 ===")
    def m_leak(tmp):
        rows = _rows(tmp)
        target = _first_claimed(rows)
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        data["model_input"] = (data.get("model_input") or []) + [
            {"role": "system", "content": "hiddenJudge: authoritative_state leaked"}]
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_leak, "hiddenJudge 出现在模型输入")

    print("=== 8.9 raw trace 缺失 ===")
    def m_missing_raw(tmp):
        rows = _rows(tmp)
        (tmp / rows[0]["raw_trace_path"]).unlink()
    mutate(m_missing_raw, "raw trace 缺失")

    print("=== 8.10 invalid run reason 为空 ===")
    def m_invalid_reason(tmp):
        rows = _rows(tmp)
        target = rows[0]
        target["invalid_run"] = True
        target["invalid_reason"] = None
        _write_rows(tmp, rows)
    mutate(m_invalid_reason, "invalid run reason 为空")

    print("=== 8.11 runs.jsonl 与 raw authoritative final state 不一致 ===")
    def m_state_mismatch(tmp):
        rows = _rows(tmp)
        target = rows[0]
        target["final_judge"]["authoritative_state"] = {"tampered": True}
        _write_rows(tmp, rows)
    mutate(m_state_mismatch, "runs.jsonl 与 raw authoritative state 不一致")

    print("=== 8.12 冻结配置 hash 漂移 ===")
    def m_hash(tmp):
        manifest = tmp / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for k in data.get("frozen_hashes", {}):
            data["frozen_hashes"][k] = "0" * 64
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    mutate(m_hash, "冻结配置 hash 漂移")

    print("=== 8.13 工具面异常（跨臂能力不一致）===")
    def m_tools(tmp):
        rows = _rows(tmp)
        target = next((r for r in rows if r["arm"] == "A"), rows[0])
        raw = tmp / target["raw_trace_path"]
        data = json.loads(raw.read_text(encoding="utf-8"))
        data["arm_capabilities"]["tools"] = ["screenshot", "finish"]  # 缺 Computer Use
        raw.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mutate(m_tools, "A 臂工具面缺少 Computer Use")

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
