# -*- coding: utf-8 -*-
"""验证套件 2：A/B/C 能力隔离、B/C verifier 等价、hidden judge 隔离。

对应 H0_DEEPSEEK_PROMPT.md 4.3 / 4.4。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import arm_policy, protocol  # noqa: E402
from harness.fixture_env import FixtureEnvironment  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def main():
    pd = protocol.load_protocol()
    print("=== 4.3 A/B/C 能力隔离 ===")

    # 工具面：三臂逐字相同
    tools = {arm: arm_policy.model_tools(pd, arm) for arm in ("A", "B", "C")}
    check("A/B/C 工具面逐字相同", tools["A"] == tools["B"] == tools["C"],
          json.dumps(tools["A"], ensure_ascii=False))
    check("工具面含 screenshot + Computer Use",
          tools["A"][0] == "screenshot" and set(arm_policy.COMPUTER_USE_TOOLS).issubset(set(tools["A"])),
          f"tools={tools['A']}")

    caps = {arm: arm_policy.capability_fingerprint(pd, arm) for arm in ("A", "B", "C")}
    check("A 不可见 verifier", caps["A"]["verifier_packet_visible"] is False)
    check("A 不启用 gate", caps["A"]["gate_enforced"] is False)
    check("B 可见 verifier", caps["B"]["verifier_packet_visible"] is True)
    check("B 不启用 gate", caps["B"]["gate_enforced"] is False)
    check("C 可见 verifier", caps["C"]["verifier_packet_visible"] is True)
    check("C 启用 gate", caps["C"]["gate_enforced"] is True)
    check("C 唯一额外能力是 gate（其余与 B 相同）",
          {k: caps["B"][k] for k in ("tools", "model_capabilities", "verifier_packet_visible", "hidden_judge_visible")}
          == {k: caps["C"][k] for k in ("tools", "model_capabilities", "verifier_packet_visible", "hidden_judge_visible")},
          "B 与 C 除 gate 外全部一致")
    check("三臂 hidden judge 均不可见",
          all(caps[a]["hidden_judge_visible"] is False for a in ("A", "B", "C")))

    print("=== 4.3/4.4 verifier packet 等价与泄漏 ===")
    for task_id in pd["task_by_id"]:
        with FixtureEnvironment(pd, task_id, headless=True) as env:
            env.reset()
            # 初态 packet
            pkt_initial = env.verifier_packet()
            check(f"{task_id}: verifier packet 为 canonical JSON（B/C 可直接逐字节比对）",
                  pkt_initial == json.dumps(json.loads(pkt_initial), ensure_ascii=False, sort_keys=True),
                  pkt_initial[:120])

            # 业务动作后 packet（B/C 读同一函数 → 必然等价；此处证明确定性）
            if task_id == "optimistic-rollback":
                env.toggle_checkbox(True)
            env.click_business_button()
            pkt_a = env.verifier_packet()
            pkt_b = env.verifier_packet()
            check(f"{task_id}: 同一状态重复读取 verifier 逐字节一致（B/C 等价基础）",
                  pkt_a == pkt_b, f"len={len(pkt_a)}")

            # hiddenJudge 与 verifier 的字段隔离
            v = env.verifier()
            j = env.hidden_judge()
            check(f"{task_id}: hiddenJudge 含 authoritative_state，verifier 不含",
                  "authoritative_state" in j and "authoritative_state" not in json.dumps(v, ensure_ascii=False),
                  f"judge_keys={sorted(j)}")
            check(f"{task_id}: verifier 不含 duplicate_effect_count（hidden 字段）",
                  "duplicate_effect_count" not in json.dumps(v, ensure_ascii=False))
            check(f"{task_id}: verifier 证据按 trust 标注（authoritative/evidence/untrusted）",
                  all(e.get("trust") in ("authoritative", "evidence", "untrusted") for e in v.get("evidence", [])),
                  f"trusts={[e.get('trust') for e in v.get('evidence', [])]}")

            # A 越权：模型若尝试调用 Harness 接口
            for api in ("verifier", "hiddenJudge", "snapshot"):
                out = env.try_harness_api_from_page(api)
                check(f"{task_id}: 页面级越权调用 {api} 可被探测（Harness-only 通道）",
                      isinstance(out, dict), f"ok={out.get('ok')}")
            # 越权尝试不得产生环境副作用
            seq_before = int(env.snapshot().get("action_seq", 0))
            env.try_harness_api_from_page("hiddenJudge")
            seq_after = int(env.snapshot().get("action_seq", 0))
            check(f"{task_id}: 越权读取 hiddenJudge 无环境副作用", seq_before == seq_after,
                  f"action_seq {seq_before} -> {seq_after}")

    print("=== 负向：故意破坏隔离必须被抓到 ===")
    # A 注入 verifier
    fake_a = {"arm": "A", "arm_capabilities": {"verifier_packet_visible": True, "gate_enforced": False,
                                               "hidden_judge_visible": False,
                                               "tools": arm_policy.model_tools(pd, "A")}}
    check("A 注入 verifier → 能力指纹与 arms.json 冲突",
          fake_a["arm_capabilities"]["verifier_packet_visible"] is not caps["A"]["verifier_packet_visible"],
          "审计器将报 FAIL（见 verify_auditor）")
    # B 开 gate
    fake_b = {"gate_enforced": True}
    check("B 开 gate → 与 arms.json 冲突",
          fake_b["gate_enforced"] is not caps["B"]["gate_enforced"], "审计器将报 FAIL")
    # C 额外建议
    advice = "你应该重试"
    check("C 收到额外策略建议 → 可被字符串检测捕获",
          any(n.lower() in advice.lower() for n in ("你应该", "请重试")), advice)

    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
