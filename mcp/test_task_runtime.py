# -*- coding: utf-8 -*-
"""任务运行时第一阶段纯逻辑测试。

这些测试不启动浏览器，专门守住三条边界：
1. 轨迹不泄露输入原文；
2. 临时网页编号不污染状态身份；
3. 候选图保留观测到的成功与失败分支。
"""
import json
import unittest

from task_runtime import (
    TaskRuntimeGraph,
    build_graph,
    build_trace_entry,
    normalize_page_state,
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


if __name__ == "__main__":
    unittest.main()

