# -*- coding: utf-8 -*-
"""Verification Value Benchmark v1 — H0 runner。

用法：
    python runner.py run --tasks all --arms A B C --runs-per-cell 1
    python runner.py audit <run_dir>
    python runner.py smoke

职责边界（H0_DEEPSEEK_PROMPT.md）：
  - 只执行与记录，不重新设计实验；
  - 冻结协议（任务文本、成功条件、verifier 语义、gate policy、P0 口径）
    一律从 harness.protocol 读取；
  - hiddenJudge 只在 run 结束后由 Harness 读取，永不进入模型上下文；
  - 基础设施故障记 invalid_run=true 并保留非空原因，不静默重跑。

Gate 语义：策略只表达「想做什么」，是否放行由 runner 依 arms.json.gate_policy
决定。C 臂的 gate 在动作真正发生前阻断，属 Harness enforcement。
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

from harness import arm_policy, classifier, gate_policy, protocol, schema_validator, scripted_agent  # noqa: E402
from harness.fixture_env import FixtureEnvironment, InfrastructureError  # noqa: E402

HARNESS_VERSION = "verification-value-benchmark-v1-h0/1.0"
H0_ONLY = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def base_row(run_id: str, task_id: str, arm: str, model: str) -> dict:
    return {
        "run_id": run_id,
        "benchmark_id": "verification-value-benchmark-v1",
        "task_id": task_id,
        "arm": arm,
        "model": model,
        "harness_version": HARNESS_VERSION,
        "started_at": utc_now(),
        "elapsed_ms": 0,
        "claimed_success": False,
        "task_success": False,
        "false_positive": False,
        "premature_completion": False,
        "unsafe_duplicate_effect": False,
        "recovery_attempted": False,
        "recovery_success": None,
        "computer_use_actions": 0,
        "screenshots": 0,
        "retries": 0,
        "verifier_packets": 0,
        "gate_blocks": 0,
        "pending_wait_ms": 0,
        "tokens_in": None,
        "tokens_out": None,
        "final_model_text": None,
        "first_success_claim_ms": None,
        "invalid_run": False,
        "invalid_reason": None,
        "final_judge": {"terminal": False, "task_success": False, "authoritative_state": {}},
        "raw_trace_path": "",
        "h0_only": H0_ONLY,
        "agent_kind": "scripted",
    }


def _gate(protocol_data, arm, status, action, task, intent_committed=False):
    """只在该臂启用 gate 时执行判定；否则返回 None（不干预）。"""
    if not arm_policy.gate_enforced(protocol_data, arm):
        return None
    return gate_policy.decide(protocol_data, status, action, task, intent_committed)


def run_one(protocol_data: dict, task_id: str, arm: str, run_id: str, strategy_name: str,
            out_dir: Path, model: str = "scripted-h0-agent", headless: bool = True) -> tuple:
    task = protocol.task_spec(protocol_data, task_id)
    row = base_row(run_id, task_id, arm, model)
    raw = {
        "run_id": run_id, "benchmark_id": row["benchmark_id"], "task_id": task_id, "arm": arm,
        "h0_only": H0_ONLY, "harness_version": HARNESS_VERSION, "model": model,
        "agent_kind": "scripted", "strategy": strategy_name, "started_at": row["started_at"],
        "task_instruction": task["instruction_zh"],
        "arm_capabilities": arm_policy.capability_fingerprint(protocol_data, arm),
        "frozen_hashes": protocol_data["frozen_hashes"],
        "reset": None, "trace": [], "verifier_packets": [], "gate_decisions": [],
        "first_success_claim": None, "final_model_text": None, "ground_truth": None,
        "hidden_judge_leaked": False, "status": None, "invalid_reason": None,
        # 模型可见上下文（H0：脚本化 agent 无 LLM，但记录"将会注入什么"，
        # 用于机器证明 hiddenJudge 从未进入模型输入）
        "model_input": [{"role": "user", "content": task["instruction_zh"]}],
    }
    started = time.monotonic()
    stability_ms = protocol.stability_window_ms(protocol_data)
    env = None
    try:
        env = FixtureEnvironment(protocol_data, task_id, headless=headless).open()
        raw["reset"] = env.reset()
        raw["reset_verifier"] = env.verifier()
        raw["reset_judge"] = env.hidden_judge()

        strategy = scripted_agent.get_strategy(task_id, strategy_name)
        state = {}
        last_seq = 0
        intent_committed = False
        intent_attempted = False
        pending_started = None
        pending_wait_ms = 0
        transitions = [{"status": env.verifier().get("status"), "at_ms": 0}]
        verifier_packets = 0
        gate_blocks = 0
        cu_actions = 0
        first_claim = None
        outcome = None
        max_steps = int(protocol_data["budgets"]["max_agent_steps"])
        max_wall_s = int(protocol_data["budgets"]["max_wall_time_seconds"])

        for step in range(1, max_steps + 1):
            if time.monotonic() - started > max_wall_s:
                raw["trace"].append({"step": step, "kind": "budget_timeout"})
                break

            decision = strategy(env, state) or {"action": "done"}
            act = str(decision.get("action") or "")

            # ── pending 计时 ──────────────────────────────────
            v_now = env.verifier()
            if v_now.get("status") == "pending" and pending_started is None:
                pending_started = time.monotonic()
            elif v_now.get("status") != "pending" and pending_started is not None:
                pending_wait_ms += int((time.monotonic() - pending_started) * 1000)
                pending_started = None
            if not transitions or transitions[-1]["status"] != v_now.get("status"):
                transitions.append({"status": v_now.get("status"),
                                    "at_ms": int((time.monotonic() - started) * 1000)})

            # ── 业务动作（不可逆 intent）────────────────────────
            # Gate 触发点（HARNESS_HANDOFF "Checkpoint"）：gate 在「业务动作发生后
            # 的 checkpoint」与「最终完成声明前」被咨询，不在第一次动作前。
            # 因此只有已发生过 intent 之后，新的不可逆 intent 才受 gate 约束。
            if act in ("click_business", "toggle_checkbox"):
                if act == "click_business" and intent_attempted:
                    # 判断这是「重复同一 intent」还是「新的不可逆 intent」
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
                            if state["_blocked_intent"] > 3:
                                outcome = "gate_blocked_intent"
                                break
                            time.sleep(0.2)
                            continue

                try:
                    if act == "toggle_checkbox":
                        res = env.toggle_checkbox(bool(decision.get("checked", True)))
                        cu_actions += 1
                    else:
                        res = env.click_business_button()
                        cu_actions += 1
                except InfrastructureError:
                    raise
                raw["trace"].append({"step": step, "kind": "computer_use",
                                     "tool": "mouse_click", "action": act, "result": res})

                # ── checkpoint：业务动作后 ─────────────────────
                if res.get("triggered"):
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
                        raw["model_input"].append({
                            "role": "environment_evidence", "step": step,
                            "source": "verificationBench.verifier()", "packet": packet,
                            "advice_added": False,
                        })
                        raw["trace"].append({"step": step, "kind": "verifier_injected",
                                             "role": "environment_evidence", "packet": packet,
                                             "advice_added": False})
                    if v.get("status") == "committed":
                        intent_committed = True
                continue

            # ── 明确报告失败 / 结束 ────────────────────────────
            if act in ("claim_failure", "done"):
                row["final_model_text"] = str(decision.get("text") or "")[:2000]
                raw["final_model_text"] = row["final_model_text"]
                raw["trace"].append({"step": step, "kind": act,
                                     "text": row["final_model_text"][:500]})
                outcome = act
                break

            # ── 声称完成 ──────────────────────────────────────
            if act == "claim_success":
                text = str(decision.get("text") or "")
                if arm_policy.verifier_visible(protocol_data, arm):
                    v_final = env.verifier()
                    if arm_policy.gate_enforced(protocol_data, arm):
                        g = gate_policy.decide(protocol_data, str(v_final.get("status")),
                                               gate_policy.ACTION_FINAL_SUCCESS, task, intent_committed)
                        raw["gate_decisions"].append({"step": step, "phase": "final_success", **g})
                        raw["trace"].append({"step": step, "kind": "gate_decision", **g})
                        if not g["allowed"]:
                            gate_blocks += 1
                            raw["trace"].append({"step": step, "kind": "gate_block",
                                                 "action": g["action"], "reason": g["reason"]})
                            # 被阻断：不得完成；策略继续（等待/重观察）
                            state["_blocked_claim"] = int(state.get("_blocked_claim", 0)) + 1
                            if state["_blocked_claim"] > 3:
                                outcome = "gate_blocked_claim"
                                break
                            time.sleep(0.2)
                            continue
                # 允许声称完成
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
                raw["trace"].append({"step": step, "kind": "success_claim", "text": text[:500],
                                     "verifier_status": first_claim["verifier_status"]})
                outcome = "claimed"
                break

            # ── wait ──────────────────────────────────────────
            time.sleep(max(0, int(decision.get("ms", 150))) / 1000)

        if pending_started is not None:
            pending_wait_ms += int((time.monotonic() - pending_started) * 1000)

        # 等待稳定窗口后读取 hidden judge（模型已结束）
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
        row["final_judge"] = {
            "terminal": bool(judge.get("terminal")),
            "task_success": bool(judge.get("task_success")),
            "authoritative_state": judge.get("authoritative_state") or {},
            "duplicate_effect_count": judge.get("duplicate_effect_count"),
            "last_verifier_status": judge.get("last_verifier_status"),
        }

        metrics = classifier.classify(
            protocol_data, task,
            claimed_success=row["claimed_success"], hidden_judge=judge,
            first_claim=first_claim, recovery_attempted=row["recovery_attempted"],
            recoverable_failure_observed=("failed" in [t["status"] for t in transitions]),
        )
        row["task_success"] = metrics["task_success"]
        row["false_positive"] = metrics["false_positive"]
        row["premature_completion"] = metrics["premature_completion"]
        row["unsafe_duplicate_effect"] = metrics["unsafe_duplicate_effect"]
        row["recovery_success"] = metrics["recovery_success"]
        raw["classifier"] = metrics
        raw["status"] = outcome or "ended_without_claim"
        row["notes"] = f"scripted strategy={strategy_name}; status={raw['status']}"
    except InfrastructureError as exc:
        row["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        mark_invalid(row, raw, f"基础设施故障：{exc}")
        try:
            if env is not None and env._page is not None:
                judge = env.hidden_judge()
                raw["ground_truth"] = judge
                row["final_judge"] = {
                    "terminal": bool(judge.get("terminal")),
                    "task_success": bool(judge.get("task_success")),
                    "authoritative_state": judge.get("authoritative_state") or {},
                    "duplicate_effect_count": judge.get("duplicate_effect_count"),
                    "last_verifier_status": judge.get("last_verifier_status"),
                }
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


def mark_invalid(row: dict, raw: dict, reason: str):
    row["invalid_run"] = True
    row["invalid_reason"] = str(reason)[:400]
    raw["invalid_reason"] = row["invalid_reason"]


def summarize(rows: list) -> dict:
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
            "recovery_success_rate": (round(sum(x.get("recovery_success") is True for x in recov) / len(recov), 4)
                                      if recov else None),
            "mean_elapsed_ms": round(statistics.mean(x["elapsed_ms"] for x in items), 1),
            "mean_computer_use_actions": round(statistics.mean(x["computer_use_actions"] for x in items), 2),
            "mean_verifier_packets": round(statistics.mean(x["verifier_packets"] for x in items), 2),
            "mean_gate_blocks": round(statistics.mean(x["gate_blocks"] for x in items), 2),
        }
    return out


def cmd_run(args) -> int:
    protocol_data = protocol.load_protocol()
    tasks = list(protocol_data["task_by_id"]) if args.tasks == ["all"] else args.tasks
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) if args.out else (protocol.DEFAULT_ARTIFACTS / f"H0-dryrun-{stamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    jsonl = out_dir / "runs.jsonl"

    manifest = {
        "stage": "H0", "harness_version": HARNESS_VERSION, "created_at": utc_now(),
        "h0_only": H0_ONLY, "agent_kind": "scripted", "model": args.model,
        "tasks": tasks, "arms": args.arms, "runs_per_cell": args.runs_per_cell,
        "frozen_hashes": protocol_data["frozen_hashes"],
        "isolation_matrix": arm_policy.isolation_matrix(protocol_data),
        "warning": "H0 仅校准仪器；不得用于推断 Astra、A/B/C 或项目战略价值。",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for task_id in tasks:
        for arm in args.arms:
            for i in range(1, args.runs_per_cell + 1):
                if args.strategy:
                    strategy = args.strategy
                else:
                    names = scripted_agent.strategy_names(task_id)
                    strategy = scripted_agent.DEFAULT_STRATEGY.get(task_id, names[0])
                run_id = f"{task_id}_{arm}_{i:03d}"
                print(f"  → {run_id} strategy={strategy}", flush=True)
                row, raw = run_one(protocol_data, task_id, arm, run_id, strategy, out_dir,
                                   model=args.model, headless=not args.headful)
                raw_path = out_dir / "raw" / f"{run_id}.json"
                raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                row["raw_trace_path"] = str(Path("raw") / f"{run_id}.json")
                schema_validator.validate_row(row, protocol_data["schema"])
                with jsonl.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                rows.append(row)
                print(f"     success={row['task_success']} claimed={row['claimed_success']} "
                      f"fp={row['false_positive']} premature={row['premature_completion']} "
                      f"dup={row['unsafe_duplicate_effect']} recov={row['recovery_success']} "
                      f"gate_blocks={row['gate_blocks']} invalid={row['invalid_run']}", flush=True)

    summary = summarize(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    import subprocess
    analyzer = protocol.PHASE_DIR / "analyze_results.py"
    proc = subprocess.run([sys.executable, str(analyzer), str(jsonl)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if proc.returncode == 0:
        (out_dir / "analyze_output.json").write_text(proc.stdout, encoding="utf-8")
        print("[analyze_results.py] 已生成 analyze_output.json")
    else:
        print(f"[analyze_results.py] 失败（{proc.returncode}）：{(proc.stderr or proc.stdout)[:300]}")

    print("\n⚠ H0 数据仅用于仪器校准，不得作为 Astra / A/B/C 价值结论。")
    return 0


def cmd_audit(args) -> int:
    from harness.auditor import run_audit
    return run_audit(Path(args.run_dir))


def cmd_smoke(args) -> int:
    from harness.smoke import run_smoke
    return run_smoke(args)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="Verification Value Benchmark v1 — H0 harness")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="执行 dry-run")
    p.add_argument("--tasks", nargs="+", default=["all"])
    p.add_argument("--arms", nargs="+", default=["A", "B", "C"], choices=["A", "B", "C"])
    p.add_argument("--runs-per-cell", type=int, default=1)
    p.add_argument("--strategy", default=None, help="覆盖默认策略（仅 H0 调试用）")
    p.add_argument("--model", default="scripted-h0-agent")
    p.add_argument("--out", default=None)
    p.add_argument("--headful", action="store_true")
    p.set_defaults(func=cmd_run)

    p2 = sub.add_parser("audit", help="审计 run 目录")
    p2.add_argument("run_dir")
    p2.set_defaults(func=cmd_audit)

    p3 = sub.add_parser("smoke", help="最小能力自检")
    p3.add_argument("--out", default=None)
    p3.add_argument("--headful", action="store_true")
    p3.set_defaults(func=cmd_smoke)

    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
