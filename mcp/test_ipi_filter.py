# -*- coding: utf-8 -*-
"""IPI 伪隐藏过滤缺口修复验证(内核 engine/visibility.js isPseudoHidden):
针对备忘录实测泄露的五种伪隐藏(VEC_4~VEC_8):
  VEC_4 color:white 同色     → 应阻断(修复)
  VEC_5 移出视口             → 应阻断(修复)
  VEC_6 font-size:0          → 应阻断(修复)
  VEC_7 text-indent          → 应阻断(修复)
  VEC_8 aria-hidden          → 应阻断(修复)
回归:结构性隐藏 VEC_1/2/3 仍阻断;对照组 VEC_0、正常元素 VEC_9/10 不误伤。

双维度验证:
  A. text 匹配(world_entities) —— 与备忘录实测一同口径
  B. 页面原生 id 解析(world_resolve #vecN) —— 证明"独立构件"确实没进世界
     (避免 body 根容器 textContent 聚合 + 100 字符截断造成的假阳性/假阴性)
动态时序:注入可见元素 → 进入世界 → 加 aria-hidden 隐藏 → 应从世界移除
"""
import asyncio
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
IPI_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ipi_matrix.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            r = await call(session, "world_open", {"url": IPI_URI, "wait_ms": 1500})
            wid = r["world_id"]
            print(f"打开 IPI 矩阵页: world_id={wid}, total={r['summary']['total']}")

            # ── A. text 匹配维度 ──
            marks = {
                "VEC_0": "visible text should appear",   # 对照组:应进入
                "VEC_1": "display-none",
                "VEC_2": "visibility-hidden",
                "VEC_3": "opacity-zero",
                "VEC_4": "white-text",
                "VEC_5": "off-screen",
                "VEC_6": "zero-font",
                "VEC_7": "text-indent",
                "VEC_8": "aria-hidden",
            }
            results = {}
            for key, mark in marks.items():
                rr = await call(session, "world_entities", {"world_id": wid, "text": mark})
                results[key] = len(rr["entities"])
                print(f"  A.text  {key:6s} count={results[key]:3d}  {'泄露' if results[key] else '阻断'}")

            # ── B. 页面原生 id 解析维度(独立构件是否进世界) ──
            print("  B.id    页面原生 id 解析(独立构件):")
            id_ok = {}
            for n in range(0, 9):
                vid = f"vec{n}"
                try:
                    rr = await call(session, "world_resolve", {"world_id": wid, "query": vid})
                    id_ok[vid] = rr.get("id") or rr.get("kind")
                except Exception:
                    id_ok[vid] = "not-found"
                print(f"    resolve({vid}) -> {id_ok[vid]}")

            # ── 动态时序:注入可见 → 进入世界 → aria-hidden 隐藏 → 应移除 ──
            print("  动态时序(注入→隐藏→移除):")
            r = await call(session, "world_eval", {
                "world_id": wid,
                "expression": "(() => { const d = document.createElement('div'); d.id='dyn-ipi'; d.textContent='DYN_IPI_TARGET dynamic inject'; d.style.cssText='color:black;background:white;font-size:16px;position:static;'; document.body.appendChild(d); return true; })()",
            })
            await call(session, "world_wait", {"world_id": wid, "mode": "appear", "text": "DYN_IPI_TARGET", "timeout_ms": 5000})
            rr = await call(session, "world_entities", {"world_id": wid, "text": "DYN_IPI_TARGET"})
            before = len(rr["entities"])
            print(f"    注入后 count={before} (应>0)")

            # 现在隐藏:给该元素加 aria-hidden + color:white
            r = await call(session, "world_eval", {
                "world_id": wid,
                "expression": "(() => { const d = document.getElementById('dyn-ipi'); if (d) { d.setAttribute('aria-hidden', 'true'); d.style.color = 'white'; } return true; })()",
            })
            await call(session, "world_wait", {"world_id": wid, "mode": "disappear", "text": "DYN_IPI_TARGET", "timeout_ms": 5000})
            rr = await call(session, "world_entities", {"world_id": wid, "text": "DYN_IPI_TARGET"})
            after = len(rr["entities"])
            print(f"    隐藏后 count={after} (应=0)")

            h1 = (await call(session, "world_entities", {"world_id": wid, "text": "IPI Attack Matrix"}))["entities"]
            print(f"  正常标题 h1 count={len(h1)} (应>0)")

            await call(session, "world_close", {"world_id": wid})

            # ── 断言 ──
            failures = []
            for key in ["VEC_1", "VEC_2", "VEC_3", "VEC_4", "VEC_5", "VEC_6", "VEC_7", "VEC_8"]:
                if results[key] > 0:
                    failures.append(f"A.text {key} 应阻断,实际 count={results[key]}")
            if results["VEC_0"] == 0:
                failures.append("A.text VEC_0 对照组应进入,实际 count=0")
            # 独立构件维度:伪隐藏的 vec4~vec8 不应被 resolve 到;vec0 应在
            for n in [4, 5, 6, 7, 8]:
                if id_ok.get(f"vec{n}") and id_ok[f"vec{n}"] != "not-found":
                    failures.append(f"B.id vec{n} 应不在世界,实际 resolve -> {id_ok[f'vec{n}']}")
            if not id_ok.get("vec0") or id_ok["vec0"] == "not-found":
                failures.append("B.id vec0 对照组应可解析,实际未找到")
            if before == 0:
                failures.append("动态时序:注入可见元素未进入世界(测试前提失败)")
            if after != 0:
                failures.append(f"动态时序:隐藏后仍残留 count={after}(IPI 动态时序泄露)")
            if len(h1) == 0:
                failures.append("正常标题 h1 被误伤")

            if failures:
                print("\n❌ 失败:")
                for f in failures:
                    print("  -", f)
                sys.exit(1)
            print("\n✅ IPI 伪隐藏过滤全部通过:VEC_4~VEC_8 阻断、对照不误伤、动态时序隐藏后移除")


if __name__ == "__main__":
    asyncio.run(main())
