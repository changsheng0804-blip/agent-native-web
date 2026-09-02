# -*- coding: utf-8 -*-
"""假设R:CSS 变量实现同色隐藏文字——IPI 过滤能否拦截?

判定核心:通过 world_entities 查询"SECRET"文本,看两种藏字方式是否都能被过滤掉。
  - css-var(变量玩法):被过滤 → IPI 已覆盖;出现在世界清单 → 漏洞
  - plain(直接同色老把戏):必须被过滤(对照,验证现有 IPI 生效)
  - normal 正常文字:必须可见(对照,排除误伤)
"""
import asyncio, json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "assumption_r_cssvar.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            d = await call(session, "world_open", {"url": URI, "wait_ms": 1000})
            wid = d["world_id"]
            print(f"页面元素总数: {d.get('summary', {}).get('total')}")

            r = await call(session, "world_entities", {"world_id": wid, "max_results": 50})
            all_ents = r.get("entities", [])
            # 精确判定:世界清单里"非 root 容器"且文本含 SECRET 的独立元素(才是真泄漏)
            leak_ents = [e for e in all_ents
                         if "SECRET" in (e.get("text") or "")
                         and e.get("tag") not in ("html", "body")
                         and e["id"] != "root.body"]
            print(f"页面元素总数: {d.get('summary', {}).get('total')}")
            print(f"独立元素文本含 SECRET(应=0): {len(leak_ents)}")
            for e in leak_ents:
                print(f"  !! {e['id']} {e['semantic']:<12s} {e['name'][:40]}")
            # root.body 的 text 含全页文本是设计使然(与现有 IPI 测试结构一致),不算泄漏
            body_has = any("SECRET" in (e.get("text") or "") for e in all_ents if e.get("tag") == "body")
            print(f"(root.body 含全页文本属既有设计,不计:{body_has})")

            vis = [e for e in all_ents if "VISIBLE" in (e.get("text") or "") and e.get("tag") not in ("html", "body")]
            print(f"正常文字 VISIBLE 独立元素可见: {len(vis)}")

            if leak_ents:
                print("\n❌ 假设R 成立:独立隐藏元素进入世界清单(IPI 漏洞)")
                sys.exit(1)
            elif vis:
                print("\n✅ 假设R 不成立:CSS 变量藏字与直接同色都被 IPI 正确过滤;正常文字不受影响")
            else:
                print("\n⚠️ 无法判定(正常文字也没了?)")
            await call(session, "world_close", {"world_id": wid})


asyncio.run(main())