# -*- coding: utf-8 -*-
"""视觉证据链路验证(world_screenshot Set-of-Mark / ImageContent / 视觉 diff 兜底)

PR #2 复习出的正式测试;POC 脚本已降级到 mcp/poc/。
覆盖:
 1. world_screenshot(annotated=True) 返回 text+image 双 content,ImageContent 有效
 2. return_base64=False 只返回文本
 3. 纯 CSS 变化(无 DOM 增删)点击 → visual-effected/high(视觉 diff 兜底生效)
 4. 负例:无副作用点击 → no-change(不误报 visual-effected)
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
DYN_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "dyn.html").as_uri()
VIS_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "visual.html").as_uri()


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return r


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # ── 1. annotated + ImageContent ──
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = json.loads(r.content[0].text)["world_id"]

            r = await call(session, "world_screenshot", {"world_id": wid, "annotated": True})
            types_ = [c.type for c in r.content]
            assert types_ == ["text", "image"], f"annotated 应返回 text+image,实际 {types_}"
            img = next(c for c in r.content if c.type == "image")
            assert img.mimeType == "image/png", f"mimeType={img.mimeType}"
            raw = base64.b64decode(img.data)
            assert raw[:8] == b"\x89PNG\r\n\x1a\n", "data 应为有效 PNG"
            print(f"1. annotated 截图: text+image 双 content, PNG {len(raw)//1024}KB")
            txt = json.loads(r.content[0].text)
            assert "标注" in txt["target"], f"描述应提到标注: {txt['target']}"
            await call(session, "world_close", {"world_id": wid})

            # ── 2. return_base64=False ──
            r = await call(session, "world_open", {"url": DYN_URI, "wait_ms": 800})
            wid = json.loads(r.content[0].text)["world_id"]
            r = await call(session, "world_screenshot", {"world_id": wid, "return_base64": False})
            assert [c.type for c in r.content] == ["text"], "return_base64=False 应只返回文本"
            print("2. return_base64=False: 只返回文本 ✅")
            await call(session, "world_close", {"world_id": wid})

            # ── 3. 视觉 diff 兜底:纯 CSS 变色(无 DOM 增删) ──
            r = await call(session, "world_open", {"url": VIS_URI, "wait_ms": 800})
            wid = json.loads(r.content[0].text)["world_id"]
            ents = json.loads((await call(session, "world_entities", {
                "world_id": wid, "text": "视觉动画", "max_results": 8,
            })).content[0].text)["entities"]
            inter = [e for e in ents if e.get("interactive")]
            assert inter, "应找到可交互的视觉动画目标"
            r = await call(session, "world_click", {"world_id": wid, "id": inter[0]["id"], "visual_evidence": True})
            data = json.loads(r.content[0].text)
            eff = data.get("effect", {})
            assert eff.get("verdict") == "visual-effected", f"纯 CSS 变化应判 visual-effected,实际 {eff.get('verdict')}"
            assert eff.get("confidence") == "high"
            assert eff.get("visual_diff_score", 0) > 1.5, f"diff score={eff.get('visual_diff_score')}"
            print(f"3. 纯 CSS 变色 → visual-effected/high (RMS={eff.get('visual_diff_score')}) ✅")

            # ── 4. 负例:无副作用标题点击不应 visual-effected(同样开启视觉证据验证不误报) ──
            ents = json.loads((await call(session, "world_entities", {
                "world_id": wid, "text": "无副作用标题", "max_results": 8,
            })).content[0].text)["entities"]
            assert ents, "应找到无副作用标题"
            r = await call(session, "world_click", {"world_id": wid, "id": ents[0]["id"], "visual_evidence": True})
            data = json.loads(r.content[0].text)
            eff = data.get("effect", {})
            assert eff.get("verdict") == "no-change", f"负例应 no-change,实际 {eff.get('verdict')}"
            print("4. 负例点击 → no-change(视觉兜底不误报) ✅")
            await call(session, "world_close", {"world_id": wid})

            print("\n✅ 视觉证据链路全部通过:SoM 标注 / ImageContent / 视觉 diff 兜底 / 负例")


if __name__ == "__main__":
    asyncio.run(main())