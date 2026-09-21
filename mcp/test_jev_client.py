# -*- coding: utf-8 -*-
"""Jev 决策客户端:参数校验、请求构造与响应解析(全部离线,不发真实请求)。

真实联网冒烟测试需显式开启(依赖 OPENROUTER_API_KEY,会消耗极少量额度):
    set JEV_LIVE_TEST=1 && python mcp/test_jev_client.py
"""
import asyncio
import json
import os
import unittest
from unittest import mock

import httpx

from jev_client import (
    API_KEY_ENV,
    DEFAULT_ENDPOINT,
    DEFAULT_MODEL,
    JevClient,
    JevError,
    answer_of,
    answer_value,
    choice,
    decide,
    noul,
    score,
)

TEST_KEY = "sk-or-v1-test-key"


def _sample_answers():
    return {
        "is_urgent": {"type": "noul", "noul": 0.99},
        "department": {
            "type": "choice",
            "choice": "technical",
            "confidence": 0.78,
            "probabilities": {"technical": 0.85, "billing": 0.15},
        },
        "frustration": {"type": "score", "score": 1.0, "confidence": 1.0},
    }


def _sample_response(answers=None, model="typesafe/jev-1.13-20260917"):
    return {
        "model": model,
        "answers": answers if answers is not None else _sample_answers(),
        "usage": {"input_tokens": 306, "output_tokens": 23, "cost": 0.000012852},
        "id": "gen-dec-test",
        "provider": "TypeSafe",
    }


def _questions():
    return {
        "is_urgent": noul("这条消息是否表达紧急诉求"),
        "department": choice("应由哪个团队处理", {"billing": "账单问题", "technical": "技术问题"}),
        "frustration": score("客户情绪激烈程度", ["平静", "不满但克制", "非常愤怒"]),
    }


class QuestionBuilderTests(unittest.TestCase):
    def test_builder_shapes(self):
        self.assertEqual(noul("是否紧急"), {"type": "noul", "instructions": "是否紧急"})
        self.assertEqual(
            choice("选一个", {"a": "甲", "b": "乙"}),
            {"type": "choice", "instructions": "选一个", "criteria": {"a": "甲", "b": "乙"}},
        )
        self.assertEqual(
            score("打分", ["低", "高"]),
            {"type": "score", "instructions": "打分", "criteria": ["低", "高"]},
        )

    def test_builder_rejects_empty_instructions(self):
        for builder in (noul, lambda i: choice(i, {"a": "甲"}), lambda i: score(i, ["低"])):
            with self.subTest(builder=builder):
                with self.assertRaises(ValueError):
                    builder("   ")


class ValidationTests(unittest.TestCase):
    def _client(self):
        return JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    def test_missing_api_key_fails_fast(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(API_KEY_ENV, None)
            with self.assertRaises(JevError) as ctx:
                JevClient()
            self.assertIn(API_KEY_ENV, str(ctx.exception))
            with self.assertRaises(JevError):
                JevClient(api_key="")

    def test_state_validation(self):
        client = self._client()
        for bad_state in ("", "   ", None, 123):
            with self.subTest(state=bad_state):
                with self.assertRaises(ValueError):
                    client.decide(bad_state, {"q": noul("是否")})

    def test_question_validation(self):
        client = self._client()
        cases = [
            ({}, "空 questions"),
            ({"q": "not-a-dict"}, "问题不是 dict"),
            ({"q": {"type": "unknown", "instructions": "x"}}, "未知类型"),
            ({"q": {"type": "noul"}}, "缺 instructions"),
            ({"q": {"type": "choice", "instructions": "x"}}, "choice 缺 criteria"),
            ({"q": {"type": "choice", "instructions": "x", "criteria": {}}}, "choice 空 criteria"),
            ({"q": {"type": "score", "instructions": "x", "criteria": []}}, "score 空 criteria"),
            ({"q": {"type": "noul", "instructions": "x", "criteria": {"a": "b"}}}, "noul 带 criteria"),
            ({"": noul("x")}, "空问题名"),
        ]
        for questions, label in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    client.decide("state", questions)


class DecideTests(unittest.TestCase):
    def test_payload_and_parse(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_sample_response())

        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(handler))
        result = client.decide("客户消息原文", _questions())

        self.assertEqual(captured["url"], DEFAULT_ENDPOINT)
        self.assertEqual(captured["auth"], f"Bearer {TEST_KEY}")
        self.assertEqual(captured["body"]["model"], DEFAULT_MODEL)
        self.assertEqual(captured["body"]["state"], "客户消息原文")
        self.assertEqual(sorted(captured["body"]["questions"]), ["department", "frustration", "is_urgent"])
        self.assertEqual(result["model"], "typesafe/jev-1.13-20260917")
        self.assertEqual(result["usage"]["cost"], 0.000012852)
        self.assertEqual(answer_value(answer_of(result, "is_urgent")), 0.99)
        self.assertEqual(answer_value(answer_of(result, "department")), "technical")

    def test_structured_state_is_serialized(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_sample_response({"q": {"type": "noul", "noul": 0.5}}))

        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(handler))
        client.decide({"url": "https://example.com", "text": "hi"}, {"q": noul("是否")})
        self.assertEqual(json.loads(captured["body"]["state"]), {"url": "https://example.com", "text": "hi"})

    def test_per_call_model_override(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json=_sample_response({"q": {"type": "noul", "noul": 0.5}}))

        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(handler))
        client.decide("state", {"q": noul("是否")}, model="typesafe/jev-1.13")
        self.assertEqual(captured["body"]["model"], "typesafe/jev-1.13")

    def test_http_error_does_not_leak_key(self):
        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": {"message": "bad model"}})))
        with self.assertRaises(JevError) as ctx:
            client.decide("state", {"q": noul("是否")})
        message = str(ctx.exception)
        self.assertIn("400", message)
        self.assertIn("bad model", message)
        self.assertNotIn(TEST_KEY, message)

    def test_missing_answer_is_rejected(self):
        answers = _sample_answers()
        answers.pop("is_urgent")
        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_sample_response(answers))))
        with self.assertRaises(JevError) as ctx:
            client.decide("state", _questions())
        self.assertIn("is_urgent", str(ctx.exception))

    def test_bad_json_is_rejected(self):
        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>not json</html>")))
        with self.assertRaises(JevError):
            client.decide("state", {"q": noul("是否")})

    def test_network_error_is_wrapped(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(handler))
        with self.assertRaises(JevError) as ctx:
            client.decide("state", {"q": noul("是否")})
        self.assertIn("ConnectError", str(ctx.exception))


class AsyncDecideTests(unittest.IsolatedAsyncioTestCase):
    async def test_adecide(self):
        client = JevClient(api_key=TEST_KEY, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_sample_response())))
        result = await client.adecide("state", {"is_urgent": noul("是否紧急")})
        self.assertEqual(answer_value(answer_of(result, "is_urgent")), 0.99)


class HelperTests(unittest.TestCase):
    def test_answer_value_types(self):
        self.assertEqual(answer_value({"type": "noul", "noul": 0.5}), 0.5)
        self.assertEqual(answer_value({"type": "choice", "choice": "a"}), "a")
        self.assertEqual(answer_value({"type": "score", "score": 2.0}), 2.0)

    def test_answer_value_rejects_unknown(self):
        for bad in ("not-a-dict", {"type": "mystery"}):
            with self.subTest(answer=bad):
                with self.assertRaises(ValueError):
                    answer_value(bad)

    def test_answer_of_missing_raises(self):
        with self.assertRaises(JevError):
            answer_of({"answers": {"a": {"type": "noul", "noul": 0.1}}}, "b")

    def test_module_level_decide_wires_client(self):
        with mock.patch.object(JevClient, "decide", autospec=True, return_value={"model": "x"}) as mocked:
            result = decide("state", {"q": noul("是否")}, api_key=TEST_KEY)
        self.assertEqual(result, {"model": "x"})
        self.assertEqual(mocked.call_count, 1)


class McpHandlerTests(unittest.TestCase):
    """world_jev_decide MCP 适配层:结构化成功/失败载荷与 LITE 守卫(不打真实网络)。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_handler_returns_structured_success(self):
        from aw_jev import _t_world_jev_decide

        class FakeClient:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def adecide(self, state, questions, *, model=None):
                return {
                    "model": "typesafe/jev-1.13-20260917",
                    "answers": {"is_urgent": {"type": "noul", "noul": 0.99}},
                    "usage": {"cost": 0.000012852},
                }

        with mock.patch("aw_jev.JevClient", FakeClient):
            result = self._run(_t_world_jev_decide({"state": "客户消息", "questions": {"is_urgent": noul("是否紧急")}}))
        payload = json.loads(result[0].text)
        self.assertEqual(payload["channel"], "jev")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["answers"]["is_urgent"]["noul"], 0.99)
        self.assertEqual(payload["usage"]["cost"], 0.000012852)

    def test_handler_returns_structured_failure_without_key(self):
        from aw_jev import _t_world_jev_decide

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(API_KEY_ENV, None)
            result = self._run(_t_world_jev_decide({"state": "s", "questions": {"q": noul("是否")}}))
        payload = json.loads(result[0].text)
        self.assertFalse(payload["ok"])
        self.assertIn(API_KEY_ENV, payload["why"])

    def test_handler_maps_client_error(self):
        from aw_jev import _t_world_jev_decide

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            async def adecide(self, *args, **kwargs):
                raise JevError("Jev 返回 HTTP 400: bad model")

        with mock.patch("aw_jev.JevClient", FakeClient):
            result = self._run(_t_world_jev_decide({"state": "s", "questions": {"q": noul("是否")}}))
        payload = json.loads(result[0].text)
        self.assertFalse(payload["ok"])
        self.assertIn("400", payload["why"])

    def test_handler_rejects_in_lite_mode(self):
        from aw_jev import _t_world_jev_decide

        with mock.patch.dict(os.environ, {"AGENT_WORLD_LITE": "1"}):
            with self.assertRaises(ValueError):
                self._run(_t_world_jev_decide({"state": "s", "questions": {"q": noul("是否")}}))


class LiveSmokeTests(unittest.TestCase):
    """真实联网冒烟:默认跳过,显式 JEV_LIVE_TEST=1 才跑。"""

    @unittest.skipUnless(os.environ.get(API_KEY_ENV) and os.environ.get("JEV_LIVE_TEST") == "1",
                         "需要 OPENROUTER_API_KEY 且 JEV_LIVE_TEST=1")
    def test_live_noul(self):
        result = decide("The deploy failed twice and customers are seeing 500s. Can someone look now?",
                        {"urgent": noul("Does this need attention right now?")})
        probability = answer_value(answer_of(result, "urgent"))
        self.assertIsInstance(probability, float)
        self.assertGreaterEqual(probability, 0.0)
        self.assertLessEqual(probability, 1.0)


if __name__ == "__main__":
    unittest.main()
