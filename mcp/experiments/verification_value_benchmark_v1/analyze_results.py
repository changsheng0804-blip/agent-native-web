# -*- coding: utf-8 -*-
"""Verification Value Benchmark v1 描述性汇总。

只做预注册指标的机械聚合；不做显著性检验，也不自动生成战略结论。
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def load_rows(path: Path):
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"第 {lineno} 行不是合法 JSON: {exc}") from exc
        rows.append(row)
    if not rows:
        raise SystemExit("runs.jsonl 为空")
    return rows


def pct(n, d):
    return round(n / d, 4) if d else None


def summarize(rows):
    grouped = defaultdict(list)
    invalid = []
    for row in rows:
        if row.get("invalid_run"):
            if not row.get("invalid_reason"):
                raise SystemExit(f"invalid run 缺 reason: {row.get('run_id')}")
            invalid.append(row)
            continue
        grouped[(row.get("task_id"), row.get("arm"))].append(row)

    summary = {"effective_runs": sum(len(v) for v in grouped.values()),
               "invalid_runs": len(invalid), "groups": {}}

    for (task_id, arm), items in sorted(grouped.items()):
        n = len(items)
        recoverable = [x for x in items if x.get("recovery_attempted")]
        key = f"{task_id}:{arm}"
        summary["groups"][key] = {
            "task_id": task_id,
            "arm": arm,
            "runs": n,
            "task_success_rate": pct(sum(bool(x.get("task_success")) for x in items), n),
            "false_positive_rate": pct(sum(bool(x.get("false_positive")) for x in items), n),
            "premature_completion_rate": pct(sum(bool(x.get("premature_completion")) for x in items), n),
            "unsafe_duplicate_effect_rate": pct(sum(bool(x.get("unsafe_duplicate_effect")) for x in items), n),
            "recovery_success_rate": (
                pct(sum(x.get("recovery_success") is True for x in recoverable), len(recoverable))
                if recoverable else None
            ),
            "mean_elapsed_ms": round(statistics.mean(x["elapsed_ms"] for x in items), 1),
            "median_elapsed_ms": round(statistics.median(x["elapsed_ms"] for x in items), 1),
            "mean_computer_use_actions": round(statistics.mean(x["computer_use_actions"] for x in items), 2),
            "mean_retries": round(statistics.mean(x["retries"] for x in items), 2),
            "mean_verifier_packets": round(statistics.mean(x["verifier_packets"] for x in items), 2),
            "mean_gate_blocks": round(statistics.mean(x["gate_blocks"] for x in items), 2),
            "mean_pending_wait_ms": round(statistics.mean(x.get("pending_wait_ms", 0) for x in items), 1),
        }
    return summary


def main():
    if len(sys.argv) != 2:
        raise SystemExit("用法: python analyze_results.py runs.jsonl")
    path = Path(sys.argv[1])
    summary = summarize(load_rows(path))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
