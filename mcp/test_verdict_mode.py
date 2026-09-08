# -*- coding: utf-8 -*-
"""消融开关守护:AGENT_WORLD_VERDICT_MODE 三档必须真的不同。

C1 消融实验的前提是"只改返回值"。若开关失效(如漏剥某字段),实验结论无效。
本测试逐档断言模型实际能看到的字段差异,并检查判定泄漏。

档位:
  full         正常:完整后果卡(含 page_outcome 五态)
  no-verdict   L1:剥掉合成判定,保留原始证据(errors/网络状态码)
  structure    L0:只保留结构,剥掉判定与证据

另断言:三档都保留 target 等结构信息(任务可行性不因消融改变 = 公平性)。
"""
import asyncio, json, os, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "form_names.html").as_uri()

PASS = 0
FAIL = 0
# 判定泄漏关键词(出现在返回体里说明消融没剥干净)
VERDICT_TOKENS = ("errored", "progressed", "unchanged", "uncertain", "challenged",
                  "已生效", "未生效")


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def call(s, n, a, t=60):
    r = await asyncio.wait_for(s.call_tool(n, a), timeout=t)
    return json.loads(r.content[0].text)


async def probe(mode):
    env = dict(os.environ)
    env["AGENT_WORLD_VERDICT_MODE"] = mode
    params = StdioServerParameters(command=sys.executable, args=[SERVER], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            d = await call(s, "world_open", {"url": URI, "wait_ms": 1200})
            wid = d["world_id"]
            f = await call(s, "world_find", {"world_id": wid, "q": "用户名"})
            m = (f.get("matches") or [{}])[0]
            card = await call(s, "world_act", {"world_id": wid, "kind": "fill",
                                               "id": m.get("id"), "text": "probe-user"})
            await call(s, "world_close", {"world_id": wid})
            return card


def leaked_tokens(card):
    blob = json.dumps(card, ensure_ascii=False)
    return [t for t in VERDICT_TOKENS if t in blob]


async def main():
    cards = {}
    for mode in ("full", "no-verdict", "structure"):
        cards[mode] = await probe(mode)

    full, l1, l0 = cards["full"], cards["no-verdict"], cards["structure"]

    # full:含判定
    check("full 模式含 page_outcome", "page_outcome" in full, f"keys={sorted(full)}")
    check("full 模式含 effect", "effect" in full)

    # L1:无合成判定,但保留原始证据通道
    check("no-verdict 已剥离 page_outcome", "page_outcome" not in l1)
    check("no-verdict 已剥离 effect", "effect" not in l1)
    check("no-verdict 已剥离 why/next/handoff",
          not any(k in l1 for k in ("why", "next", "handoff", "recipes")))
    check("no-verdict 无判定泄漏", not leaked_tokens(l1), f"泄漏={leaked_tokens(l1)}")

    # L0:只剩结构
    check("structure 已剥离 page_outcome", "page_outcome" not in l0)
    check("structure 已剥离 effect/action_evidence/feedback",
          not any(k in l0 for k in ("effect", "action_evidence", "feedback")))
    check("structure 无判定泄漏", not leaked_tokens(l0), f"泄漏={leaked_tokens(l0)}")

    # 公平性:三档都保留结构信息(否则任务难度不同,消融不公平)
    for mode, c in cards.items():
        check(f"{mode} 保留 target(公平性)", "target" in c, f"keys={sorted(c)}")

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
