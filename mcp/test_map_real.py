# -*- coding: utf-8 -*-
"""world_map 多类型真站体检:看地图在不同类型网站上的表现差异
指标:
  - 区域数 / 空区域数(重叠锚点噪音)/ 有入口的区域数
  - 入口强 ID 钻取率(抽前 N 个验证 world_entity 可查)
  - 散件占比(other 构件 / total)——地图覆盖度
站点类型:百科/门户/电商/新闻/SPA 重型/控制台(对照组)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")

SITES = [
    ("wiki(百科)", "https://en.wikipedia.org/wiki/Web_Model_Context_Protocol", 3500),
    ("baidu(门户)", "https://www.baidu.com/", 3000),
    ("amazon(电商)", "https://www.amazon.com/", 4000),
    ("bbc(新闻)", "https://www.bbc.com/", 4000),
    ("gf(SPA重型)", "https://www.google.com/travel/flights", 4500),
    ("github(控制台)", "https://github.com/git/git", 4000),
]


async def call(session, name, args, timeout=90):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            print("=== world_map 多类型真站体检 ===", flush=True)
            print(f"{'站点':<16s} {'元素':>6s} {'区域':>4s} {'空区':>4s} {'有入口区':>6s} {'散件%':>6s} {'钻取':>6s}", flush=True)
            rows = []
            for name, url, wait in SITES:
                try:
                    r = await call(session, "world_open", {"url": url, "wait_ms": wait})
                    wid = r["world_id"]
                except Exception as e:
                    print(f"{name:<16s} [SKIP] 打开失败: {type(e).__name__}: {str(e)[:60]}", flush=True)
                    continue
                try:
                    m = await call(session, "world_map", {"world_id": wid, "max_entries": 6})
                except Exception as e:
                    print(f"{name:<16s} [SKIP] map失败: {type(e).__name__}: {str(e)[:60]}", flush=True)
                    await call(session, "world_close", {"world_id": wid})
                    continue
                total = m.get("total", 0)
                regions = m.get("regions", [])
                other = m.get("other", {})
                empty = sum(1 for x in regions if x.get("total", 0) == 0 and not x.get("entries"))
                with_entries = sum(1 for x in regions if x.get("entries"))
                scattered = other.get("total", 0)
                scattered_pct = scattered / total * 100 if total else 0
                # 钻取验证:取地图里前 4 个入口强 ID
                all_entries = [e for x in regions for e in x.get("entries", [])]
                drill_ok = 0
                drill_n = min(len(all_entries), 4)
                for e in all_entries[:drill_n]:
                    try:
                        ent = await call(session, "world_entity", {"world_id": wid, "id": e["id"]})
                        if ent and ent.get("bounds"):
                            drill_ok += 1
                    except Exception:
                        pass
                print(f"{name:<16s} {total:>6d} {len(regions):>4d} {empty:>4d} {with_entries:>6d} {scattered_pct:>5.0f}% {drill_ok:>3d}/{drill_n}", flush=True)
                # 打印区域概览(前 8 个)
                def _rlabel(x):
                    rg = x.get("region", {})
                    return f"{rg.get('semantic')}.{str(rg.get('name',''))[:24]}({x.get('total',0)}/{len(x.get('entries',[]))})"
                print(f"   区域: {[_rlabel(x) for x in regions[:8]]}", flush=True)
                rows.append({"name": name, "total": total, "regions": len(regions), "empty": empty,
                             "scattered_pct": scattered_pct, "drill": f"{drill_ok}/{drill_n}"})
                await call(session, "world_close", {"world_id": wid})

            print("\n=== 汇总 ===", flush=True)
            if not rows:
                print("全部失败")
                return
            avg_scatter = sum(r["scattered_pct"] for r in rows) / len(rows)
            total_empty = sum(r["empty"] for r in rows)
            print(f"平均散件占比 {avg_scatter:.0f}% (越低=地图覆盖越好)", flush=True)
            print(f"空区域总数 {total_empty}(重叠锚点噪音)", flush=True)
            if avg_scatter <= 60 and total_empty <= 8:
                print("✅ 地图整体可用:覆盖良好且噪音少", flush=True)
            else:
                print("⚠️ 地图有短板:散件多或空区域多,需针对性优化", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
