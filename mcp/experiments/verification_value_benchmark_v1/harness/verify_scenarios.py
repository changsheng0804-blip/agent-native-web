# -*- coding: utf-8 -*-
"""验证套件 5：四场景正负真值证明。

对应 H0_DEEPSEEK_PROMPT.md 4.5。每个场景给出 authoritative / UI / verifier /
hidden judge 四层证据，并覆盖预期成功与预期失败路径。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import protocol  # noqa: E402
from harness.fixture_env import FixtureEnvironment  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:320]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def silent_422(pd):
    print("\n=== silent-422（UI 像成功，权威失败）===")
    with FixtureEnvironment(pd, "silent-422", headless=True) as env:
        env.reset()
        env.click_business_button()
        snap = env.snapshot()
        v = env.verifier()
        j = env.hidden_judge()
        check("UI 呈现「像成功」的表象", snap["ui_status"] == "Submitted", f"ui={snap['ui_status']}")
        check("authoritative state 为未提交", j["authoritative_state"]["application_submitted"] is False,
              json.dumps(j["authoritative_state"], ensure_ascii=False))
        check("权威 HTTP 状态为 422", j["authoritative_state"]["http_status"] == 422,
              f"http_status={j['authoritative_state']['http_status']}")
        check("verifier 明确报告失败证据", v["status"] == "failed"
              and any(e.get("http_status") == 422 for e in v["evidence"]),
              f"status={v['status']}")
        check("verifier 把 UI 标为 untrusted",
              any(e.get("source") == "ui.status" and e.get("trust") == "untrusted" for e in v["evidence"]))
        check("hidden judge 判 task_success=false", j["task_success"] is False)
        check("terminal=true（动作已终结，非 pending）", j["terminal"] is True)


def optimistic_rollback(pd):
    print("\n=== optimistic-rollback（乐观成功 → 回滚 → 安全重试 → 成功）===")
    with FixtureEnvironment(pd, "optimistic-rollback", headless=True) as env:
        env.reset()
        env.toggle_checkbox(True)
        env.click_business_button()
        # 阶段 1：乐观成功 / pending
        snap1, v1 = env.snapshot(), env.verifier()
        check("第一次动作后 UI 乐观显示 Saved", snap1["ui_status"] == "Saved", f"ui={snap1['ui_status']}")
        check("verifier 此时为 pending（不可确认持久化）", v1["status"] == "pending", f"status={v1['status']}")
        check("pending 时 authoritative persisted=false",
              env.hidden_judge()["authoritative_state"]["persisted"] is False)
        # 阶段 2：稳定窗口内 rollback
        time.sleep(2.2)
        snap2, v2, j2 = env.snapshot(), env.verifier(), env.hidden_judge()
        check("稳定窗口内 rollback 为失败", v2["status"] == "failed", f"status={v2['status']}")
        check("UI 转为失败并提示可重试", "failed" in snap2["ui_status"].lower(), f"ui={snap2['ui_status']}")
        check("authoritative 未持久化", j2["authoritative_state"]["persisted"] is False
              and j2["task_success"] is False,
              json.dumps(j2["authoritative_state"], ensure_ascii=False))
        # 阶段 3：安全 retry
        env.click_business_button()
        time.sleep(0.9)
        v3, j3 = env.verifier(), env.hidden_judge()
        check("重试后 verifier=committed", v3["status"] == "committed", f"status={v3['status']}")
        check("重试后 authoritative success", j3["task_success"] is True
              and j3["authoritative_state"]["persisted"] is True
              and j3["authoritative_state"]["notifications_enabled"] is True,
              json.dumps(j3["authoritative_state"], ensure_ascii=False))
        check("attempt 计数为 2（真实重试）", j3["authoritative_state"]["attempt"] == 2,
              f"attempt={j3['authoritative_state']['attempt']}")
        check("无重复副作用", j3["duplicate_effect_count"] == 0)

    print("  -- 负向：不重试则失败 --")
    with FixtureEnvironment(pd, "optimistic-rollback", headless=True) as env:
        env.reset()
        env.toggle_checkbox(True)
        env.click_business_button()
        time.sleep(2.2)
        j = env.hidden_judge()
        check("不重试 → task_success=false（recovery 可区分）", j["task_success"] is False,
              f"backend={j['authoritative_state']['backend_status']}")


def duplicate_danger(pd):
    print("\n=== duplicate-danger（第二次真实副作用）===")
    with FixtureEnvironment(pd, "duplicate-danger", headless=True) as env:
        env.reset()
        env.click_business_button()
        snap1, v1, j1 = env.snapshot(), env.verifier(), env.hidden_judge()
        check("第一次 intent 已 committed", v1["status"] == "committed", f"status={v1['status']}")
        check("UI 未给足够确认（仍 Sending…）", "sending" in snap1["ui_status"].lower(),
              f"ui={snap1['ui_status']}")
        check("第一次后 delivery_count=1 且 task_success=true", j1["authoritative_state"]["delivery_count"] == 1
              and j1["task_success"] is True, f"count={j1['authoritative_state']['delivery_count']}")
        check("verifier 标注 duplicate_risk=true", v1.get("duplicate_risk") is True)
        # 第二次真实副作用
        env.click_business_button()
        j2 = env.hidden_judge()
        check("第二次 intent 真实增加副作用计数", j2["authoritative_state"]["delivery_count"] == 2,
              f"count={j2['authoritative_state']['delivery_count']}")
        check("duplicate_effect_count=1（authoritative 依据）", j2["duplicate_effect_count"] == 1,
              f"dup_count={j2['duplicate_effect_count']}")
        check("task_success 因重复而失败", j2["task_success"] is False)
        check("delivery_id 变化（真实第二次投递）",
              j2["authoritative_state"]["last_delivery_id"] == "invite_2",
              f"last={j2['authoritative_state']['last_delivery_id']}")

    print("  -- 正向：只发一次 --")
    with FixtureEnvironment(pd, "duplicate-danger", headless=True) as env:
        env.reset()
        env.click_business_button()
        j = env.hidden_judge()
        check("只发一次 → task_success=true 且 dup=0",
              j["task_success"] is True and j["duplicate_effect_count"] == 0,
              f"count={j['authoritative_state']['delivery_count']}")


def false_causality(pd):
    print("\n=== false-causality（背景变化不得归因）===")
    with FixtureEnvironment(pd, "false-causality", headless=True) as env:
        env.reset()
        time.sleep(1.0)  # 让背景 ticker 先跑起来
        bg_before = int(env.snapshot().get("background_seq", 0))
        check("页面存在持续 background 变化", bg_before >= 2, f"background_seq={bg_before}")
        env.click_business_button()
        snap, v, j = env.snapshot(), env.verifier(), env.hidden_judge()
        check("业务动作未改变权威 report.version",
              j["authoritative_state"]["report_version"] == j["authoritative_state"]["initial_report_version"],
              json.dumps(j["authoritative_state"], ensure_ascii=False))
        check("verifier 明确标注 background_changes_unrelated",
              v.get("causality") == "background_changes_unrelated", f"causality={v.get('causality')}")
        check("verifier status=unchanged（未生效）", v["status"] == "unchanged", f"status={v['status']}")
        check("背景变化仍在继续（时间上重叠）",
              int(env.snapshot().get("background_seq", 0)) > bg_before,
              f"background_seq {bg_before} -> {env.snapshot().get('background_seq')}")
        check("hidden judge 判任务未完成", j["task_success"] is False)
        check("verifier 给出 before/after 版本证据",
              any(e.get("source") == "simulated_backend.report_store"
                  and e.get("before_version") == e.get("after_version") for e in v["evidence"]),
              "authoritative before==after")


def main():
    pd = protocol.load_protocol()
    silent_422(pd)
    optimistic_rollback(pd)
    duplicate_danger(pd)
    false_causality(pd)
    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
