# -*- coding: utf-8 -*-
"""A/B/C 工具面与能力隔离（唯一事实来源：arms.json）。

H0 要求机器验证：
  - A/B/C 截图与 Computer Use 能力**完全一致**；
  - A 完全看不到 verifier；
  - B/C verifier packet 等价；
  - C 唯一多出的能力是确定性 gate enforcement。

本模块只做「把 arms.json 翻译成工具面」，不内联任何臂的权限。
"""
from __future__ import annotations

from . import protocol

# 所有臂的感知/行动工具完全一致（README 公平性规则 1）
SCREENSHOT_TOOL = "screenshot"
COMPUTER_USE_TOOLS = ("mouse_click", "mouse_drag", "key_press", "mouse_move")
FINISH_TOOL = "finish"

# 对模型完全不可见的 Harness-only 接口（arms.json.forbidden_for_all_models）
HARNESS_ONLY = (
    "verificationBench.hiddenJudge",
    "verificationBench.authoritativeState",
    "verificationBench.snapshot",
    "direct_javascript_eval",
    "dom_inspection_tool",
)


def model_tools(protocol_data: dict, arm_id: str) -> list:
    """模型可见工具列表。对 A/B/C 逐字相同（能力一致性的机器证据）。"""
    protocol.arm_spec(protocol_data, arm_id)  # 不存在则 fail fast
    return [SCREENSHOT_TOOL, *COMPUTER_USE_TOOLS, FINISH_TOOL]


def capability_fingerprint(protocol_data: dict, arm_id: str) -> dict:
    """臂的能力指纹，用于证明 A/B/C 感知/行动能力一致。"""
    arm = protocol.arm_spec(protocol_data, arm_id)
    return {
        "model_capabilities": list(arm["model_capabilities"]),
        "tools": model_tools(protocol_data, arm_id),
        "verifier_packet_visible": bool(arm["verifier_packet_visible"]),
        "gate_enforced": bool(arm["gate_enforced"]),
        "hidden_judge_visible": bool(arm["hidden_judge_visible"]),
    }


def verifier_visible(protocol_data: dict, arm_id: str) -> bool:
    return bool(protocol.arm_spec(protocol_data, arm_id)["verifier_packet_visible"])


def gate_enforced(protocol_data: dict, arm_id: str) -> bool:
    return bool(protocol.arm_spec(protocol_data, arm_id)["gate_enforced"])


def hidden_judge_visible(protocol_data: dict, arm_id: str) -> bool:
    return bool(protocol.arm_spec(protocol_data, arm_id)["hidden_judge_visible"])


def isolation_matrix(protocol_data: dict) -> dict:
    """返回 {arm: {verifier, gate, hidden_judge, tools}} 供审计比对。"""
    return {arm: capability_fingerprint(protocol_data, arm) for arm in ("A", "B", "C")}
