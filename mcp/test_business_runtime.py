# -*- coding: utf-8 -*-
"""显式业务状态规则和操作前置条件测试。"""
import unittest

from business_runtime import (
    attach_business_runtime,
    check_operation,
    normalize_operation_contracts,
    project_business_state,
)
from task_runtime import build_trace_entry, normalize_page_state


RULES = [
    {"id": "profile.empty", "when": {"form_state": "empty", "outcome_hint": None}},
    {"id": "profile.partial", "when": {"form_state": "partial", "outcome_hint": None}},
    {"id": "profile.complete", "when": {"form_state": "complete", "outcome_hint": None}},
    {"id": "profile.challenge", "when": {"outcome_hint": "challenged"}},
]

CONTRACTS = [
    {
        "name": "提交资料",
        "preconditions": ["profile.complete"],
        "inputs": [{"from": "填写资料.资料", "to": "提交资料.资料"}],
        "outputs": [{"name": "提交结果", "ref": "profile.result"}],
        "executor": "world_act",
    }
]


def state(form_state, outcome=None):
    return normalize_page_state({
        "url": "https://example.test/profile",
        "state": "stable",
        "form_fields": [{"name": "profile", "type": "text", "filled": form_state == "complete"}],
        "dialogs": [],
        "menus": [],
    }, outcome)


class BusinessRuntimeTests(unittest.TestCase):
    def test_explicit_rule_projects_business_state(self):
        self.assertEqual(project_business_state(state("empty"), RULES)["state_id"], "profile.empty")
        self.assertEqual(project_business_state(state("complete"), RULES)["state_id"], "profile.complete")
        self.assertEqual(project_business_state(state("empty", "challenged"), RULES)["state_id"], "profile.challenge")

    def test_conflicting_rules_are_ambiguous(self):
        conflicting = RULES + [{"id": "profile.other", "when": {"form_state": "empty", "outcome_hint": None}}]
        result = project_business_state(state("empty"), conflicting)
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["state_id"])

    def test_operation_check_requires_declared_precondition(self):
        contracts = normalize_operation_contracts(CONTRACTS)
        allowed = check_operation(contracts, "提交资料", project_business_state(state("complete"), RULES))
        denied = check_operation(contracts, "提交资料", project_business_state(state("empty"), RULES))
        unknown = check_operation(contracts, "不存在的操作", project_business_state(state("complete"), RULES))
        self.assertTrue(allowed["allowed"])
        self.assertEqual(denied["status"], "precondition_failed")
        self.assertEqual(unknown["status"], "unknown_contract")

    def test_operation_check_rejects_role_scope_and_site_version(self):
        contracts = normalize_operation_contracts([{
            "name": "发布资料",
            "preconditions": ["profile.complete"],
            "required_roles": ["管理员"],
            "required_scopes": ["profile.publish"],
            "site_versions": ["v2"],
        }])
        business = project_business_state(state("complete"), RULES)
        base = {"role": "管理员", "permission_scope": "profile.publish", "site_version": "v2"}
        self.assertTrue(check_operation(contracts, "发布资料", business, base)["allowed"])
        self.assertEqual(
            check_operation(contracts, "发布资料", business, {**base, "role": "普通用户"})["status"],
            "permission_denied",
        )
        self.assertEqual(
            check_operation(contracts, "发布资料", business, {**base, "permission_scope": "profile.read"})["status"],
            "permission_denied",
        )
        self.assertEqual(
            check_operation(contracts, "发布资料", business, {**base, "site_version": "v1"})["status"],
            "site_version_denied",
        )

    def test_attach_adds_business_state_and_contract_dataflow(self):
        trace = build_trace_entry(
            trace_id="trace-business", task_id="task-business", step_index=1,
            action="world_click", args={"kind": "click", "operation": "提交资料"},
            before={"url": "https://example.test/profile", "state": "stable", "form_fields": [{"name": "profile", "type": "text", "filled": True}]},
            after={"url": "https://example.test/profile", "state": "stable", "form_fields": [{"name": "profile", "type": "text", "filled": True}]},
            payload={"page_outcome": "progressed", "situation": {"type": "form"}, "effect": {"verdict": "effected", "confidence": "high"}},
            evidence_seq=1, world_epoch=0,
        )
        attach_business_runtime(trace, RULES, CONTRACTS)
        self.assertEqual(trace["business_before"]["state_id"], "profile.complete")
        self.assertEqual(trace["operation_contract"]["name"], "提交资料")
        self.assertEqual(trace["dataflow"]["inputs"][0]["from"], "填写资料.资料")
        self.assertIn("business_state", trace["before"])


if __name__ == "__main__":
    unittest.main()
