# -*- coding: utf-8 -*-
"""通用站点探针:对新站点做一轮标准体检,定位原生网页世界/行动层的问题。

用法: py -3.11 probe_site.py <url> [wait_ms] [stabilize_ms]
输出:
  1. world_open 摘要 + 状态卡(page/anomaly/frames/auth/dialogs)
  2. world_layers 图层统计(标签/角色/交互/视口)
  3. 找可交互构件(button/combobox/input/link 各取前几个)
  4. 尝试点击第一个可见按钮,验证变更流是否记录
  5. 汇总:发现的问题点
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


async def call(session, name, args, timeout=40):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    raw_url = sys.argv[1] if len(sys.argv) > 1 else "https://news.ycombinator.com/"
    # cmd 传参可能保留字面双引号,防御性剥离
    url = raw_url.strip().strip('"').strip("'")
    wait_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 4000
    stabilize_ms = int(sys.argv[3]) if len(sys.argv) > 3 else 10000

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=20)
            t0 = time.time()
            r = await call(session, "world_open", {"url": url, "wait_ms": wait_ms, "stabilize_ms": stabilize_ms}, timeout=120)
            dt = round(time.time() - t0, 1)
            wid = r["world_id"]
            st = r.get("status", {})
            print(f"\n===== 站点探针: {url} (world_id={wid}, 耗时 {dt}s) =====")
            print(f"摘要: total={r['summary'].get('total')} interactive={r['summary'].get('interactive')} inViewport={r['summary'].get('inViewport')}")
            print(f"状态卡: state={st.get('page', {}).get('state')} url={st.get('page', {}).get('url', '')[:70]}")
            print(f"  world.elements={st.get('world', {}).get('elements')} domTotal={st.get('page', {}).get('domTotal')}")
            print(f"  anomaly={'anomaly' in str(st.get('page', {}).get('state'))}  frames={len(st.get('frames', []))}  auth={st.get('auth')}")
            for f in st.get("frames", []):
                print(f"    frame: ready={f.get('ready')} elements={f.get('elements')} url={f.get('url','')[:60]}")
            print(f"  dialogs={st.get('dialogs')}")

            # 图层统计
            r = await call(session, "world_layers", {"world_id": wid}, timeout=30)
            layers = r
            struct = layers.get("structure", {})
            sem = layers.get("semantic", {})
            names = layers.get("names", {})
            print(f"\n图层: total={struct.get('total')} 标签种类={len(struct.get('byTag', {}))} 语义角色={sem.get('types')}")
            print(f"  interactive={layers.get('interactive')} named={names.get('named')} unnamed={names.get('unnamed')}")

            # 找可交互构件
            for role in ("button", "combobox", "input", "link"):
                r = await call(session, "world_entities", {"world_id": wid, "role": role, "in_viewport": True, "max_results": 5}, timeout=30)
                es = r.get("entities", [])
                if es:
                    print(f"\n[role={role}] {r.get('count')} 个(视口内),前 {len(es)} 个:")
                    for e in es[:5]:
                        print(f"    {e.get('id',''):12s} {e.get('name','')[:42]:42s} inViewport={e.get('inViewport')}")

            # 尝试点击第一个可见按钮(观察行动层与变更流)
            clicked = False
            r = await call(session, "world_entities", {"world_id": wid, "role": "button", "in_viewport": True, "max_results": 20}, timeout=30)
            for e in r.get("entities", []):
                if not e.get("inViewport"):
                    continue
                try:
                    r2 = await call(session, "world_click", {"world_id": wid, "id": e["id"]}, timeout=30)
                    print(f"\n点击 {e['id']} ({e.get('name','')[:36]}): method={r2.get('method')}")
                    clicked = True
                    break
                except Exception as ex:
                    print(f"  点击 {e.get('id')} 失败: {str(ex)[:100]}")
            if not clicked:
                print("\n(未找到可点击按钮,跳过点击测试)")

            await asyncio.sleep(1)
            r = await call(session, "world_changes", {"world_id": wid, "since": 0}, timeout=30)
            evs = r.get("events", [])
            print(f"\n变更流: to={r.get('to')} 共 {len(evs)} 条,最后 5 条:")
            for e in evs[-5:]:
                print(f"    {e}")

            await call(session, "world_close", {"world_id": wid}, timeout=15)
            print("\n===== 探针完成 =====")


if __name__ == "__main__":
    asyncio.run(main())
