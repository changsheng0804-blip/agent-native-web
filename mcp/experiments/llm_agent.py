# -*- coding: utf-8 -*-
"""LLM agent 循环(DeepSeek API, OpenAI 兼容)——正式实验的 agent 驱动。

设计:
  - 工具调用协议:模型产出 tool_calls → 实验脚本执行 → 结果回填
  - 结果截断预算:大返回(world_entities/world_changes)截断到 TRUNCATE_CHARS(回执预算纪律)
  - 环境通知注入:每轮 LLM 调用前,把监视器的 pending 失效通知注入为 system 消息
    (模拟"环境主动说话"),agent 修正后通过 premise_ack 恢复
  - 公平性:两臂共用同一循环,仅工具集与通知注入不同
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx

API_URL = "https://api.moonshot.cn/v1/chat/completions"
MODEL = "kimi-k2.7-code"          # 用户指定:Kimi 2.7(不用 kimi-k3,成本高)
API_KEY_ENV = "KIMI_API_KEY"
TRUNCATE_CHARS = 2500
MAX_TOKENS = 4096
LLM_TIMEOUT_S = 120


def _truncate(text, n=TRUNCATE_CHARS):
    text = str(text)
    return text[:n] + f"…(截断,共{len(text)}字符)" if len(text) > n else text


class LlmAgent:
    """最小 LLM agent:每轮调用模型,执行工具,直到完成或步数上限。"""

    def __init__(self, session, system_prompt, tools, max_steps=25, verbose=False,
                 notify=None):
        self.session = session
        self.system_prompt = system_prompt
        self.tools = tools
        self.max_steps = max_steps
        self.verbose = verbose
        self.notify = notify or (lambda: [])   # 返回待注入的环境通知列表
        self.messages = [{"role": "system", "content": system_prompt}]
        self.steps = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = []        # 每次工具调用的记录
        self.final_text = ""

    # ── 主循环 ──────────────────────────────────────────
    async def run(self):
        while self.steps < self.max_steps:
            # 环境通知注入(前提失效等)
            notices = self.notify()
            if notices:
                self.messages.append({"role": "system",
                                      "content": "⚠ 环境通知: " + json.dumps(notices, ensure_ascii=False)})
            resp = await self._chat()
            if resp is None:
                return {"status": "llm_error", "steps": self.steps}
            msg = resp["choices"][0]["message"]
            self.final_text = msg.get("content") or ""
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    self.steps += 1
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except Exception:
                        args = {}
                    result = await self._exec_tool(name, args)
                    self.calls.append({"step": self.steps, "name": name, "args": args,
                                       "t": int(time.time() * 1000)})
                    if self.verbose:
                        print(f"  [{self.steps}] {name}({json.dumps(args, ensure_ascii=False)[:100]})", flush=True)
                    self.messages.append({"role": "assistant",
                                          "content": None,
                                          "tool_calls": [tc]})
                    self.messages.append({"role": "tool",
                                          "tool_call_id": tc["id"],
                                          "content": _truncate(result)})
                continue
            # 无工具调用 = 任务结束
            return {"status": "done", "steps": self.steps, "final": self.final_text}
        return {"status": "max_steps", "steps": self.steps}

    # ── LLM 调用 ────────────────────────────────────────
    async def _chat(self):
        key = os.environ.get(API_KEY_ENV, "")
        body = {"model": MODEL, "messages": self.messages, "tools": self.tools,
                "max_tokens": MAX_TOKENS}
        try:
            r = await httpx.AsyncClient(timeout=LLM_TIMEOUT_S).post(
                API_URL, headers={"Authorization": f"Bearer {key}"}, json=body)
            if r.status_code != 200:
                print(f"  LLM HTTP {r.status_code}: {_truncate(r.text, 200)}", flush=True)
                return None
            data = r.json()
            usage = data.get("usage", {})
            self.tokens_in += usage.get("prompt_tokens", 0)
            self.tokens_out += usage.get("completion_tokens", 0)
            return data
        except Exception as e:
            print(f"  LLM 调用失败: {type(e).__name__}: {str(e)[:150]}", flush=True)
            return None

    # ── 工具执行 ────────────────────────────────────────
    async def _exec_tool(self, name, args):
        if name == "premise_status":
            return json.dumps({"assumptions": list(self._monitor.assumptions.keys()) if hasattr(self, "_monitor") else [],
                               "active": True}, ensure_ascii=False)
        if name.startswith("premise_"):
            return await self._premise_tool(name, args)
        result = await self._mcp_tool(name, args)
        if self.verbose:
            print(f"      <- {_truncate(result, 350)}", flush=True)
        return result

    async def _mcp_tool(self, name, args):
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, {"world_id": self.wid, **args}), timeout=60)
        except Exception as e:
            return f"工具执行失败: {type(e).__name__}: {str(e)[:200]}"
        parts = []
        for item in result.content:
            if getattr(item, "type", None) == "text":
                parts.append(item.text)
        if not parts:
            return "(无返回)"
        joined = "\n".join(parts)
        try:
            data = json.loads(joined)
            # 压缩:entities 只保留 id/name/semantic;changes 只保留计数与 key
            if isinstance(data, dict):
                if "entities" in data:
                    ents = data.get("entities", [])
                    data["entities"] = [{"id": e.get("id"), "name": e.get("name"),
                                         "semantic": e.get("semantic")} for e in ents[:15]]
                    data["entity_count"] = len(ents)
                if "events" in data:
                    data["events"] = data.get("events", [])[:10]
                return json.dumps(data, ensure_ascii=False)
            return joined
        except Exception:
            return joined

    async def _premise_tool(self, name, args):
        m = self._monitor
        if m is None:
            return "前提监视器未启用"
        if name == "premise_assume":
            doc_id = args.get("doc_id") or args.get("name")
            attr = args.get("attr", "value")
            expect = args.get("expect")
            # 双通道解析:模型可能传内核 id(el_N)或 DOM id;div 类元素用 textContent
            expr = self._monitor_expr(doc_id, attr)
            m.assume(args["name"], expr, expect)
            return json.dumps({"ok": True, "monitoring": args["name"], "expr": expr}, ensure_ascii=False)
        if name == "premise_ack":
            m.ack(args.get("name"))
            return json.dumps({"ok": True, "resumed": args.get("name")}, ensure_ascii=False)
        if name == "premise_status":
            return json.dumps({"assumptions": list(m.assumptions.keys())}, ensure_ascii=False)
        return f"未知前提工具 {name}"

    @staticmethod
    def _monitor_expr(doc_id, attr):
        """读元素真实状态的 JS:先按 DOM id,再按内核 id 解析(模型可能传 el_N)。"""
        resolve = (f"document.getElementById('{doc_id}') || "
                   f"(window.agentWorld && agentWorld._runtime.world.elements.get('{doc_id}') || {{}})._el || {{}}")
        if attr == "checked":
            return (f"() => {{ const el = {resolve}; "
                    f"return el.checked === true ? 'true' : 'false'; }}")
        return (f"() => {{ const el = {resolve}; "
                f"return (el.value != null && el.value !== '') ? el.value : (el.textContent || ''); }}")

    # 注入依赖(由实验脚本设置)
    def attach(self, wid, monitor=None):
        self.wid = wid
        self._monitor = monitor
