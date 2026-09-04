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
                    "workflow_id": "profile-submit",
                    "site_version": "fixture-v1",
                    "role": "普通用户",
                    "permission_scope": "profile.write",
                })
                assert opened["ready"] is True
                assert opened["trace_persistence_enabled"] is True
                wid = opened["world_id"]
                task_id = opened["task_id"]

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

                trace = await call(session, "world_trace", {"world_id": wid})
                assert trace["task_goal"] == "填写资料并提交"
                assert len(trace["traces"]) == 1
                item = trace["traces"][0]
                assert item["operation"] == "填写资料"
                assert item["before"]["form_state"] == "empty"
                assert item["after"]["form_state"] == "partial"
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

    print("任务运行时浏览器闭环测试通过")


if __name__ == "__main__":
    asyncio.run(main())
