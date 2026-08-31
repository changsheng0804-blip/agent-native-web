# -*- coding: utf-8 -*-
"""world_wait 事件驱动验证(替代 0.3s 轮询):
1. 条件已满足 -> 立即返回(不等待)
2. 动态注入出现 -> 事件命中
3. disappear:注入->出现->移除->消失
4. 超时兜底 -> matched=False, driven=timeout
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "test_fixtures" / "dyn.html").as_uri()


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 1500})
            wid = r["world_id"]
            print(f"打开动态页: world_id={wid}, total={r['summary']['total']}")

            # 1. 条件已满足(标题已存在) -> 应立即返回
            t0 = time.time()
            r = await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": "动态测试页", "timeout_ms": 10000})
            dt = time.time() - t0
            print(f"1.已满足 appear: matched={r['matched']} count={r.get('count')} driven={r.get('driven')} 耗时={dt:.2f}s")
            assert r["matched"] and r.get("driven") == "event", "已满足应立即命中(event)"
            assert dt < 5, "事件驱动应立即返回,不应轮询等待"

            # 2. 动态注入出现(每次 evaluate 注入一个元素) -> 事件命中
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": "() => { const d=document.createElement('div'); d.id='evt-x'; d.textContent='EVT_TARGET appear-event'; document.body.appendChild(d); return true; }",
            })
            t0 = time.time()
            r = await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": "EVT_TARGET", "timeout_ms": 10000})
            dt = time.time() - t0
            print(f"2.动态出现 appear: matched={r['matched']} count={r.get('count')} driven={r.get('driven')} 耗时={dt:.2f}s")
            assert r["matched"] and r.get("driven") == "event", "动态出现应事件命中"

            # 3. disappear:移除该元素 -> 事件命中消失
            await call(session, "world_eval", {
                "world_id": wid,
                "expression": "() => { const d=document.getElementById('evt-x'); if (d) d.remove(); return true; }",
            })
            t0 = time.time()
            r = await call(session, "world_wait", {"world_id": wid, "mode": "disappear", "text": "EVT_TARGET", "timeout_ms": 10000})
            dt = time.time() - t0
            print(f"3.动态消失 disappear: matched={r['matched']} count={r.get('count')} driven={r.get('driven')} 耗时={dt:.2f}s")
            assert r["matched"] and r.get("driven") == "event", "动态消失应事件命中"

            # 4. 超时兜底:等待永不出现的文本
            t0 = time.time()
            r = await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": "NEVER_EXISTS_XYZ", "timeout_ms": 2000})
            dt = time.time() - t0
            print(f"4.超时兜底 appear: matched={r['matched']} driven={r.get('driven')} timeout_ms={r.get('timeout_ms')} 耗时={dt:.2f}s")
            assert not r["matched"] and r.get("driven") == "timeout", "超时应 matched=False"
            assert 1.5 <= dt <= 6, f"超时兜底应约 2s,实际 {dt:.2f}s"

            await call(session, "world_close", {"world_id": wid})
            print("\n✅ world_wait 事件驱动验证通过:已满足立即返回/动态出现/消失/超时兜底")


if __name__ == "__main__":
    asyncio.run(main())
