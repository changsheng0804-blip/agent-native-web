# -*- coding: utf-8 -*-
"""B2 守护:假成功对照演示必须持续成立。

`demo_false_success.py` 是"给人看"的对照演示,但它的结论必须被门禁守护——
否则演示可能悄悄变成"因错误的原因好看"。

本测试验证三个场景的核心断言:
  1. 页面在动作返回时显示"成功/处理中"(场景成立,不是空跑)
  2. 后果卡**不得**判 progressed(假成功红线)
  3. 至少有一个场景,后果卡给出了页面之外的环境证据(网络状态码)

注:这是 demo 的守护测试,复用 demo 的实现,不重复造轮子。
"""
import asyncio, sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import demo_false_success as demo  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


async def main():
    results = []
    for cid in sorted(demo.CASES):
        demo.GROUND_TRUTH["requests"] = []
        results.append(await demo.run_demo(cid))

    for r in results:
        title = r["case"]["title"]
        po = r["card"]["page_outcome"]
        naive = r["naive_text"]
        # 1. 场景成立:页面在动作返回时看起来是"成功/处理中"
        looks_ok = any(k in naive for k in ("✅", "处理中", "排队中", "成功"))
        check(f"[{title}] 页面在动作返回时无失败信号(场景成立)", looks_ok, f"naive={naive!r}")
        # 2. 假成功红线
        check(f"[{title}] 后果卡不得判 progressed", po != "progressed", f"page_outcome={po}")

    # 3. 至少一个场景给出环境证据(证明验证层确实带来了页面之外的信息)
    with_evidence = [r for r in results if any(x.get("status") for x in r["ground_truth"])]
    check("至少一个场景捕获到页面之外的环境证据", bool(with_evidence),
          f"有证据的场景数={len(with_evidence)}")

    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
