# -*- coding: utf-8 -*-
"""第一阶段任务轨迹与候选图的浏览器闭环测试。"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER = str(Path(__file__).resolve().parent / "server.py")
FORM_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "form_names.html").as_uri()


async def call(session, name, args):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=60)
    return json.loads(result.content[0].text)


async def main():
    with tempfile.TemporaryDirectory() as directory:
        env = os.environ.copy()
        env["AGENT_TASK_RUNTIME_PERSIST"] = "1"
        env["AGENT_TASK_RUNTIME_STORE_DIR"] = directory
        params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                opened = await call(session, "world_open", {
                    "url": FORM_URI,
                    "wait_ms": 500,
                    "task_goal": "填写资料并提交",
                    "role": "普通用户",
                    "permission_scope": "profile.write",
                    "site_adapter_file": "资料提交 profile-adapter-v1.json",
                })
                assert opened["ready"] is True
                assert opened["trace_persistence_enabled"] is True
                assert opened["site_adapter_id"] == "profile-adapter"
                assert opened["site_adapter_version"] == "1"
                compatibility = await call(session, "world_adapter_compare", {
                    "base_file": "资料提交 profile-adapter-v1.json",
                    "candidate_file": "资料提交 profile-adapter-v2.json",
                })
                assert compatibility["comparison"]["status"] == "incompatible"
                assert compatibility["executed"] is False
                wid = opened["world_id"]
                task_id = opened["task_id"]

                initial_business = await call(session, "world_business_state", {"world_id": wid})
                assert initial_business["business_state"]["state_id"] == "profile.empty"
                allowed = await call(session, "world_operation_check", {
                    "world_id": wid, "operation": "填写资料",
                })
                assert allowed["check"]["allowed"] is True
                empty_plan = await call(session, "world_task_plan", {
                    "world_id": wid,
                    "goal_state": "profile.complete",
                    "allow_candidate": True,
                })
                assert empty_plan["plan"]["status"] == "blocked"
                assert empty_plan["executed"] is False

                found = await call(session, "world_find", {"world_id": wid, "q": "用户名"})
                target = next(item["id"] for item in found["matches"] if item.get("interactive"))
                card = await call(session, "world_act", {
                    "world_id": wid,
                    "kind": "fill",
                    "id": target,
                    "text": "secret-value-that-must-not-be-recorded",
                    "operation": "填写资料",
                    "output_bindings": [{"name": "资料", "ref": "profile.form"}],
                })
                assert card["page_outcome"] == "progressed"

                partial_business = await call(session, "world_business_state", {"world_id": wid})
                assert partial_business["business_state"]["state_id"] == "profile.partial"
                denied = await call(session, "world_operation_check", {
                    "world_id": wid, "operation": "提交资料",
                })
                assert denied["check"]["status"] == "precondition_failed"

                trace = await call(session, "world_trace", {"world_id": wid})
                assert trace["task_goal"] == "填写资料并提交"
                assert len(trace["traces"]) == 1
                item = trace["traces"][0]
                assert item["operation"] == "填写资料"
                assert item["before"]["form_state"] == "empty"
                assert item["after"]["form_state"] == "partial"
                assert item["business_before"]["state_id"] == "profile.empty"
                assert item["business_after"]["state_id"] == "profile.partial"
                assert item["dataflow"]["outputs"][0]["ref"] == "profile.form"
                assert "secret-value-that-must-not-be-recorded" not in json.dumps(trace, ensure_ascii=False)

                graph_result = await call(session, "world_graph", {"world_id": wid})
                graph = graph_result["graph"]
                assert graph["status"] == "candidate"
                assert graph["trace_count"] == 1
                assert len(graph["states"]) == 2
                assert len(graph["edges"]) == 1
                assert graph["edges"][0]["operation"] == "填写资料"
                assert graph["edges"][0]["dataflow"]["outputs"][0]["ref"] == "profile.form"
                assert graph["source_context"]["site_version"] == "fixture-v1"
                replay_check = await call(session, "world_graph_replay_check", {
                    "world_id": wid,
                    "edge_id": graph["edges"][0]["edge_id"],
                    "trace_step": 1,
                })
                assert replay_check["replay"]["status"] == "passed"
                assert replay_check["executed"] is False

                assessment = await call(session, "world_graph_assess", {
                    "world_id": wid,
                    "expected_outcomes": ["progressed"],
                    "min_replays": 2,
                })
                assert assessment["graph_status"] == "candidate"
                assert assessment["lifecycle"]["missing_outcomes"] == []

                await call(session, "world_close", {"world_id": wid})
                archived = await call(session, "world_trace_archive", {"task_id": task_id})
                assert archived["enabled"] is True
                assert len(archived["traces"]) == 1
                archived_graph = await call(session, "world_graph_archive", {
                    "task_id": task_id,
                    "expected_outcomes": ["progressed"],
                    "min_replays": 2,
                })
                assert archived_graph["enabled"] is True
                assert archived_graph["graph"]["trace_count"] == 1
                bundle = await call(session, "world_graph_bundle", {
                    "task_ids": [task_id],
                    "goal": "填写资料并提交",
                    "expected_outcomes": ["progressed"],
                    "min_replays": 2,
                })
                assert bundle["enabled"] is True
                assert bundle["source"]["task_count"] == 1
                assert bundle["graph"]["status"] == "candidate"

                # 严格模式:业务操作不满足前置条件时,world_act 只产生错误证据卡,
                # 不应真正点击页面上的 Continue。
                strict_open = await call(session, "world_open", {
                    "url": FORM_URI,
                    "wait_ms": 500,
                    "task_goal": "提交资料",
                    "business_state_rules": [
                        {"id": "profile.empty", "when": {"form_state": "empty", "outcome_hint": None}},
                        {"id": "profile.complete", "when": {"form_state": "complete", "outcome_hint": None}},
                    ],
                    "operation_contracts": [
                        {"name": "提交资料", "preconditions": ["profile.complete"]},
                    ],
                    "enforce_contracts": True,
                })
                strict_wid = strict_open["world_id"]
                assert strict_open["enforce_contracts"] is True
                reused_plan = await call(session, "world_task_plan", {
                    "world_id": strict_wid,
                    "goal_state": "profile.partial",
                    "task_ids": [task_id],
                    "allow_candidate": True,
                })
                assert reused_plan["source"]["archived_trace_count"] == 1
                assert reused_plan["plan"]["status"] == "ready"
                assert reused_plan["plan"]["steps"][0]["operation"] == "填写资料"
                assert reused_plan["executed"] is False
                continue_match = await call(session, "world_find", {
                    "world_id": strict_wid, "q": "Continue",
                })
                continue_id = next(item["id"] for item in continue_match["matches"] if item.get("interactive"))
                blocked_card = await call(session, "world_act", {
                    "world_id": strict_wid,
                    "kind": "click",
                    "id": continue_id,
                    "operation": "提交资料",
                })
                assert blocked_card["page_outcome"] == "errored"
                assert blocked_card["contract_check"]["status"] == "precondition_failed"
                assert blocked_card["executed"] is False
                strict_state = await call(session, "world_business_state", {"world_id": strict_wid})
                assert strict_state["business_state"]["state_id"] == "profile.empty"
                strict_trace = await call(session, "world_trace", {"world_id": strict_wid})
                assert len(strict_trace["traces"]) == 1
                assert strict_trace["traces"][0]["effect"]["page_outcome"] == "errored"
                await call(session, "world_close", {"world_id": strict_wid})

    print("任务运行时浏览器闭环测试通过")


if __name__ == "__main__":
    asyncio.run(main())
