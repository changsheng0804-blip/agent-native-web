# -*- coding: utf-8 -*-
"""world_jev_decide:MCP 适配层(把 jev_client 的结构化决策能力暴露给 agent)。

设计:
  - 纯网络调用,无 Playwright 线程亲和 → 在 call_tool 中走异步路径,不排入单线程
    执行器(避免一次 1~3s 的远端决策阻塞页面动作队列)
  - 成功返回 {"channel": "jev", "ok": true, "model", "answers", "usage"}
  - 失败返回 {"channel": "jev", "ok": false, "why": ...}(结构化,agent 可据此降级,
    例如 key 缺失时回退到自身判断)
  - key 只来自环境变量 OPENROUTER_API_KEY;工具入参不接受 key,不落盘、不打印
"""
from __future__ import annotations

from aw_core import CANONICAL_TOOLS, _lite_mode
from aw_runtime import _ok
from jev_client import JevClient, JevError


async def _t_world_jev_decide(args: dict):
    if _lite_mode() and "world_jev_decide" not in CANONICAL_TOOLS:
        raise ValueError(f"AGENT_WORLD_LITE 模式只开放 6 个默认工具({sorted(CANONICAL_TOOLS)});"
                         "world_jev_decide 是内部/调试工具,请勿在 LITE 会话调用")
    try:
        client = JevClient(model=args.get("model"))
    except JevError as exc:
        return _ok({"channel": "jev", "ok": False, "why": str(exc)})
    try:
        result = await client.adecide(args.get("state"), args.get("questions"))
    except (JevError, ValueError) as exc:
        return _ok({"channel": "jev", "ok": False, "why": str(exc)})
    return _ok({
        "channel": "jev",
        "ok": True,
        "model": result["model"],
        "answers": result["answers"],
        "usage": result["usage"],
    })
