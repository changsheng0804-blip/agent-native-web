# -*- coding: utf-8 -*-
"""调试 world_guide T2:任务词解析 + el_64/el_70 匹配明细。"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import _guide_terms  # noqa: E402

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "server.py"
URL = "https://github.com/git/git"


async def call(session, name, args, timeout=60):
    result = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    for item in result.content:
        if getattr(item, "type", None) == "text":
            return json.loads(item.text)
    return {}


async def main():
    terms = _guide_terms("给该仓库点 Star")
    print("terms:", terms)

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            opened = await call(session, "world_open", {"url": URL, "ready_policy": "action",
                                                        "wait_ms": 8000, "reuse_policy": "never",
                                                        "task_id": "dbg-guide"}, timeout=150)
            wid = opened["world_id"]
            expr = """(ids) => {
                const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\\u4e00-\\u9fff]/g, '');
                const terms = ["给该仓库点", "star"];
                const out = [];
                for (const id of ids) {
                    const e = agentWorld.query.getEntity(id);
                    if (!e) { out.push({id, missing: true}); continue; }
                    const hay = norm([(e.region||''), e.semantic, e.name, e.text,
                                      (e.attributes||{}).href].join(' '));
                    const matched = [];
                    for (const t of terms) if (hay.includes(norm(t))) matched.push(t);
                    out.push({id, name: e.name, text: (e.text||'').slice(0,20),
                              region: e.region, matched});
                }
                return out;
            }"""
            r = await call(session, "world_eval", {"world_id": wid, "expression": expr,
                                                   "arg": ["el_64", "el_69", "el_70", "el_79"]}, timeout=45)
            print(json.dumps(r, ensure_ascii=False, indent=1))
            await call(session, "world_close", {"world_id": wid}, timeout=15)


if __name__ == "__main__":
    asyncio.run(main())
