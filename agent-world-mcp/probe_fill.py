# -*- coding: utf-8 -*-
"""行动层 fill 验证探针:在指定站点的指定输入框上执行 click+fill,验证覆盖层修复。

用法: py -3.11 probe_fill.py <url> <name或id> <text> [wait_ms]
验证点:
  1. click 走哪条路径(method)
  2. fill 走哪条路径(method) —— 修复后应能在可见输入框验证到文本,必要时降级 js-setter
  3. fill 后可见输入框真实 value(直接 DOM 验证)
  4. 世界模型是否能查到填入文本的构件(模型视角)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    raw_url = sys.argv[1]
    url = raw_url.strip().strip('"').strip("'")
    target = sys.argv[2]
    text = sys.argv[3]
    wait_ms = int(sys.argv[4]) if len(sys.argv) > 4 else 4000

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            r = await call(session, "world_open", {"url": url, "wait_ms": wait_ms, "stabilize_ms": 12000}, timeout=120)
            wid = r["world_id"]
            print(f"\n===== fill 验证: {url} -> {target!r}='{text}' =====")

            # 找目标构件(支持 name 或 id)
            try:
                r = await call(session, "world_entity", {"world_id": wid, "id": target}, timeout=30)
                ent = {k: r.get(k) for k in ("id", "name", "tag", "semantic", "text")}
                ent["attrs"] = {k: (r.get("attributes") or {}).get(k) for k in ("placeholder", "ariaLabel")}
                print(f"目标构件: {json.dumps(ent, ensure_ascii=False)}")
            except Exception as e:
                print(f"找不到构件 {target}: {str(e)[:100]}")
                await call(session, "world_close", {"world_id": wid}, timeout=15)
                return

            # 点击
            try:
                r = await call(session, "world_click", {"world_id": wid, "id": target}, timeout=30)
                print(f"click: method={r.get('method')}")
            except Exception as e:
                print(f"click 失败: {str(e)[:120]}")

            await asyncio.sleep(1)

            # fill
            try:
                r = await call(session, "world_fill", {"world_id": wid, "id": target, "text": text}, timeout=30)
                print(f"fill: method={r.get('method')}  target_tag={r.get('target_tag')}")
            except Exception as e:
                print(f"fill 失败: {str(e)[:120]}")

            await asyncio.sleep(1.5)

            # 直接 DOM 验证:所有可见输入框的 value
            r = await call(session, "world_eval", {"world_id": wid, "expression": """(() => {
                const nodes = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(n => {
                    const r = n.getBoundingClientRect();
                    const s = getComputedStyle(n);
                    return r.width>5 && r.height>5 && s.display!=='none' && s.visibility!=='hidden';
                });
                return nodes.map(n => ({
                    ph: n.placeholder || (n.getAttribute('aria-label')||'').slice(0,30),
                    val: (n.value || n.textContent || '').slice(0, 40)
                })).filter(x => x.val);
            })()"""}, timeout=30)
            filled = json.loads(r["result"])
            print(f"DOM 可见输入框有值: {json.dumps(filled, ensure_ascii=False)}")
            matched = any(text in (x.get("val") or "") for x in filled)
            print(f"fill 是否真正生效: {'✅ 是' if matched else '❌ 否'}")

            # 世界模型视角:含文本的构件
            r = await call(session, "world_entities", {"world_id": wid, "text": text, "max_results": 5}, timeout=30)
            print(f"世界模型含 '{text}' 构件: {r['count']} 个")
            for e in r["entities"][:3]:
                print(f"   {e['id']:12s} {e['name'][:44]:44s} text={e.get('text','')[:30]!r}")

            await call(session, "world_close", {"world_id": wid}, timeout=15)
            print("===== 完成 =====")


if __name__ == "__main__":
    asyncio.run(main())
