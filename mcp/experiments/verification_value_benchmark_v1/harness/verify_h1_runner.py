# -*- coding: utf-8 -*-
"""H1 runner 仪器自检。

默认**离线**模式：不调用模型、不产生任何 H1 数据，只验证 H1 新增部件的正确性：
  - 坐标命中测试（business intent 判定）
  - 真实坐标点击是否触发 action_seq
  - 工具调用 → 决策协议 的映射
  - 回填给模型的工具结果不含 Harness-only 字段

`--live` 会做一次端到端真实调用（写临时目录并删除）。
**该模式属于产生真实运行数据，须由用户明确批准后执行。**
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from harness import protocol  # noqa: E402
from harness.fixture_env import FixtureEnvironment  # noqa: E402
from harness.llm_agent import TOOLS, AstraAgent, ModelConfig  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def offline_checks():
    pd = protocol.load_protocol()
    print("=== 1. 坐标命中测试与真实坐标点击（不调用模型）===")
    for task_id, sel in (("silent-422", "#submit"), ("optimistic-rollback", "#save"),
                         ("duplicate-danger", "#send"), ("false-causality", "#refresh")):
        env = FixtureEnvironment(pd, task_id, headless=True).open()
        try:
            env.reset()
            box = env._page.evaluate(
                "(s) => { const r = document.querySelector(s).getBoundingClientRect();"
                " return {x: r.x + r.width/2, y: r.y + r.height/2}; }", sel)
            hit = env.hit_test_business_target(box["x"], box["y"])
            miss = env.hit_test_business_target(2.0, 2.0)
            res = env.click_at(box["x"], box["y"])
            check(f"{task_id}: 业务按钮坐标命中", hit is True)
            check(f"{task_id}: 空白处不误判为业务 intent", miss is False)
            check(f"{task_id}: 坐标点击触发 action_seq", res["triggered"] is True,
                  f"{res['action_seq_before']}→{res['action_seq_after']}")
        finally:
            env.close()

    print("\n=== 2. 工具调用 → 决策协议 映射（纯函数，无网络）===")
    cases = [
        ({"name": "mouse_click", "arguments": '{"x":10,"y":20}', "call_id": "c1"},
         {"action": "click_at", "x": 10, "y": 20}),
        ({"name": "key_press", "arguments": '{"key":"Enter"}', "call_id": "c2"},
         {"action": "key_press", "key": "Enter"}),
        ({"name": "screenshot", "arguments": "{}", "call_id": "c3"}, {"action": "screenshot"}),
        ({"name": "report_success", "arguments": '{"summary":"done"}', "call_id": "c4"},
         {"action": "claim_success"}),
        ({"name": "report_failure", "arguments": '{"summary":"failed"}', "call_id": "c5"},
         {"action": "claim_failure"}),
        ({"name": "mouse_drag", "arguments": '{"from_x":1,"from_y":2,"to_x":3,"to_y":4}',
          "call_id": "c6"}, {"action": "mouse_drag"}),
    ]
    for call, want in cases:
        got = AstraAgent._to_decision([call], "")
        ok = all(got.get(k) == v for k, v in want.items())
        check(f"{call['name']} → {want['action']}", ok, str(got)[:120])
    check("无工具调用 → no_tool_call",
          AstraAgent._to_decision([], "hi").get("action") == "no_tool_call")
    check("非法 JSON → malformed_call",
          AstraAgent._to_decision([{"name": "mouse_click", "arguments": "{oops",
                                    "call_id": "x"}], "").get("action") == "malformed_call")

    print("\n=== 3. 回填模型的工具结果不得含 Harness-only 字段 ===")
    cfg = ModelConfig()
    dummy = {"x": 5, "y": 6, "ok": True, "triggered": True, "action_seq_after": 3,
             "action_seq_before": 2}
    payload = AstraAgent.tool_result_payload(None, dummy)  # unbound: 纯函数
    check("保留动作本身", payload.get("x") == 5 and payload.get("y") == 6)
    check("剔除 triggered/action_seq",
          "triggered" not in payload and "action_seq_after" not in payload,
          str(payload))

    print("\n=== 4. 工具面一致性（A/B/C 逐字相同）===")
    names = [t["name"] for t in TOOLS]
    check("工具面固定", names == ["mouse_click", "mouse_move", "mouse_drag",
                                  "key_press", "screenshot", "report_success",
                                  "report_failure"], str(names))
    check("无任意 JS/DOM 检查工具",
          not any(k in " ".join(names).lower() for k in ("eval", "js", "dom", "inspect")))
    fp = cfg.fingerprint()
    check("模型配置指纹完整",
          all(k in fp for k in ("model", "reasoning_effort", "temperature", "tools")))


def live_check():
    import os
    import subprocess
    print("=== 端到端真实调用（--live）===")
    tmp = Path(tempfile.mkdtemp(prefix="vvb-h1-live-"))
    try:
        proc = subprocess.run(
            [sys.executable, str(HERE.parent / "h1_runner.py"), "run",
             "--tasks", "silent-422", "--arms", "A", "--runs-per-cell", "1",
             "--stage", "smoke", "--out", str(tmp), "--confirm-live-model"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, cwd=str(HERE.parent), timeout=900)
        jsonl = tmp / "runs.jsonl"
        check("产出 runs.jsonl", jsonl.exists(), (proc.stderr or "")[:200])
        if jsonl.exists():
            row = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
            check("invalid_run=false", not row["invalid_run"], str(row["invalid_reason"]))
            check("模型确实被调用", bool(row.get("tokens_in")),
                  f"in={row.get('tokens_in')} out={row.get('tokens_out')}")
            raw = json.loads((tmp / row["raw_trace_path"]).read_text(encoding="utf-8"))
            blob = json.dumps(raw.get("model_input") or [], ensure_ascii=False)
            leaked = [n for n in ("hiddenJudge", "authoritative_state", "action_seq")
                      if n in blob]
            check("模型输入无 Harness-only 泄漏", not leaked, str(leaked))
            print(f"  model_turns={row.get('model_turns')} status={raw.get('status')}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        print("[cleanup] 临时目录已删除")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="执行一次端到端真实调用（需用户批准）")
    args = ap.parse_args()
    if args.live:
        live_check()
    else:
        offline_checks()
    passed = sum(CHECKS)
    print(f"\n{passed}/{len(CHECKS)} 通过"
          + ("" if args.live else "（离线模式：未调用模型，未产生 H1 数据）"))
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
