# -*- coding: utf-8 -*-
"""Verification Value Benchmark v1 — H1 runner（真实模型）。

用法：
    python h1_runner.py preflight            # 只检查调用条件，不产生任何 H1 数据
    python h1_runner.py run --tasks all --arms A B C --runs-per-cell 2 --stage smoke
    python h1_runner.py run --tasks all --arms A B C --runs-per-cell 5 --stage formal

与 H0 runner 的关系：
  - 冻结语义（任务文本、成功条件、verifier 语义、gate policy、P0 口径、
    premature / duplicate / recovery 判定）**全部复用同一套 harness 模块**，
    不复制第二套常量；
  - 唯一差别是决策来源：H0 用 scripted_agent，H1 用真实模型（llm_agent）。

Gate 时机（正式裁决）：
  Verification Gate 在第一次业务动作之前不生效；第一次业务 intent 必须允许执行。
  Gate 从第一次业务动作产生 checkpoint 后开始生效，此后在每次 checkpoint 后、
  后续不可逆 intent 前，以及接受模型 completion claim 前进行检查。
  本 benchmark 测的是 post-action verification enforcement，
  不是 precondition/authorization gate。

安全默认：`run` 必须显式加 `--confirm-live-model` 才会真正调用模型并写产物，
避免误触产生正式 H1 数据。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import arm_policy, classifier, gate_policy, protocol, schema_validator  # noqa: E402
from harness.fixture_env import FixtureEnvironment, InfrastructureError  # noqa: E402
from harness.llm_agent import AstraAgent, ModelConfig, ModelUnavailableError  # noqa: E402

HARNESS_VERSION = "verification-value-benchmark-v1-h1/0.1-preflight"
H0_ONLY = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def base_row(run_id, task_id, arm, model) -> dict:
    return {
        "run_id": run_id,
        "benchmark_id": "verification-value-benchmark-v1",
        "task_id": task_id, "arm": arm, "model": model,
        "harness_version": HARNESS_VERSION, "started_at": utc_now(), "elapsed_ms": 0,
        "claimed_success": False, "task_success": False, "false_positive": False,
        "premature_completion": False, "unsafe_duplicate_effect": False,
        "recovery_attempted": False, "recovery_success": None,
        "computer_use_actions": 0, "screenshots": 0, "retries": 0,
        "verifier_packets": 0, "gate_blocks": 0, "pending_wait_ms": 0,
        "tokens_in": None, "tokens_out": None, "final_model_text": None,
        "first_success_claim_ms": None, "invalid_run": False, "invalid_reason": None,
        "final_judge": {"terminal": False, "task_success": False, "authoritative_state": {}},
        "raw_trace_path": "", "h0_only": H0_ONLY, "agent_kind": "llm",
    }


def _gate(protocol_data, arm, status, action, task, intent_committed=False):
    if not arm_policy.gate_enforced(protocol_data, arm):
        return None
    return gate_policy.decide(protocol_data, status, action, task, intent_committed)


def mark_invalid(row, raw, reason):
    row["invalid_run"] = True
    row["invalid_reason"] = str(reason)[:400]
    raw["invalid_reason"] = row["invalid_reason"]


def run_one(protocol_data, task_id, arm, run_id, config: ModelConfig, out_dir: Path,
            headless=True, save_screenshots=False) -> tuple:
    task = protocol.task_spec(protocol_data, task_id)
    row = base_row(run_id, task_id, arm, config.model)
    raw = {
        "run_id": run_id, "benchmark_id": row["benchmark_id"], "task_id": task_id, "arm": arm,
        "h0_only": H0_ONLY, "harness_version": HARNESS_VERSION, "model": config.model,
        "agent_kind": "llm", "model_config": config.fingerprint(), "started_at": row["started_at"],
        "task_instruction": task["instruction_zh"],
        "arm_capabilities": arm_policy.capability_fingerprint(protocol_data, arm),
        "frozen_hashes": protocol_data["frozen_hashes"],
        "reset": None, "trace": [], "verifier_packets": [], "gate_decisions": [],
        "first_success_claim": None, "final_model_text": None, "ground_truth": None,
        "hidden_judge_leaked": False, "status": None, "invalid_reason": None,
        "model_input": [], "gate_timing_ruling": "post-action enforcement (first intent allowed)",
    }
    started = time.monotonic()
    stability_ms = protocol.stability_window_ms(protocol_data)
    env = agent = None
    try:
        env = FixtureEnvironment(protocol_data, task_id, headless=headless).open()
        raw["reset"] = env.reset()
        raw["reset_verifier"] = env.verifier()
        raw["reset_judge"] = env.hidden_judge()

        agent = AstraAgent(task, config)
        agent.start(task["instruction_zh"])
        raw["model_input"].append({"role": "user", "content": task["instruction_zh"]})
        agent.add_screenshot(env.screenshot_png())
        row["screenshots"] += 1

        state = {}
        intent_committed = False
        intent_attempted = False
        pending_started = None
        pending_wait_ms = 0
        transitions = [{"status": env.verifier().get("status"), "at_ms": 0}]
        verifier_packets = gate_blocks = cu_actions = 0
        first_claim = None
        outcome = None
        max_steps = int(protocol_data["budgets"]["max_agent_steps"])
        max_wall_s = int(protocol_data["budgets"]["max_wall_time_seconds"])

        for step in range(1, max_steps + 1):
            if time.monotonic() - started > max_wall_s:
                raw["trace"].append({"step": step, "kind": "budget_timeout"})
                break

            decision = agent.decide()
            act = str(decision.get("action") or "")
            raw["trace"].append({"step": step, "kind": "model_decision",
                                 "action": act, "text": str(decision.get("text") or "")[:500]})

            v_now = env.verifier()
            if v_now.get("status") == "pending" and pending_started is None:
                pending_started = time.monotonic()
            elif v_now.get("status") != "pending" and pending_started is not None:
                pending_wait_ms += int((time.monotonic() - pending_started) * 1000)
                pending_started = None
            if not transitions or transitions[-1]["status"] != v_now.get("status"):
                transitions.append({"status": v_now.get("status"),
                                    "at_ms": int((time.monotonic() - started) * 1000)})

            # ── 不可逆业务 intent：坐标点击命中业务目标才算 ────────
            is_business_intent = False
            if act == "click_at":
                is_business_intent = env.hit_test_business_target(decision.get("x"), decision.get("y"))

            if is_business_intent and intent_attempted:
                # Gate 生效区间：首次业务动作之后
                if (str(v_now.get("status")) == "committed"
                        and bool(task.get("duplicate_risk")) and intent_committed):
                    g_action = gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT
                elif (str(v_now.get("status")) in ("failed", "unchanged")
                        and bool(task.get("recoverable"))):
                    g_action = gate_policy.ACTION_RETRY
                else:
                    g_action = gate_policy.ACTION_NEW_IRREVERSIBLE_INTENT
                g = _gate(protocol_data, arm, str(v_now.get("status")), g_action, task,
                          intent_committed)
                if g is not None:
                    raw["gate_decisions"].append({"step": step, "phase": "new_intent", **g})
                    raw["trace"].append({"step": step, "kind": "gate_decision", **g})
                    if not g["allowed"]:
                        gate_blocks += 1
                        raw["trace"].append({"step": step, "kind": "gate_block",
                                             "action": g["action"], "reason": g["reason"]})
                        state["_blocked_intent"] = int(state.get("_blocked_intent", 0)) + 1
                        agent.add_action_result("mouse_click", {
                            "blocked": True, "reason": "harness_policy_blocked",
                            "x": decision.get("x"), "y": decision.get("y")})
                        if state["_blocked_intent"] > 3:
                            outcome = "gate_blocked_intent"
                            break
                        continue

            # ── 执行动作 ─────────────────────────────────────────
            try:
                if act == "click_at":
                    res = env.click_at(decision.get("x"), decision.get("y"))
                    cu_actions += 1
                    raw["trace"].append({"step": step, "kind": "computer_use",
                                         "tool": "mouse_click", **res,
                                         "business_intent": is_business_intent})
                elif act == "mouse_move":
                    res = env.mouse_move(decision.get("x"), decision.get("y"))
                    cu_actions += 1
                elif act == "mouse_drag":
                    res = env.mouse_drag(decision.get("from_x"), decision.get("from_y"),
                                         decision.get("to_x"), decision.get("to_y"))
                    cu_actions += 1
                elif act == "key_press":
                    res = env.press_key(str(decision.get("key") or ""))
                    cu_actions += 1
                elif act == "screenshot":
                    res = {"ok": True}
                    row["screenshots"] += 1
                elif act in ("claim_success", "claim_failure", "done"):
                    res = {"ok": True}
                elif act in ("no_tool_call", "malformed_call", "unknown_tool"):
                    res = {"ok": False, "note": act}
                    raw["trace"].append({"step": step, "kind": "model_protocol_violation",
                                         "action": act, "detail": str(decision)[:300]})
                else:
                    res = {"ok": False, "note": f"unknown action {act!r}"}
            except InfrastructureError:
                raise

            # ── checkpoint：业务动作后 ────────────────────────────
            if is_business_intent and res.get("triggered"):
                intent_attempted = True
                last_seq = int(res.get("action_seq_after", 0))
                v = env.verifier()
                raw["trace"].append({"step": step, "kind": "checkpoint",
                                     "action_seq": last_seq,
                                     "ui_status": env.snapshot().get("ui_status"),
                                     "verifier_status": v.get("status")})
                if arm_policy.verifier_visible(protocol_data, arm):
                    packet = env.verifier_packet()
                    verifier_packets += 1
                    raw["verifier_packets"].append({"step": step, "packet": packet})
                    agent.add_evidence(packet)
                    raw["model_input"].append({"role": "environment_evidence", "step": step,
                                               "source": "verificationBench.verifier()",
                                               "packet": packet, "advice_added": False})
                    raw["trace"].append({"step": step, "kind": "verifier_injected"})
                if str(v.get("status")) == "committed":
                    intent_committed = True

            # ── 把动作结果与最新截图回给模型 ──────────────────────
            # 注意：只回显动作本身，绝不回传 action_seq/triggered 等 Harness-only 字段。
            if act not in ("claim_success", "claim_failure", "done"):
                agent.add_action_result(
                    {"click_at": "mouse_click", "mouse_move": "mouse_move",
                     "mouse_drag": "mouse_drag", "key_press": "key_press",
                     "screenshot": "screenshot"}.get(act, act),
                    agent.tool_result_payload(res))
                if act != "screenshot":
                    agent.add_screenshot(env.screenshot_png())
                    row["screenshots"] += 1

            # ── 声称完成 ─────────────────────────────────────────
            if act == "claim_success":
                text = str(decision.get("text") or "")
                if arm_policy.verifier_visible(protocol_data, arm):
                    v_final = env.verifier()
                    if arm_policy.gate_enforced(protocol_data, arm):
                        g = gate_policy.decide(protocol_data, str(v_final.get("status")),
                                               gate_policy.ACTION_FINAL_SUCCESS, task,
                                               intent_committed)
                        raw["gate_decisions"].append({"step": step, "phase": "final_success", **g})
                        raw["trace"].append({"step": step, "kind": "gate_decision", **g})
                        if not g["allowed"]:
                            gate_blocks += 1
                            raw["trace"].append({"step": step, "kind": "gate_block",
                                                 "action": g["action"], "reason": g["reason"]})
                            state["_blocked_claim"] = int(state.get("_blocked_claim", 0)) + 1
                            agent.add_action_result("report_success", {
                                "blocked": True, "reason": "harness_policy_blocked",
                                "detail": "completion claim rejected; task not yet verified as done"})
                            if state["_blocked_claim"] > 3:
                                outcome = "gate_blocked_claim"
                                break
                            continue
                if first_claim is None:
                    first_claim = {
                        "claimed_at_ms": int((time.monotonic() - started) * 1000),
                        "verifier_status": env.verifier().get("status"),
                        "authoritative_terminal": bool(env.hidden_judge().get("terminal")),
                    }
                row["claimed_success"] = True
                row["first_success_claim_ms"] = first_claim["claimed_at_ms"]
                row["final_model_text"] = text[:2000]
                raw["first_success_claim"] = first_claim
                raw["final_model_text"] = text[:2000]
                raw["trace"].append({"step": step, "kind": "success_claim",
                                     "text": text[:500],
                                     "verifier_status": first_claim["verifier_status"]})
                outcome = "claimed"
                break

            if act == "claim_failure":
                raw["final_model_text"] = str(decision.get("text") or "")[:2000]
                row["final_model_text"] = raw["final_model_text"]
                raw["trace"].append({"step": step, "kind": "failure_claim",
                                     "text": raw["final_model_text"][:500]})
                outcome = "reported_failure"
                break

            if act == "done":
                outcome = "ended_without_claim"
                break

        if pending_started is not None:
            pending_wait_ms += int((time.monotonic() - pending_started) * 1000)

        time.sleep(stability_ms / 1000)
        judge = env.hidden_judge()
        raw["ground_truth"] = judge
        raw["final_verifier"] = env.verifier()
        raw["transitions"] = transitions

        if first_claim is not None:
            for t in transitions:
                if t["at_ms"] > first_claim["claimed_at_ms"]:
                    first_claim["later_transition"] = t
                    break

        row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        row["computer_use_actions"] = cu_actions
        row["retries"] = int(state.get("retried", False))
        row["recovery_attempted"] = bool(state.get("recovery_attempted", False))
        row["verifier_packets"] = verifier_packets
        row["gate_blocks"] = gate_blocks
        row["pending_wait_ms"] = pending_wait_ms
        row["tokens_in"] = agent.tokens_in or None
        row["tokens_out"] = agent.tokens_out or None
        row["model_turns"] = agent.turns
        row["final_judge"] = {
            "terminal": bool(judge.get("terminal")),
            "task_success": bool(judge.get("task_success")),
            "authoritative_state": judge.get("authoritative_state") or {},
            "duplicate_effect_count": judge.get("duplicate_effect_count"),
            "last_verifier_status": judge.get("last_verifier_status"),
        }
        metrics = classifier.classify(
            protocol_data, task, claimed_success=row["claimed_success"], hidden_judge=judge,
            first_claim=first_claim, recovery_attempted=row["recovery_attempted"],
            recoverable_failure_observed=("failed" in [t["status"] for t in transitions]))
        row["task_success"] = metrics["task_success"]
        row["false_positive"] = metrics["false_positive"]
        row["premature_completion"] = metrics["premature_completion"]
        row["unsafe_duplicate_effect"] = metrics["unsafe_duplicate_effect"]
        row["recovery_success"] = metrics["recovery_success"]
        raw["classifier"] = metrics
        raw["status"] = outcome or "ended_without_claim"
        raw["model_turns"] = agent.turns
    except (InfrastructureError, ModelUnavailableError) as exc:
        row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        mark_invalid(row, raw, f"{type(exc).__name__}：{exc}")
        try:
            if env is not None and env._page is not None:
                judge = env.hidden_judge()
                raw["ground_truth"] = judge
        except Exception as inner:
            raw["ground_truth_error"] = f"{type(inner).__name__}: {inner}"[:300]
    except Exception as exc:
        row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        mark_invalid(row, raw, f"执行器异常：{type(exc).__name__}: {exc}"[:400])
    finally:
        if env is not None:
            env.close()
    raw["invalid_reason"] = row["invalid_reason"]
    return row, raw


def summarize(rows):
    grouped = {}
    for row in rows:
        if row["invalid_run"]:
            continue
        grouped.setdefault((row["task_id"], row["arm"]), []).append(row)
    out = {"effective_runs": sum(len(v) for v in grouped.values()),
           "invalid_runs": sum(1 for r in rows if r["invalid_run"]),
           "total_rows": len(rows), "groups": {}}
    for (task_id, arm), items in sorted(grouped.items()):
        n = len(items)

        def rate(key):
            return round(sum(bool(x.get(key)) for x in items) / n, 4) if n else None

        recov = [x for x in items if x.get("recovery_attempted")]
        out["groups"][f"{task_id}:{arm}"] = {
            "task_id": task_id, "arm": arm, "runs": n,
            "task_success_rate": rate("task_success"),
            "false_positive_rate": rate("false_positive"),
            "premature_completion_rate": rate("premature_completion"),
            "unsafe_duplicate_effect_rate": rate("unsafe_duplicate_effect"),
            "recovery_success_rate": (round(sum(x.get("recovery_success") is True for x in recov)
                                            / len(recov), 4) if recov else None),
            "mean_elapsed_ms": round(statistics.mean(x["elapsed_ms"] for x in items), 1),
            "mean_computer_use_actions": round(statistics.mean(x["computer_use_actions"] for x in items), 2),
            "mean_verifier_packets": round(statistics.mean(x["verifier_packets"] for x in items), 2),
            "mean_gate_blocks": round(statistics.mean(x["gate_blocks"] for x in items), 2),
        }
    return out


# ── preflight：只检查条件，不产生 H1 数据 ────────────────────
def cmd_preflight(args) -> int:
    print("=== H1 preflight（只读检查，不产生任何 H1 数据）===\n")
    ok = True

    print("[1] 冻结协议自检")
    try:
        pd = protocol.load_protocol()
        print(f"    OK   tasks={len(pd['task_by_id'])} arms={sorted(pd['arms']['arms'])} "
              f"stability_ms={protocol.stability_window_ms(pd)}")
    except SystemExit as exc:
        print(f"    FAIL {exc}")
        return 1

    print("\n[2] 模型凭据")
    key = os.environ.get("OPENROUTER_API_KEY")
    print(f"    OPENROUTER_API_KEY: {'SET' if key else 'MISSING'}")
    if not key:
        ok = False

    print("\n[3] SDK 与端点")
    try:
        import openai
        print(f"    openai SDK: {getattr(openai, '__version__', '?')}")
    except ImportError:
        print("    openai SDK: MISSING")
        ok = False

    print("\n[4] 模型可达性与工具面")
    if key:
        try:
            cfg = ModelConfig(model=args.model, reasoning_effort=args.reasoning_effort)
            agent = AstraAgent(protocol.task_spec(pd, "silent-422"), cfg)
            agent.start("Reply with the single word: ok")
            d = agent.decide()
            print(f"    OK   model={args.model} turns={agent.turns} "
                  f"tokens_in={agent.tokens_in} tokens_out={agent.tokens_out} action={d.get('action')}")
        except ModelUnavailableError as exc:
            print(f"    FAIL {exc}")
            ok = False
    else:
        print("    SKIP 无凭据")

    print("\n[5] 浏览器后端")
    try:
        env = FixtureEnvironment(pd, "silent-422", headless=True).open()
        env.reset()
        png = env.screenshot_png()
        env.close()
        print(f"    OK   screenshot={len(png)} bytes")
    except Exception as exc:
        print(f"    FAIL {type(exc).__name__}: {exc}")
        ok = False

    print("\n[6] Computer Use 通道（官方 computer_use_preview vs harness 中介）")
    print("    见 H1_PREFLIGHT.md：gpt-6-astra 拒绝原生 computer_use_preview，")
    print("    必须由 harness 用 function tools 中介执行真实鼠标事件。")

    print(f"\n=== preflight {'PASS' if ok else 'FAIL'} ===")
    print("未产生任何 H1 数据；正式运行需显式 --confirm-live-model。")
    return 0 if ok else 1


def cmd_run(args) -> int:
    if not args.confirm_live_model:
        print("拒绝执行：正式 H1 运行需显式加 --confirm-live-model。")
        print("（先运行 `python h1_runner.py preflight` 检查调用条件）")
        return 2
    protocol_data = protocol.load_protocol()
    tasks = list(protocol_data["task_by_id"]) if args.tasks == ["all"] else args.tasks
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else (
        protocol.DEFAULT_ARTIFACTS / f"H1-{args.stage}-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    jsonl = out_dir / "runs.jsonl"
    config = ModelConfig(model=args.model, reasoning_effort=args.reasoning_effort,
                         temperature=args.temperature, seed=args.seed)
    manifest = {
        "stage": f"H1-{args.stage}", "harness_version": HARNESS_VERSION, "created_at": utc_now(),
        "h0_only": False, "agent_kind": "llm", "model_config": config.fingerprint(),
        "tasks": tasks, "arms": args.arms, "runs_per_cell": args.runs_per_cell,
        "frozen_hashes": protocol_data["frozen_hashes"],
        "isolation_matrix": arm_policy.isolation_matrix(protocol_data),
        "gate_timing_ruling": "post-action enforcement (first intent allowed)",
        "warning": "H1 数据只用于本 benchmark 预注册指标；不得与其他实验混用结论。",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for task_id in tasks:
        for arm in args.arms:
            for i in range(1, args.runs_per_cell + 1):
                run_id = f"{task_id}_{arm}_{i:03d}"
                print(f"  → {run_id}", flush=True)
                row, raw = run_one(protocol_data, task_id, arm, run_id, config, out_dir,
                                   headless=not args.headful)
                (out_dir / "raw" / f"{run_id}.json").write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                row["raw_trace_path"] = str(Path("raw") / f"{run_id}.json")
                schema_validator.validate_row(row, protocol_data["schema"])
                with jsonl.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                print(f"     success={row['task_success']} claimed={row['claimed_success']} "
                      f"fp={row['false_positive']} premature={row['premature_completion']} "
                      f"dup={row['unsafe_duplicate_effect']} gate_blocks={row['gate_blocks']} "
                      f"invalid={row['invalid_run']}", flush=True)

    summary = summarize(rows)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="VVv1 H1 runner（真实模型）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("preflight", help="只检查调用条件，不产生 H1 数据")
    p.add_argument("--model", default="openai/gpt-6-astra")
    p.add_argument("--reasoning-effort", default="medium")
    p.set_defaults(func=cmd_preflight)

    p2 = sub.add_parser("run", help="执行 H1（需 --confirm-live-model）")
    p2.add_argument("--tasks", nargs="+", default=["all"])
    p2.add_argument("--arms", nargs="+", default=["A", "B", "C"], choices=["A", "B", "C"])
    p2.add_argument("--runs-per-cell", type=int, default=2)
    p2.add_argument("--stage", default="smoke", choices=["smoke", "formal"])
    p2.add_argument("--model", default="openai/gpt-6-astra")
    p2.add_argument("--reasoning-effort", default="medium")
    p2.add_argument("--temperature", type=float, default=1.0)
    p2.add_argument("--seed", type=int, default=None)
    p2.add_argument("--out", default=None)
    p2.add_argument("--headful", action="store_true")
    p2.add_argument("--confirm-live-model", action="store_true")
    p2.set_defaults(func=cmd_run)

    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
