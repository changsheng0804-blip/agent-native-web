# -*- coding: utf-8 -*-
"""冻结协议的只读加载器 + 配置指纹。

原则（H0_DEEPSEEK_PROMPT.md 第 2 节）：
  任务文本、成功条件、A/B/C 能力边界、verifier 语义、gate policy、P0 口径
  全部视为预注册实验协议，只能读取，不能在执行器里复制第二套常量。

所有阈值（stability_window_ms 等）都从 tasks.json / arms.json 读取。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

PHASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PHASE_DIR.parents[2]

README_FILE = PHASE_DIR / "README.md"
TASKS_FILE = PHASE_DIR / "tasks.json"
ARMS_FILE = PHASE_DIR / "arms.json"
SCHEMA_FILE = PHASE_DIR / "result.schema.json"
HANDOFF_FILE = PHASE_DIR / "HARNESS_HANDOFF.md"
H0_PROMPT_FILE = PHASE_DIR / "H0_DEEPSEEK_PROMPT.md"

# 冻结文件：任何改动都必须被审计器发现
FROZEN_FILES = (README_FILE, TASKS_FILE, ARMS_FILE, SCHEMA_FILE, HANDOFF_FILE)

EXPECTED_TASK_IDS = ("silent-422", "optimistic-rollback", "duplicate-danger", "false-causality")

# 输出目录（gitignore: mcp/experiments/artifacts/）
DEFAULT_ARTIFACTS = PHASE_DIR.parent / "artifacts" / "verification_value_benchmark_v1"


def _load_json(path: Path):
    if not path.exists():
        raise SystemExit(f"冻结协议文件缺失：{path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_hashes() -> dict:
    """冻结文件的 SHA256 指纹，用于审计配置漂移。"""
    return {p.name: file_hash(p) for p in FROZEN_FILES if p.exists()}


def load_protocol() -> dict:
    """加载并自检全部冻结协议事实。任何不一致 fail fast。"""
    for path in (README_FILE, TASKS_FILE, ARMS_FILE, SCHEMA_FILE, HANDOFF_FILE):
        if not path.exists():
            raise SystemExit(f"冻结协议文件缺失：{path}")

    tasks = _load_json(TASKS_FILE)
    arms = _load_json(ARMS_FILE)
    schema = _load_json(SCHEMA_FILE)

    errors = []
    if tasks.get("benchmark_id") != "verification-value-benchmark-v1":
        errors.append(f"benchmark_id 异常：{tasks.get('benchmark_id')!r}")
    ids = tuple(t.get("task_id") for t in tasks.get("tasks", []))
    if ids != EXPECTED_TASK_IDS:
        errors.append(f"task 集合异常：{ids!r}")
    if set(arms.get("arms", {})) != {"A", "B", "C"}:
        errors.append(f"arms 必须为 A/B/C，实际：{sorted(arms.get('arms', {}))}")

    # A/B/C 能力边界自检：A 无 verifier；B 有 verifier 无 gate；C 有 verifier 有 gate
    expect = {
        "A": {"verifier_packet_visible": False, "gate_enforced": False, "hidden_judge_visible": False},
        "B": {"verifier_packet_visible": True, "gate_enforced": False, "hidden_judge_visible": False},
        "C": {"verifier_packet_visible": True, "gate_enforced": True, "hidden_judge_visible": False},
    }
    for arm_id, want in expect.items():
        arm = arms.get("arms", {}).get(arm_id) or {}
        for key, value in want.items():
            if bool(arm.get(key)) is not value:
                errors.append(f"arm {arm_id}.{key} 期望 {value}，实际 {arm.get(key)!r}")
        caps = arm.get("model_capabilities")
        if caps != ["screenshot", "computer_use"]:
            errors.append(f"arm {arm_id}.model_capabilities 必须为 screenshot+computer_use，实际 {caps!r}")

    gate = arms.get("gate_policy") or {}
    for status in ("pending", "failed", "unchanged", "uncertain", "conflict", "committed"):
        if status not in gate:
            errors.append(f"gate_policy 缺少状态：{status}")

    budgets = tasks.get("budgets") or {}
    for key in ("max_wall_time_seconds", "max_agent_steps", "stability_window_ms"):
        if not isinstance(budgets.get(key), int) or budgets[key] <= 0:
            errors.append(f"budgets.{key} 异常：{budgets.get(key)!r}")

    if errors:
        raise SystemExit("冻结协议自检失败：\n  - " + "\n  - ".join(errors))

    return {
        "tasks": tasks,
        "arms": arms,
        "schema": schema,
        "budgets": budgets,
        "task_by_id": {t["task_id"]: t for t in tasks["tasks"]},
        "frozen_hashes": frozen_hashes(),
    }


def task_spec(protocol: dict, task_id: str) -> dict:
    spec = protocol["task_by_id"].get(task_id)
    if spec is None:
        raise SystemExit(f"tasks.json 中不存在 task {task_id!r}")
    return spec


def arm_spec(protocol: dict, arm_id: str) -> dict:
    arm = protocol["arms"]["arms"].get(arm_id)
    if arm is None:
        raise SystemExit(f"arms.json 中不存在 arm {arm_id!r}")
    return arm


def fixture_path(protocol: dict, task_id: str) -> Path:
    rel = task_spec(protocol, task_id)["fixture"]
    path = REPO_ROOT / rel
    if not path.exists():
        raise SystemExit(f"fixture 缺失：{path}")
    return path


def stability_window_ms(protocol: dict) -> int:
    return int(protocol["budgets"]["stability_window_ms"])


def gate_policy(protocol: dict) -> dict:
    return protocol["arms"]["gate_policy"]
