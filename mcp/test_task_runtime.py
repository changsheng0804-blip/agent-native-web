# -*- coding: utf-8 -*-
"""任务运行时第一阶段纯逻辑测试。

这些测试不启动浏览器，专门守住三条边界：
1. 轨迹不泄露输入原文；
2. 临时网页编号不污染状态身份；
3. 候选图保留观测到的成功与失败分支。
"""
import json
import tempfile
import unittest

from task_runtime import (
    TaskRuntimeGraph,
    TraceStore,
    assess_graph,
    build_graph,
    build_trace_entry,
    normalize_page_state,
    plan_graph,
    sanitize_action,
    stable_route,
)


class TaskRuntimeTests(unittest.TestCase):
    def test_route_drops_query_and_fragment(self):
        self.assertEqual(
            stable_route("https://example.test/checkout?token=secret#pay"),
            "https://example.test/checkout",
        )

    def test_state_key_ignores_dynamic_element_ids(self):
        first = normalize_page_state({
            "url": "https://example.test/form?run=1",
            "state": "stable",
            "form_fields": [{
                "id": "el_1", "name": "email", "type": "email", "filled": True,
            }],
            "dialogs": [{"id": "el_2", "name": "dialog.confirm"}],
            "menus": [],
        })
        second = normalize_page_state({
            "url": "https://example.test/form?run=2",
            "state": "stable",
            "form_fields": [{
                "id": "el_99", "name": "email", "type": "email", "filled": True,
            }],
            "dialogs": [{"id": "el_77", "name": "dialog.confirm"}],
            "menus": [],
        })
        self.assertEqual(first["state_key"], second["state_key"])
        self.assertEqual(first["form_state"], "complete")

    def test_action_sanitizes_input_value(self):
        safe = sanitize_action("world_fill", {
            "kind": "fill",
            "id": "el_1",
            "text": "very-secret-password",
        })
        encoded = json.dumps(safe, ensure_ascii=False)
        self.assertNotIn("very-secret-password", encoded)
        self.assertEqual(safe["text"]["length"], len("very-secret-password"))
        self.assertTrue(safe["text"]["sha256"])

    def test_graph_keeps_success_and_failure_branches(self):
        before = {
            "url": "https://example.test/form",
            "state": "stable",
            "form_fields": [{"id": "el_1", "name": "email", "type": "email", "filled": True}],
            "dialogs": [],
            "menus": [],
        }
        after_success = {
            **before,
            "form_fields": [],
        }
        after_error = {
            **before,
            "form_fields": [],
        }
        success = build_trace_entry(
            trace_id="trace-test", task_id="task-test", step_index=1,
            action="world_press", args={"kind": "press", "key": "Enter", "operation": "submit"},
            before=before, after=after_success,
            payload={
                "page_outcome": "progressed",
                "situation": {"type": "navigation"},
                "effect": {"verdict": "effected", "confidence": "high"},
            }, evidence_seq=1, world_epoch=0,
        )
        error = build_trace_entry(
            trace_id="trace-test", task_id="task-test", step_index=2,
            action="world_press", args={"kind": "press", "key": "Enter", "operation": "submit"},
            before=before, after=after_error,
            payload={
                "page_outcome": "errored",
                "situation": {"type": "network_error"},
                "effect": {"verdict": "unevaluated", "confidence": "high"},
            }, evidence_seq=2, world_epoch=0,
        )
        graph = build_graph([success, error], task_id="task-test", goal="填写资料并提交")
        self.assertEqual(graph["status"], "candidate")
        self.assertEqual(graph["trace_count"], 2)
        outcomes = {
            edge["effects"].get("page_outcome")
            for edge in graph["edges"]
        }
        self.assertIn("progressed", outcomes)
        self.assertIn("errored", outcomes)

    def test_explicit_dataflow_is_preserved_without_values(self):
        trace = build_trace_entry(
            trace_id="trace-flow", task_id="task-flow", step_index=1,
            action="world_act",
            args={
                "kind": "click",
                "operation": "提交资料",
                "input_bindings": [{"from": "填写资料.资料", "to": "提交资料.资料"}],
                "output_bindings": [{"name": "提交结果", "ref": "submission.result"}],
            },
            before={"url": "https://example.test/form", "state": "stable"},
            after={"url": "https://example.test/form", "state": "stable"},
            payload={
                "page_outcome": "progressed",
                "situation": {"type": "form"},
                "effect": {"verdict": "effected", "confidence": "high"},
            }, evidence_seq=1, world_epoch=0,
        )
        graph = build_graph([trace], task_id="task-flow")
        flow = graph["edges"][0]["dataflow"]
        self.assertEqual(flow["inputs"][0]["from"], "填写资料.资料")
        self.assertEqual(flow["outputs"][0]["ref"], "submission.result")
        self.assertNotIn("真实值", json.dumps(trace, ensure_ascii=False))

    def test_trace_store_round_trip(self):
        trace = {"schema_version": "0.1", "task_id": "task-store", "step_index": 1}
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(directory)
            store.append("task-store", trace)
            self.assertEqual(store.read("task-store"), [trace])
            self.assertFalse(store.path_for("用户目标").name.endswith("用户目标.jsonl"))

    def test_lifecycle_requires_replays_and_expected_branches(self):
        def make_trace(trace_id, outcome):
            return build_trace_entry(
                trace_id=trace_id, task_id="task-life", step_index=1,
                action="world_click", args={"kind": "click", "operation": "提交"},
                before={"url": "https://example.test/form", "state": "stable"},
                after={"url": "https://example.test/result", "state": "stable"},
                payload={
                    "page_outcome": outcome,
                    "situation": {"type": "navigation"},
                    "effect": {"verdict": "effected", "confidence": "high"},
                }, evidence_seq=1, world_epoch=0,
            )

        graph = build_graph([
            make_trace("run-1", "progressed"),
            make_trace("run-2", "progressed"),
        ], expected_outcomes=["progressed", "errored"], min_replays=2)
        self.assertEqual(graph["status"], "replayed")
        self.assertEqual(graph["lifecycle"]["missing_outcomes"], ["errored"])
        self.assertEqual(graph["edges"][0]["verification"]["status"], "verified")

        expired = assess_graph(graph, expected_outcomes=["progressed"], valid_until=1)
        self.assertEqual(expired["status"], "expired")
        self.assertEqual(expired["edges"][0]["verification"]["status"], "expired")

    def test_planner_requires_verified_edges_unless_exploring(self):
        graph = {
            "states": [
                {"state_key": "state-empty", "snapshot": {"business_state": "profile.empty"}},
                {"state_key": "state-partial", "snapshot": {"business_state": "profile.partial"}},
                {"state_key": "state-complete", "snapshot": {"business_state": "profile.complete"}},
            ],
            "edges": [
                {
                    "edge_id": "edge-fill-1", "from": "state-empty", "to": "state-partial",
                    "operation": "填写资料", "status": "candidate",
                    "verification": {"status": "candidate"},
                    "business": {"to": ["profile.partial"]},
                },
                {
                    "edge_id": "edge-fill-2", "from": "state-partial", "to": "state-complete",
                    "operation": "补全资料", "status": "candidate",
                    "verification": {"status": "candidate"},
                    "business": {"to": ["profile.complete"]},
                },
            ],
        }
        safe = plan_graph(graph, "state-empty", "profile.complete")
        self.assertEqual(safe["status"], "blocked")
        exploratory = plan_graph(
            graph, "state-empty", "profile.complete", allow_candidate=True,
        )
        self.assertEqual(exploratory["status"], "ready")
        self.assertTrue(exploratory["used_candidate"])
        self.assertFalse(exploratory["publishable"])
        self.assertEqual([step["operation"] for step in exploratory["steps"]], ["填写资料", "补全资料"])
        verified_graph = json.loads(json.dumps(graph))
        for edge in verified_graph["edges"]:
            edge["status"] = "verified"
            edge["verification"]["status"] = "verified"
        verified = plan_graph(verified_graph, "state-empty", "profile.complete")
        self.assertEqual(verified["status"], "ready")
        self.assertTrue(verified["all_verified"])
        self.assertTrue(verified["publishable"])


if __name__ == "__main__":
    unittest.main()
