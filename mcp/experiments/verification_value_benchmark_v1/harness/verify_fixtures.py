# -*- coding: utf-8 -*-
"""验证套件 1：fixture reset 确定性、timer/run 隔离、真实交互通道。

对应 H0_DEEPSEEK_PROMPT.md 4.1 / 4.2。
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
    CHECKS.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def fingerprint(snap: dict) -> dict:
    """authoritative/事件序号/UI 初态的可比较指纹。"""
    return {k: v for k, v in snap.items() if k != "events"}


def run_task(task_id: str, pd: dict):
    print(f"\n=== {task_id} ===")
    with FixtureEnvironment(pd, task_id, headless=True) as env:
        # ── 4.1 三次独立 reset 确定性 ────────────────────────
        fps, judges, verifiers, events = [], [], [], []
        for _ in range(3):
            snap = env.reset()
            fps.append(fingerprint(snap))
            judges.append(env.hidden_judge())
            verifiers.append(env.verifier())
            events.append(list(snap.get("events") or []))
        check("reset ×3 authoritative/UI 指纹一致",
              all(f == fps[0] for f in fps), json.dumps(fps[0], ensure_ascii=False)[:160])
        check("reset ×3 hidden judge 初态一致", all(j == judges[0] for j in judges),
              json.dumps(judges[0].get("authoritative_state"), ensure_ascii=False)[:140])
        check("reset ×3 verifier 初态一致", all(v == verifiers[0] for v in verifiers),
              f"status={verifiers[0].get('status')}")
        check("reset 后 events 为空（事件序号归零）", all(e == [] for e in events),
              f"events={events[0]}")

        # ── 4.2 真实交互：点击必须触发业务动作 ───────────────
        if task_id == "optimistic-rollback":
            env.toggle_checkbox(True)
        before_seq = int(env.snapshot().get("action_seq", 0))
        res = env.click_business_button()
        after_seq = int(env.snapshot().get("action_seq", 0))
        check("真实鼠标点击触发业务动作", res["triggered"] and after_seq > before_seq,
              f"action_seq {before_seq} -> {after_seq}")
        check("snapshot().action_seq 随真实业务动作变化",
              after_seq == before_seq + 1, f"{before_seq} -> {after_seq}")
        # 反向证据：不点击则不变化
        seq_a = int(env.snapshot().get("action_seq", 0))
        env.press_key("ArrowRight")
        seq_b = int(env.snapshot().get("action_seq", 0))
        check("无业务点击时 action_seq 不变（无旁路触发）", seq_a == seq_b, f"{seq_a} -> {seq_b}")

        # ── timer/run 隔离：前一 run 的定时器不污染下一 run ──
        if task_id == "optimistic-rollback":
            # 第一次点击后有 1800ms rollback timer；立刻 reset 并观察
            env.reset()
            time.sleep(2.2)  # 超过原 timer 时长
            snap = env.snapshot()
            check("reset 清除上一 run 的 rollback timer",
                  snap.get("backend_status") == "idle" and snap.get("persisted") is False
                  and snap.get("attempt") == 0,
                  f"backend_status={snap.get('backend_status')} attempt={snap.get('attempt')}")
            check("reset 后 hidden judge 仍为未成功",
                  env.hidden_judge().get("task_success") is False)
        if task_id == "false-causality":
            # background ticker 每 350ms；reset 后 background_seq 必须从 0 重新计数
            snap0 = env.reset()
            check("reset 重置 background_seq", int(snap0.get("background_seq", -1)) == 0,
                  f"background_seq={snap0.get('background_seq')}")
            time.sleep(0.9)
            seq_a = int(env.snapshot().get("background_seq", 0))
            check("background ticker 在 reset 后重新运行", seq_a >= 2, f"background_seq={seq_a}")
            snap1 = env.reset()
            check("再次 reset 使 background_seq 归零（不继承）",
                  int(snap1.get("background_seq", -1)) == 0, f"background_seq={snap1.get('background_seq')}")

        # ── 4.4 hiddenJudge 不可见性（DOM/窗口枚举）──────────
        leaks = env.probe_leaks()
        check("可见 DOM 文本不含 authoritative 字段", leaks["visible_leaks"] == [],
              f"visible_leaks={leaks['visible_leaks']}")
        globals_ = env.enumerate_model_visible_globals()
        check("window 枚举仅暴露 verificationBench（Harness 接口，非模型工具）",
              globals_ == ["verificationBench"], f"globals={globals_}")


def main():
    pd = protocol.load_protocol()
    for task_id in pd["task_by_id"]:
        run_task(task_id, pd)
    passed = sum(1 for c in CHECKS if c["ok"])
    print(f"\n{passed}/{len(CHECKS)} 通过")
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
