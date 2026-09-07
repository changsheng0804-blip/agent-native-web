# -*- coding: utf-8 -*-
"""真实站点任务图闭环:GitHub 公开拉取请求筛选流程。

本测试只读 GitHub 公开页面,不登录、不评论、不创建或修改仓库内容。
轨迹归档写入临时目录,测试结束后自动清理,不把真实页面记录提交进仓库。
"""
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
ADAPTER = "GitHub 开放PR筛选 github-pulls-v1.json"
HOME_URL = "https://github.com/git/git"
NO_RESULT_QUERY = "is:pr is:open author:agent-native-web-no-such-user-20260904"


async def call(session, name, args):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=90)
    if not result.content:
        raise AssertionError(f"工具没有返回内容: {name}")
    return json.loads(result.content[0].text)


def first_interactive(matches, predicate=None):
    for item in matches:
        if not item.get("interactive"):
            continue
        if predicate is None or predicate(item):
            return item
    raise AssertionError(f"没有找到目标构件: {matches[:5]}")


async def find_search_box(session, world_id):
    found = await call(session, "world_find", {
        "world_id": world_id,
        "role": "input",
        "interactive": True,
        "max_results": 30,
    })
    return first_interactive(
        found["matches"],
        lambda item: "search" in f"{item.get('name', '')} {item.get('text', '')}".lower()
        or "issue" in f"{item.get('name', '')} {item.get('text', '')}".lower(),
    )


async def explore(session, query=None):
    opened = await call(session, "world_open", {
        "url": HOME_URL,
        "wait_ms": 3500,
        "stabilize_ms": 15000,
        "task_goal": "查看 git/git 的公开拉取请求筛选结果",
        "site_adapter_file": ADAPTER,
    })
    world_id = opened["world_id"]
    task_id = opened["task_id"]
    try:
        state = await call(session, "world_business_state", {"world_id": world_id})
        assert state["business_state"]["state_id"] == "github.repo", state

        found = await call(session, "world_find", {
            "world_id": world_id,
            "q": "Pull requests",
            "role": "link",
            "interactive": True,
        })
        pull_link = first_interactive(found["matches"])
        opened_pulls = await call(session, "world_act", {
            "world_id": world_id,
            "kind": "click",
            "id": pull_link["id"],
            "operation": "打开拉取请求列表",
        })
        assert opened_pulls["page_outcome"] == "progressed", opened_pulls
        state = await call(session, "world_business_state", {"world_id": world_id})
        assert state["business_state"]["state_id"] == "github.pulls", state

        search_box = await find_search_box(session, world_id)
        selected_query = query or "is:pr is:open"
        fill_operation = "筛选不存在的拉取请求" if query == NO_RESULT_QUERY else "筛选开放拉取请求"
        filled = await call(session, "world_act", {
            "world_id": world_id,
            "kind": "fill",
            "id": search_box["id"],
            "text": selected_query,
            "operation": fill_operation,
        })
        assert filled["page_outcome"] in ("progressed", "unchanged"), filled
        pressed = await call(session, "world_act", {
            "world_id": world_id,
            "kind": "press",
            "id": search_box["id"],
            "key": "Enter",
            "operation": fill_operation,
        })
        assert pressed["page_outcome"] in ("progressed", "uncertain", "unchanged"), pressed
        state = await call(session, "world_business_state", {"world_id": world_id})
        expected = "github.pulls.empty" if query == NO_RESULT_QUERY else "github.pulls"
        assert state["business_state"]["state_id"] == expected, state
        traces = await call(session, "world_trace", {"world_id": world_id})
        assert len(traces["traces"]) == 3, traces
        return {"task_id": task_id, "traces": traces["traces"], "world_id": world_id}
    finally:
        await call(session, "world_close", {"world_id": world_id})


def edge_for_trace(graph, trace):
    before_business = (trace.get("business_before") or {}).get("state_id")
    after_business = (trace.get("business_after") or {}).get("state_id")
    for edge in graph.get("edges", []):
        if edge.get("operation") != trace.get("operation"):
            continue
        exact_from = edge.get("from") == (trace.get("before") or {}).get("state_key")
        business_from = before_business in (edge.get("business", {}).get("from") or [])
        if not (exact_from or business_from):
            continue
        exact_to = edge.get("to") == (trace.get("after") or {}).get("state_key")
        business_to = after_business in (edge.get("business", {}).get("to") or [])
        if not (exact_to or business_to):
            continue
        if trace.get("effect", {}).get("page_outcome") in (edge.get("outcomes") or []):
            return edge
    raise AssertionError(f"历史图中没有对应轨迹边: {trace.get('operation')}")


async def main():
    with tempfile.TemporaryDirectory() as directory:
        env = os.environ.copy()
        env["AGENT_TASK_RUNTIME_PERSIST"] = "1"
        env["AGENT_TASK_RUNTIME_STORE_DIR"] = directory
        params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)

                success_one = await explore(session)
                success_two = await explore(session)
                failure = await explore(session, NO_RESULT_QUERY)
                exploration_runs = [success_one, success_two, failure]
                task_ids = [item["task_id"] for item in exploration_runs]
                exploration_trace_count = sum(
                    len(item["traces"]) for item in exploration_runs
                )

                bundle = await call(session, "world_graph_bundle", {
                    "task_ids": task_ids,
                    "goal": "查看 git/git 的公开拉取请求筛选结果",
                    "expected_outcomes": ["progressed", "uncertain", "unchanged"],
                    "min_replays": 2,
                })
                assert bundle["enabled"] is True, bundle
                graph = bundle["graph"]
                assert graph["status"] == "replayed", graph
                assert graph["trace_count"] == 9, graph
                assert any(edge.get("operation") == "打开拉取请求列表" for edge in graph["edges"])
                assert any(edge.get("business", {}).get("to") == ["github.pulls.empty"] for edge in graph["edges"])

                fresh = await call(session, "world_open", {
                    "url": HOME_URL,
                    "wait_ms": 3500,
                    "stabilize_ms": 15000,
                    "task_goal": "查看 git/git 的公开拉取请求筛选结果",
                    "site_adapter_file": ADAPTER,
                })
                fresh_id = fresh["world_id"]
                try:
                    safe_plan = await call(session, "world_task_plan", {
                        "world_id": fresh_id,
                        "goal_state": "github.pulls.empty",
                        "task_ids": task_ids,
                    })
                    assert safe_plan["plan"]["status"] == "blocked", safe_plan

                    exploratory_plan = await call(session, "world_task_plan", {
                        "world_id": fresh_id,
                        "goal_state": "github.pulls.empty",
                        "task_ids": task_ids,
                        "allow_candidate": True,
                    })
                    assert exploratory_plan["plan"]["status"] == "ready", exploratory_plan
                    planned_operations = [step["operation"] for step in exploratory_plan["plan"]["steps"]]
                    assert "打开拉取请求列表" in planned_operations, planned_operations
                    assert "筛选不存在的拉取请求" in planned_operations, planned_operations

                    found = await call(session, "world_find", {
                        "world_id": fresh_id,
                        "q": "Pull requests",
                        "role": "link",
                        "interactive": True,
                    })
                    pull_link = first_interactive(found["matches"])
                    clicked = await call(session, "world_act", {
                        "world_id": fresh_id,
                        "kind": "click",
                        "id": pull_link["id"],
                        "operation": "打开拉取请求列表",
                    })
                    assert clicked["page_outcome"] == "progressed", clicked
                    current_trace = await call(session, "world_trace", {"world_id": fresh_id})
                    click_edge = edge_for_trace(graph, current_trace["traces"][-1])
                    replay = await call(session, "world_graph_replay_check", {
                        "world_id": fresh_id,
                        "edge_id": click_edge["edge_id"],
                        "trace_step": 1,
                        "task_ids": task_ids,
                    })
                    assert replay["replay"]["status"] == "passed", replay

                    search_box = await find_search_box(session, fresh_id)
                    filled = await call(session, "world_act", {
                        "world_id": fresh_id,
                        "kind": "fill",
                        "id": search_box["id"],
                        "text": NO_RESULT_QUERY,
                        "operation": "筛选不存在的拉取请求",
                    })
                    assert filled["page_outcome"] in ("progressed", "unchanged"), filled
                    pressed = await call(session, "world_act", {
                        "world_id": fresh_id,
                        "kind": "press",
                        "id": search_box["id"],
                        "key": "Enter",
                        "operation": "筛选不存在的拉取请求",
                    })
                    assert pressed["page_outcome"] in ("progressed", "uncertain", "unchanged"), pressed
                    final_state = await call(session, "world_business_state", {"world_id": fresh_id})
                    assert final_state["business_state"]["state_id"] == "github.pulls.empty", final_state
                    current_trace = await call(session, "world_trace", {"world_id": fresh_id})
                    failure_trace = current_trace["traces"][-1]
                    failure_edge = edge_for_trace(graph, failure_trace)
                    replay_failure = await call(session, "world_graph_replay_check", {
                        "world_id": fresh_id,
                        "edge_id": failure_edge["edge_id"],
                        "trace_step": failure_trace["step_index"],
                        "task_ids": task_ids,
                    })
                    assert replay_failure["replay"]["status"] == "passed", replay_failure
                    replay_trace_count = len(current_trace["traces"])
                    print(json.dumps({
                        "site": "GitHub git/git",
                        "task_ids": task_ids,
                        "explorations": 3,
                        "metrics": {
                            "exploration_trace_count": exploration_trace_count,
                            "fresh_replay_action_count": replay_trace_count,
                            "safe_plan_block_count": 1,
                            "replay_check_count": 2,
                            "direct_speed_comparison": "not_measured",
                        },
                        "bundle_status": graph["status"],
                        "safe_plan": safe_plan["plan"]["status"],
                        "exploratory_plan": exploratory_plan["plan"]["status"],
                        "success_replay": replay["replay"]["status"],
                        "failure_state": final_state["business_state"]["state_id"],
                        "failure_replay": replay_failure["replay"]["status"],
                    }, ensure_ascii=False, indent=2))
                finally:
                    await call(session, "world_close", {"world_id": fresh_id})


if __name__ == "__main__":
    asyncio.run(main())
