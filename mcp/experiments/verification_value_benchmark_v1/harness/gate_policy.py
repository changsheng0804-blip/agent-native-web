# -*- coding: utf-8 -*-
"""Gate policy 引擎（唯一事实来源：arms.json.gate_policy）。

Gate 是 **Harness enforcement**，不是提示工程（HARNESS_HANDOFF.md "Gate 行为"）。
本模块把 arms.json 的 gate_policy 翻译成确定性纯函数，供 runner 与测试共用。

关键约束：
  - 不内联任何状态/动作白名单，全部从 arms.json 读取；
  - 每次阻断都返回结构化原因，供 raw trace 记录并累加 gate_blocks；
  - 只对 C 臂启用（B/A 由 runner 依据 arms.json 决定，本模块不自行判断）。
"""
from __future__ import annotations

from . import protocol

# 模型可提出的动作类型（与 gate_policy 的 allow 词汇对齐）
ACTION_FINAL_SUCCESS = "final_success"
ACTION_NEW_IRREVERSIBLE_INTENT = "new_irreversible_intent"
ACTION_WAIT = "wait"
ACTION_REOBSERVE = "reobserve"
ACTION_RETRY = "retry"
ACTION_REPLAN = "replan"
ACTION_HANDOFF = "handoff"
ACTION_REPORT_FAILURE = "report_failure"
ACTION_REPORT_NOT_EFFECTED = "report_not_effected"
ACTION_CONTINUE = "continue"

ALL_ACTIONS = (
    ACTION_FINAL_SUCCESS,
    ACTION_NEW_IRREVERSIBLE_INTENT,
    ACTION_WAIT,
    ACTION_REOBSERVE,
    ACTION_RETRY,
    ACTION_REPLAN,
    ACTION_HANDOFF,
    ACTION_REPORT_FAILURE,
    ACTION_REPORT_NOT_EFFECTED,
    ACTION_CONTINUE,
)

GATE_STATUSES = ("pending", "failed", "unchanged", "uncertain", "conflict", "committed")


def _allow_tokens(policy_entry: dict) -> set:
    return set(policy_entry.get("allow") or [])


def _retry_allowed(policy_entry: dict, recoverable: bool) -> bool:
    """safe_retry_if_task_recoverable 只在任务可恢复时生效。"""
    return "safe_retry_if_task_recoverable" in _allow_tokens(policy_entry) and bool(recoverable)


def _token_for_action(action: str) -> str:
    """把动作映射到 gate_policy.allow 词汇。"""
    return {
        ACTION_WAIT: "wait",
        ACTION_REOBSERVE: "reobserve",
        ACTION_REPLAN: "replan",
        ACTION_HANDOFF: "handoff",
        ACTION_REPORT_FAILURE: "report_failure",
        ACTION_REPORT_NOT_EFFECTED: "report_not_effected",
        ACTION_CONTINUE: "continue",
    }.get(action, action)


def decide(protocol_data: dict, status: str, action: str, task: dict,
           intent_already_committed: bool = False) -> dict:
    """Gate 判定纯函数。

    参数：
      status   verifier/authoritative 状态（gate_policy 的键）
      action   模型想做的动作（ALL_ACTIONS）
      task     tasks.json 中的 task 定义（recoverable / duplicate_risk）
      intent_already_committed  该 intent 是否已有 committed 记录

    返回：{allowed, reason, status, action, gate_block}
    """
    policy = protocol.gate_policy(protocol_data)
    if status not in policy:
        raise SystemExit(f"gate_policy 缺少状态 {status!r}")
    if action not in ALL_ACTIONS:
        raise SystemExit(f"未知 gate 动作 {action!r}")

    entry = policy[status]
    tokens = _allow_tokens(entry)
    recoverable = bool(task.get("recoverable"))
    duplicate_risk = bool(task.get("duplicate_risk"))

    def blocked(reason: str) -> dict:
        return {"allowed": False, "reason": reason, "status": status,
                "action": action, "gate_block": True}

    def allowed(reason: str) -> dict:
        return {"allowed": True, "reason": reason, "status": status,
                "action": action, "gate_block": False}

    # ── 完成声明 ──────────────────────────────────────────────
    if action == ACTION_FINAL_SUCCESS:
        if status == "committed":
            if "final_success_if_task_postcondition_met" in tokens:
                return allowed("committed：允许在任务后置条件满足时声明完成")
            return blocked("committed 但策略未允许 final_success")
        if entry.get("block_final_success") or entry.get("block_forward_success"):
            return blocked(f"{status}：禁止声明完成（gate_policy.block_*_success=true）")
        return blocked(f"{status}：gate_policy 未允许声明完成")

    # ── 新的不可逆业务动作 ────────────────────────────────────
    if action == ACTION_NEW_IRREVERSIBLE_INTENT:
        if status == "committed" and duplicate_risk and intent_already_committed:
            if entry.get("block_duplicate_same_intent_when_duplicate_risk"):
                return blocked(
                    "committed + duplicate_risk + 该 intent 已执行：禁止再次执行同一 intent"
                    "（gate_policy.block_duplicate_same_intent_when_duplicate_risk=true）")
        if status == "committed":
            if "continue" in tokens:
                return allowed("committed：允许继续（非重复 intent）")
            return blocked("committed 但策略未允许继续")
        if entry.get("block_new_irreversible_intent"):
            return blocked(f"{status}：禁止新的不可逆 intent（block_new_irreversible_intent=true）")
        return blocked(f"{status}：gate_policy 未允许新的不可逆 intent")

    # ── 安全重试 ──────────────────────────────────────────────
    if action == ACTION_RETRY:
        if _retry_allowed(entry, recoverable):
            return allowed(f"{status}：任务可恢复，允许安全 retry")
        if "safe_retry_if_task_recoverable" in tokens and not recoverable:
            return blocked(f"{status}：策略允许 retry 但任务 recoverable=false")
        return blocked(f"{status}：gate_policy 未允许 retry")

    # ── 其余动作（wait/reobserve/replan/handoff/report_*）────
    token = _token_for_action(action)
    if token in tokens:
        return allowed(f"{status}：策略允许 {token}")
    return blocked(f"{status}：gate_policy 未允许 {token}")


def full_matrix(protocol_data: dict) -> list:
    """表驱动全矩阵：状态 × 动作 × 任务形态。

    返回逐行结果，供测试与 H0 报告引用。
    """
    rows = []
    tasks = protocol_data["tasks"]["tasks"]
    # 用两个代表任务覆盖 recoverable / duplicate_risk 组合
    shapes = []
    for task in tasks:
        key = (bool(task["recoverable"]), bool(task["duplicate_risk"]))
        if key not in [(s["recoverable"], s["duplicate_risk"]) for s in shapes]:
            shapes.append({"task_id": task["task_id"], "recoverable": key[0], "duplicate_risk": key[1]})
    for status in GATE_STATUSES:
        for action in ALL_ACTIONS:
            for shape in shapes:
                for committed in (False, True):
                    if action != ACTION_NEW_IRREVERSIBLE_INTENT and committed:
                        continue  # committed 只对重复 intent 判定有意义
                    rows.append({
                        "status": status,
                        "action": action,
                        "task_id": shape["task_id"],
                        "recoverable": shape["recoverable"],
                        "duplicate_risk": shape["duplicate_risk"],
                        "intent_already_committed": committed,
                        **decide(protocol_data, status, action, shape, committed),
                    })
    return rows
