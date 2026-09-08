# -*- coding: utf-8 -*-
"""汇总 Astra Canvas Phase 1 的 runs.jsonl。

仅做描述性统计；Pilot 每臂 5 次，不用于强统计显著性结论。
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path


def mean(rows, key):
    values = [row[key] for row in rows]
    return round(statistics.mean(values), 3) if values else None


def metric_mean(rows, key):
    values = [row.get("metrics", {}).get(key) for row in rows]
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.mean(values), 3) if values else None


def summarize(rows):
    valid = [r for r in rows if not r.get("invalid_run")]
    if not valid:
        return {"valid_runs": 0}
    successes = sum(bool(r.get("success")) for r in valid)
    fps = sum(bool(r.get("false_positive")) for r in valid)
    return {
        "valid_runs": len(valid),
        "invalid_runs": len(rows) - len(valid),
        "successes": successes,
        "success_rate": round(successes / len(valid), 3),
        "false_positives": fps,
        "false_positive_rate": round(fps / len(valid), 3),
        "mean_elapsed_ms": mean(valid, "elapsed_ms"),
        "mean_steps": mean(valid, "steps"),
        "mean_total_actions": metric_mean(valid, "total_actions"),
        "mean_corrections": metric_mean(valid, "corrections"),
        "mean_computer_use_actions": metric_mean(valid, "computer_use_actions"),
        "mean_screenshots": metric_mean(valid, "screenshots"),
        "mean_structured_observations": metric_mean(valid, "structured_observations"),
        "mean_structured_actions": metric_mean(valid, "structured_actions"),
        "mean_verify_calls": metric_mean(valid, "verify_calls"),
        "mean_final_center_error_px": round(
            statistics.mean(float(r["final_ground_truth"]["center_error_px"]) for r in valid), 3
        ),
    }


def main():
    if len(sys.argv) != 2:
        raise SystemExit("用法: python analyze_results.py runs.jsonl")
    path = Path(sys.argv[1])
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"第 {lineno} 行 JSON 无效: {exc}") from exc
            if row.get("arm") not in {"A", "B", "C"}:
                raise SystemExit(f"第 {lineno} 行 arm 无效: {row.get('arm')!r}")
            rows.append(row)

    grouped = defaultdict(list)
    for row in rows:
        grouped[row["arm"]].append(row)

    result = {
        "total_rows": len(rows),
        "arms": {arm: summarize(grouped.get(arm, [])) for arm in ("A", "B", "C")},
        "notes": [
            "Pilot 每臂目标为 5 个有效 runs；样本很小，只用于发现明显信号。",
            "不能仅凭平均值宣称统计显著性。",
            "优先检查 success、false_positive，再看 elapsed/actions/corrections。",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
