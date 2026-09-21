# -*- coding: utf-8 -*-
"""Jev 决策模型调用客户端(经 OpenRouter 转发)。

Jev 是 TypeSafe AI 的 System One 决策模型:输入一段 state(待判断的上下文)和若干
类型化问题,返回概率/选项/评分,不生成文本。它不是 chat 模型,不能走 chat/completions,
必须使用 OpenRouter 的 /api/alpha/decisions 端点。

三种问题类型:
  noul   是/否判断,返回成立概率(0~1)
  choice 多选一,返回选中项、各项概率与置信度
  score  有序评分,返回分数、分布与置信度

设计:
  - 一次请求可并行问多个问题:响应时间几乎不变,成本只随问题 token 增加
  - key 只从环境变量 OPENROUTER_API_KEY 读取,不落盘、不打印、不进错误消息
  - 本地参数错误抛 ValueError(快速失败);远端/协议错误抛 JevError
  - decide() 为同步入口,adecide() 为异步入口(供 async agent 循环使用)

用法:
    from jev_client import JevClient, choice, noul

    client = JevClient()
    resp = client.decide(
        state="客户消息原文……",
        questions={
            "is_urgent": noul("这条消息是否表达紧急诉求"),
            "department": choice("应由哪个团队处理", {"billing": "账单问题", "technical": "技术问题"}),
        },
    )
    print(resp["answers"]["is_urgent"]["noul"])   # 0.99
"""
from __future__ import annotations

import json
import os

import httpx

API_KEY_ENV = "OPENROUTER_API_KEY"
# 固定版本,保证可复现;beta 别名 "~typesafe/jev-latest" 可用但数据可能被提供商记录
DEFAULT_MODEL = "typesafe/jev-1.13"
DEFAULT_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_TIMEOUT_S = 30.0

_ALLOWED_TYPES = ("noul", "choice", "score")
_ERROR_BODY_CHARS = 300


class JevError(RuntimeError):
    """Jev 调用或响应协议错误(远端问题,非本地参数错误)。"""


def _truncate(text: object, limit: int = _ERROR_BODY_CHARS) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + f"…(截断,共{len(text)}字符)"


def _clean_state(state: object) -> str:
    if isinstance(state, str):
        if not state.strip():
            raise ValueError("state 不能为空")
        return state
    if isinstance(state, (dict, list)):
        try:
            return json.dumps(state, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("state 必须是字符串或可 JSON 序列化的 dict/list") from exc
    raise ValueError("state 必须是字符串或可 JSON 序列化的 dict/list")


def _clean_instructions(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"问题 {name} 缺少 instructions")
    return text


def noul(instructions: str) -> dict:
    """构造是/否问题(返回成立概率 0~1)。"""
    return {"type": "noul", "instructions": _clean_instructions(instructions, "noul")}


def choice(instructions: str, criteria: dict) -> dict:
    """构造多选一问题。criteria 为 {选项: 说明} 字典。"""
    return {"type": "choice", "instructions": _clean_instructions(instructions, "choice"), "criteria": criteria}


def score(instructions: str, criteria: list) -> dict:
    """构造有序评分问题。criteria 为从低到高的档位列表。"""
    return {"type": "score", "instructions": _clean_instructions(instructions, "score"), "criteria": criteria}


def _clean_questions(questions: object) -> dict:
    """校验并规范化问题集,只保留协议认识的字段。"""
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions 必须是非空 dict(问题名 -> 问题定义)")
    cleaned = {}
    for raw_name, raw_question in questions.items():
        name = str(raw_name).strip()
        if not name:
            raise ValueError("问题名不能为空")
        if not isinstance(raw_question, dict):
            raise ValueError(f"问题 {name} 必须是 dict,建议用 noul()/choice()/score() 构造")
        qtype = raw_question.get("type")
        if qtype not in _ALLOWED_TYPES:
            raise ValueError(f"问题 {name} 的 type 必须是 {_ALLOWED_TYPES} 之一,收到: {qtype!r}")
        item = {"type": qtype, "instructions": _clean_instructions(raw_question.get("instructions"), name)}
        criteria = raw_question.get("criteria")
        if qtype == "choice":
            if not isinstance(criteria, dict) or not criteria:
                raise ValueError(f"choice 问题 {name} 需要非空 criteria 字典(选项 -> 说明)")
            item["criteria"] = {str(key): str(value) for key, value in criteria.items()}
        elif qtype == "score":
            if not isinstance(criteria, list) or not criteria:
                raise ValueError(f"score 问题 {name} 需要非空 criteria 列表(从低到高档位)")
            item["criteria"] = [str(value) for value in criteria]
        elif criteria is not None:
            raise ValueError(f"noul 问题 {name} 不接受 criteria")
        cleaned[name] = item
    return cleaned


def _load_json(response: httpx.Response) -> object:
    if response.status_code != 200:
        raise JevError(f"Jev 返回 HTTP {response.status_code}: {_truncate(response.text)}")
    try:
        return response.json()
    except ValueError as exc:
        raise JevError(f"Jev 响应不是有效 JSON: {_truncate(response.text)}") from exc


def _parse_response(data: object, payload: dict) -> dict:
    """解析响应;所有请求的问题都必须有答案,缺一即报错。"""
    dumped = _truncate(json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data)
    if not isinstance(data, dict):
        raise JevError(f"Jev 响应格式异常: {dumped}")
    answers = data.get("answers")
    if not isinstance(answers, dict) or not answers:
        raise JevError(f"Jev 响应缺少 answers: {dumped}")
    missing = sorted(set(payload["questions"]) - set(answers))
    if missing:
        raise JevError(f"Jev 响应缺少问题的答案: {missing}")
    return {
        "model": data.get("model") or payload["model"],
        "answers": answers,
        "usage": data.get("usage") or {},
        "raw": data,
    }


class JevClient:
    """Jev 决策客户端:一次调用 = 一段 state + 一组问题。

    每次调用自建 HTTP 连接(用完即关);实例本身可跨调用复用配置。
    """

    def __init__(self, api_key: str | None = None, model: str | None = None, endpoint: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_S, transport: httpx.BaseTransport | None = None):
        self.api_key = (api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")).strip()
        if not self.api_key:
            raise JevError(f"缺少 Jev API key:请设置环境变量 {API_KEY_ENV}")
        self.model = (model or DEFAULT_MODEL).strip()
        if not self.model:
            raise ValueError("model 不能为空")
        self.endpoint = endpoint or DEFAULT_ENDPOINT
        self.timeout = float(timeout)
        self._transport = transport

    def _headers(self) -> dict:
        # key 只出现在请求头;任何错误信息都不得包含它
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _build_payload(self, state: object, questions: object, model: str | None) -> dict:
        return {
            "model": (model or self.model),
            "state": _clean_state(state),
            "questions": _clean_questions(questions),
        }

    def decide(self, state: object, questions: object, *, model: str | None = None) -> dict:
        """同步调用:返回 {"model", "answers", "usage", "raw"}。"""
        payload = self._build_payload(state, questions, model)
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as client:
                response = client.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise JevError(f"Jev 请求失败: {type(exc).__name__}: {exc}") from exc
        return _parse_response(_load_json(response), payload)

    async def adecide(self, state: object, questions: object, *, model: str | None = None) -> dict:
        """异步调用:语义与 decide() 一致。"""
        payload = self._build_payload(state, questions, model)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                response = await client.post(self.endpoint, headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise JevError(f"Jev 请求失败: {type(exc).__name__}: {exc}") from exc
        return _parse_response(_load_json(response), payload)


def decide(state: object, questions: object, *, api_key: str | None = None, model: str | None = None,
           endpoint: str | None = None, timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """一次性便捷入口;需要固定配置的循环调用请复用 JevClient 实例。"""
    client = JevClient(api_key=api_key, model=model, endpoint=endpoint, timeout=timeout)
    return client.decide(state, questions)


def answer_of(response: dict, name: str) -> dict:
    """按问题名读取答案;缺失即报错,避免静默拿到 None。"""
    answers = (response or {}).get("answers") or {}
    if name not in answers:
        raise JevError(f"响应中没有问题 {name!r} 的答案;已有: {sorted(answers)}")
    return answers[name]


def answer_value(answer: dict) -> object:
    """提取单个答案的标量值:noul -> 概率,choice -> 选中项,score -> 分数。"""
    if not isinstance(answer, dict):
        raise ValueError("answer 必须是 dict")
    qtype = answer.get("type")
    if qtype == "noul":
        return answer.get("noul")
    if qtype == "choice":
        return answer.get("choice")
    if qtype == "score":
        return answer.get("score")
    raise ValueError(f"未知答案类型: {qtype!r}")
