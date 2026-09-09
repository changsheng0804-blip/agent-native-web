# -*- coding: utf-8 -*-
"""runs.jsonl 的 schema 校验器。

schema 是冻结协议（result.schema.json），只读使用。
schema 的 allOf 只要求 invalid_run=true 时 invalid_reason **键存在**；
HARNESS_HANDOFF 要求「invalid run 必须有非空 reason」，
因此执行器额外强制非空字符串——这是落实协议，不是修改判定规则。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import protocol


def validate_row(row: dict, schema: dict):
    """校验单行。失败抛 SystemExit（fail fast，不静默）。"""
    import jsonschema
    try:
        jsonschema.validate(instance=row, schema=schema)
    except Exception as exc:
        raise SystemExit(f"run {row.get('run_id')} 不符合 result.schema.json：{exc}")
    if row.get("invalid_run") and not str(row.get("invalid_reason") or "").strip():
        raise SystemExit(
            f"run {row.get('run_id')}: invalid_run=true 但缺少有效 invalid_reason"
            "（HARNESS_HANDOFF：invalid run 必须有非空 reason）")


def load_rows(path: Path, schema: dict | None = None) -> list:
    """读取并逐行校验 runs.jsonl。空输入/非法 JSON 明确报错。"""
    if not path.exists():
        raise SystemExit(f"缺少 {path}")
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        if schema is not None:
            try:
                validate_row(row, schema)
            except SystemExit as exc:
                raise SystemExit(f"第 {lineno} 行：{exc}") from exc
        rows.append(row)
    if not rows:
        raise SystemExit(f"{path} 为空")
    return rows
