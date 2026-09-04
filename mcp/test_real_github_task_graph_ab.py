# -*- coding: utf-8 -*-
"""真实站点 A/B 对照:页面优先探索 vs 任务图复用。

A 组:每次打开 GitHub 后重新查找入口,不读取任务图。
B 组:先读取已有任务图并规划,再执行同一个真实流程。

两组都只读 GitHub 公开页面,不登录、不评论、不创建或修改仓库内容。
本测试的重点是测量复用与安全边界,不是把工具调用次数直接等同于模型费用。
"""
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER = str(Path(__file__).resolve().parent / "server.py")
ADAPTER = "GitHub 开放PR筛选 github-pulls-v1.json"
HOME_URL = "https://github.com/git/git"
NO_RESULT_QUERY = "is:pr is:open author:agent-native-web-no-such-user-20260904"
RUNS_PER_ARM = 3


class CallMeter:
    """记录每个实验臂的工具调用量和耗时,不把页面内容写入报告。"""

    def __init__(self):
        self.counts = defaultdict(Counter)
        self.elapsed = defaultdict(float)

    async def call(self, session, name, args, phase):
        started = time.perf_counter()
        result = await asyncio.wait_for(session.call_tool(name, args), timeout=90)
        self.counts[phase][name] += 1
        self.elapsed[phase] += time.perf_counter() - started
        if not result.content:
            raise AssertionError(f"工具没有返回内容: {name}")
        return json.loads(result.content[0].text)

    def snapshot(self, phase):
        return {
            "tool_calls": int(sum(self.counts[phase].values())),
            "world_find_calls": int(self.counts[phase]["world_find"]),
            "world_act_calls": int(self.counts[phase]["world_act"]),
            "world_task_plan_calls": int(self.counts[phase]["world_task_plan"]),
            "world_business_state_calls": int(self.counts[phase]["world_business_state"]),
            "elapsed_seconds": round(self.elapsed[phase], 3),
        }


def first_interactive(matches):
    for item in matches:
        if item.get("interactive"):
            return item
    raise AssertionError(f"没有找到目标构件: {matches[:5]}")


async def find_search_box(meter, session, world_id, phase):
    found = await meter.call(session, "world_find", {
        "world_id": world_id,
        "role": "input",
        "interactive": True,
        "max_results": 30,
    }, phase)
    for item in found["matches"]:
        label = f"{item.get('name', '')} {item.get('text', '')}".lower()
        if item.get("interactive") and ("search" in label or "issue" in label):
            return item
    raise AssertionError(f"没有找到搜索框: {found['matches'][:5]}")


async def open_world(meter, session, phase):
    return await meter.call(session, "world_open", {
        "url": HOME_URL,
        "wait_ms": 3500,
        "stabilize_ms": 15000,
        "task_goal": "查看 git/git 的公开拉取请求筛选结果",
        "site_adapter_file": ADAPTER,
    }, phase)


async def execute_page_first(meter, session, run_index):
    """A 组:完全按页面当前状态重新找入口和目标。"""
    phase = f"A-{run_index}"
    started = time.perf_counter()
    opened = await open_world(meter, session, phase)
    world_id = opened["world_id"]
    try:
        state = await meter.call(session, "world_business_state", {"world_id": world_id}, phase)
        assert state["business_state"]["state_id"] == "github.repo", state

        found = await meter.call(session, "world_find", {
            "world_id": world_id,
            "q": "Pull requests",
            "role": "link",
            "interactive": True,
        }, phase)
        pull_link = first_interactive(found["matches"])
        clicked = await meter.call(session, "world_act", {
            "world_id": world_id,
            "kind": "click",
            "id": pull_link["id"],
            "operation": "打开拉取请求列表",
        }, phase)
        assert clicked["page_outcome"] == "progressed", clicked

        state = await meter.call(session, "world_business_state", {"world_id": world_id}, phase)
        assert state["business_state"]["state_id"] == "github.pulls", state
        search_box = await find_search_box(meter, session, world_id, phase)
        for kind, extra in (
            ("fill", {"text": NO_RESULT_QUERY}),
            ("press", {"key": "Enter"}),
        ):
            action = await meter.call(session, "world_act", {
                "world_id": world_id,
                "kind": kind,
                "id": search_box["id"],
                "operation": "筛选不存在的拉取请求",
                **extra,
            }, phase)
            if kind == "fill":
                assert action["page_outcome"] in ("progressed", "unchanged"), action
            else:
                assert action["page_outcome"] in ("progressed", "uncertain", "unchanged"), action

        state = await meter.call(session, "world_business_state", {"world_id": world_id}, phase)
        assert state["business_state"]["state_id"] == "github.pulls.empty", state
        traces = await meter.call(session, "world_trace", {"world_id": world_id}, phase)
        assert len(traces["traces"]) == 3, traces
        return {
            "task_id": opened["task_id"],
            "traces": traces["traces"],
            "metrics": meter.snapshot(phase),
            "elapsed_seconds": time.perf_counter() - started,
        }
    finally:
        await meter.call(session, "world_close", {"world_id": world_id}, phase)


async def strict_safety_probe(meter, session, task_ids):
    """B 组的安全基线:未完全验证的失败分支不得直接执行。"""
    phase = "B-safety"
    opened = await open_world(meter, session, phase)
    world_id = opened["world_id"]
    try:
        planned = await meter.call(session, "world_task_plan", {
            "world_id": world_id,
            "goal_state": "github.pulls.empty",
            "task_ids": task_ids,
        }, phase)
        assert planned["plan"]["status"] == "blocked", planned
        return planned["plan"]["status"]
    finally:
        await meter.call(session, "world_close", {"world_id": world_id}, phase)


async def execute_graph_reuse(meter, session, task_ids, run_index):
    """B 组:先按历史任务图规划,再执行同一个页面流程。"""
    phase = f"B-{run_index}"
    started = time.perf_counter()
    opened = await open_world(meter, session, phase)
    world_id = opened["world_id"]
    try:
        planned = await meter.call(session, "world_task_plan", {
            "world_id": world_id,
            "goal_state": "github.pulls.empty",
            "task_ids": task_ids,
            "allow_candidate": True,
        }, phase)
        assert planned["plan"]["status"] == "ready", planned
        operations = [step["operation"] for step in planned["plan"]["steps"]]
        assert "打开拉取请求列表" in operations, operations
        assert "筛选不存在的拉取请求" in operations, operations

        found = await meter.call(session, "world_find", {
            "world_id": world_id,
            "q": "Pull requests",
            "role": "link",
            "interactive": True,
        }, phase)
        pull_link = first_interactive(found["matches"])
        clicked = await meter.call(session, "world_act", {
            "world_id": world_id,
            "kind": "click",
            "id": pull_link["id"],
            "operation": "打开拉取请求列表",
        }, phase)
        assert clicked["page_outcome"] == "progressed", clicked
        state = await meter.call(session, "world_business_state", {"world_id": world_id}, phase)
        assert state["business_state"]["state_id"] == "github.pulls", state

        search_box = await find_search_box(meter, session, world_id, phase)
        for kind, extra in (
            ("fill", {"text": NO_RESULT_QUERY}),
            ("press", {"key": "Enter"}),
        ):
            action = await meter.call(session, "world_act", {
                "world_id": world_id,
                "kind": kind,
                "id": search_box["id"],
                "operation": "筛选不存在的拉取请求",
                **extra,
            }, phase)
            if kind == "fill":
                assert action["page_outcome"] in ("progressed", "unchanged"), action
            else:
                assert action["page_outcome"] in ("progressed", "uncertain", "unchanged"), action

        state = await meter.call(session, "world_business_state", {"world_id": world_id}, phase)
        assert state["business_state"]["state_id"] == "github.pulls.empty", state
        traces = await meter.call(session, "world_trace", {"world_id": world_id}, phase)
        assert len(traces["traces"]) == 3, traces
        return {
            "metrics": meter.snapshot(phase),
            "plan_status": planned["plan"]["status"],
            "used_candidate": planned["plan"]["used_candidate"],
            "elapsed_seconds": time.perf_counter() - started,
        }
    finally:
        await meter.call(session, "world_close", {"world_id": world_id}, phase)


def arm_summary(rows):
    def mean(key):
        return round(statistics.mean(item["metrics"][key] for item in rows), 3)

    return {
        "runs": len(rows),
        "successes": len(rows),
        "mean_elapsed_seconds": round(statistics.mean(item["elapsed_seconds"] for item in rows), 3),
        "median_elapsed_seconds": round(statistics.median(item["elapsed_seconds"] for item in rows), 3),
        "mean_tool_calls": mean("tool_calls"),
        "mean_world_find_calls": mean("world_find_calls"),
        "mean_world_act_calls": mean("world_act_calls"),
        "mean_world_task_plan_calls": mean("world_task_plan_calls"),
        "mean_world_business_state_calls": mean("world_business_state_calls"),
    }


async def main():
    with tempfile.TemporaryDirectory() as directory:
        env = os.environ.copy()
        env["AGENT_TASK_RUNTIME_PERSIST"] = "1"
        env["AGENT_TASK_RUNTIME_STORE_DIR"] = directory
        params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
        meter = CallMeter()
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)

                # A 组先建立历史样本:每次都从页面重新探索同一失败分支。
                baseline = [
                    await execute_page_first(meter, session, index)
                    for index in range(1, RUNS_PER_ARM + 1)
                ]
                task_ids = [item["task_id"] for item in baseline]
                bundle = await meter.call(session, "world_graph_bundle", {
                    "task_ids": task_ids,
                    "goal": "查看 git/git 的公开拉取请求筛选结果",
                    "expected_outcomes": ["progressed", "uncertain", "unchanged"],
                    "min_replays": 2,
                }, "B-setup")
                assert bundle["enabled"] is True, bundle
                assert bundle["graph"]["trace_count"] == RUNS_PER_ARM * 3, bundle

                safe_status = await strict_safety_probe(meter, session, task_ids)
                graph_runs = [
                    await execute_graph_reuse(meter, session, task_ids, index)
                    for index in range(1, RUNS_PER_ARM + 1)
                ]

                result = {
                    "site": "GitHub git/git",
                    "workflow": "进入 Pull requests 并筛选确定不存在的作者",
                    "runs_per_arm": RUNS_PER_ARM,
                    "A_page_first": arm_summary(baseline),
                    "B_task_graph": arm_summary(graph_runs),
                    "safety_probe": safe_status,
                    "graph_status": bundle["graph"]["status"],
                    "graph_trace_count": bundle["graph"]["trace_count"],
                    "graph_candidate_runs": sum(1 for item in graph_runs if item["used_candidate"]),
                    "find_call_savings_per_run": round(
                        arm_summary(baseline)["mean_world_find_calls"]
                        - arm_summary(graph_runs)["mean_world_find_calls"], 3
                    ),
                    "act_call_savings_per_run": round(
                        arm_summary(baseline)["mean_world_act_calls"]
                        - arm_summary(graph_runs)["mean_world_act_calls"], 3
                    ),
                    "planning_call_delta_per_run": round(
                        arm_summary(graph_runs)["mean_world_task_plan_calls"]
                        - arm_summary(baseline)["mean_world_task_plan_calls"], 3
                    ),
                    "interpretation": "本阶段主要验证安全规划与路径复用;直接浏览器动作减少量仍需后续执行器改造后再测",
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
