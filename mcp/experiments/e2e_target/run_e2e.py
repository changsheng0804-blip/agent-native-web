# -*- coding: utf-8 -*-
"""端到端三臂实验编排器(靶场 run_e2e)。

臂:
  ui      策略只用页面文本判断(传统 agent)
  runtime 策略 + harness 后果卡(经 /api/verify 通道轮询账本) + 在途闸门
  channel 策略可直接查账本工具(world_eval fetch /api/verify)——WebMCP 端游上界
策略:
  N 盲动   提交→1.5s→看页面→未见"已确认/已通过"就再提交(最多3次)→按最后页面声明
  P 稳判   提交→按臂的反馈(盲等4s / 后果卡 / 账本轮询)→声明
计分:只用账本(tasks.check_success)。假成功 = declared_ok ∧ ¬ledger_ok。

用法:
  python mcp/experiments/e2e_target/run_e2e.py --pilot
  python mcp/experiments/e2e_target/run_e2e.py --full
  python mcp/experiments/e2e_target/run_e2e.py --summary
"""
import argparse
import asyncio
import hashlib
import json
import random
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

from framework import FaultProfile, TargetServer              # noqa: E402
from sites.shop import ShopSite                               # noqa: E402
from sites.content import ContentSite                          # noqa: E402
from tasks import TASKS, task_by_id, check_success, dump_tasks  # noqa: E402

SERVER = str(HERE.parent.parent / "server.py")
OUT = HERE / "e2e_results.jsonl"
OK_STR = {"shop": "状态:已确认", "content": "状态:已通过"}
OK_LIE = {"shop": "订单提交成功", "content": "发布成功"}
BAD_STR = {"shop": ("状态:已取消", "状态:失败"), "content": ("状态:已驳回",)}
N_MAIN = 20
TERMINAL = {"confirmed", "cancelled", "failed", "approved", "rejected"}


def build_server(site, task, seed):
    from framework import Ledger
    fault = FaultProfile(seed=seed, **task["faults"])
    ledger = Ledger(seed)
    sites = {"shop": ShopSite(ledger, fault, {}), "content": ContentSite(ledger, fault, {})}
    ts = TargetServer(sites, seed)
    base = ts.start()
    return ts, base, ledger, fault


class RunContext:
    def __init__(self, session, wid, base, site, task, key, arm, ledger):
        self.session = session
        self.wid = wid
        self.base = base
        self.site = site
        self.task = task
        self.key = key
        self.arm = arm
        self.ledger = ledger
        self.submits = 0
        self.in_flight = False
        self.ledger_status = None

    async def _eval(self, expr):
        r = await asyncio.wait_for(
            self.session.call_tool("world_eval", {"world_id": self.wid, "expression": expr}), timeout=30)
        return json.loads(r.content[0].text)["result"]

    async def page_text(self):
        # 刷新式读取:fetch 当前 URL 重新解析(等价于浏览器刷新,仍是纯页面文本,不碰账本)
        t = await self._eval(
            "fetch(location.href).then(r=>r.text())"
            ".then(t=>new DOMParser().parseFromString(t,'text/html').body.innerText)")
        return str(t)

    async def goto(self, path):
        await asyncio.wait_for(
            self.session.call_tool("world_open", {"world_id": self.wid,
                                                  "url": self.base + path, "wait_ms": 400}), timeout=30)

    async def click(self, q, index=0):
        r = await asyncio.wait_for(
            self.session.call_tool("world_find", {"world_id": self.wid, "q": q}), timeout=30)
        d = json.loads(r.content[0].text)
        matches = (d.get("matches") or []) if isinstance(d, dict) else []
        # 只挑可交互元素(链接/按钮):容器(ul/li/div)点击无效
        clickable = [m for m in matches if m.get("href") or m.get("interactive")]
        if index >= len(clickable):
            return False
        await asyncio.wait_for(
            self.session.call_tool("world_act", {"world_id": self.wid,
                                                 "kind": "click", "id": clickable[index]["id"]}), timeout=30)
        await asyncio.sleep(0.4)
        return True

    async def fill_form(self):
        if self.site == "shop":
            expr = (f"document.querySelector('input[name=client_key]').value='{self.key}';"
                    "document.querySelector('input[name=name]').value='测试用户';"
                    "document.querySelector('input[name=addr]').value='一号路 1 号'; true")
        else:
            expr = (f"document.querySelector('input[name=client_key]').value='{self.key}';"
                    "document.querySelector('input[name=title]').value='我的文章';"
                    "document.querySelector('textarea[name=body]').value='内容'; true")
        await self._eval(expr)

    async def goto_form(self):
        """列表页 → 表单页。shop:点第 item 个「购买」链接;content:点「发布新文章」。
        若不在列表页(如在订单详情),先点「返回」链接再进表单。"""
        if self.site == "shop":
            idx = {"a1": 0, "b2": 1, "c3": 2}.get(self.task.get("item"), 0)
            if await self.click("购买", idx):
                return True
            if not await self.click("返回商城"):
                return False
            return await self.click("购买", idx)
        if await self.click("发布新文章"):
            return True
        if not await self.click("返回"):
            return False
        return await self.click("发布新文章")

    async def submit(self):
        """提交动作(经 UI)。runtime 臂有在途闸门:未决时拒绝执行。"""
        if self.arm == "runtime" and self.in_flight:
            return False
        ok = await self.click("提交订单" if self.site == "shop" else "发布")
        if not ok:
            ok = await self.click("提交")
        if ok:
            self.submits += 1
            if self.arm == "runtime":
                self.in_flight = True
        return ok

    # ── 反馈通道 ──
    def _verify_http(self):
        url = f"{self.base}/{self.site}/api/verify?client_key={self.key}"
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def wait_ledger_terminal(self, timeout=9.0, settle=4.0):
        """轮询账本到终态并验证稳定(连续 settle 秒不变)。settle 需覆盖故障回滚窗口(3s),否则会漏看延迟回滚。"""
        t0 = time.time()
        last_status, last_change = None, 0.0
        while time.time() - t0 < timeout:
            v = self._verify_http()
            st = v.get("status", "") if v.get("exists") else ""
            now = time.time()
            if st != last_status:
                last_status, last_change = st, now
            elif (st in TERMINAL or not v.get("exists")) and now - last_change >= settle:
                self.ledger_status = st
                if self.arm == "runtime":
                    self.in_flight = False
                return v
            time.sleep(0.4)
        v = self._verify_http()
        self.ledger_status = v.get("status") if v.get("exists") else ""
        if self.arm == "runtime":
            self.in_flight = False
        return v

    async def check_ledger(self):
        """channel 臂:直接查账本(WebMCP 端游工具)。"""
        return await self._eval(f"fetch('/{self.site}/api/verify?client_key={self.key}')"
                                ".then(r => r.json()).then(JSON.stringify)")

    def runtime_card(self):
        v = self.wait_ledger_terminal()
        ok = v.get("exists") and v.get("status") in {"confirmed", "approved"}
        return {"ledger": v, "verdict": "progressed" if ok else
                ("unchanged" if not v.get("exists") else "errored")}


async def looks_ok(ctx, txt):
    if OK_STR[ctx.site] in txt:
        return True
    # 谎报页:shop 的"订单提交成功!"(内容站无此故障)——UI 臂会当真
    return OK_LIE[ctx.site] in txt


async def policy_N(ctx):
    """盲动:进表单→提交→1.0s→看页面→没确认就回列表重开表单再提交(最多3次)→按最后页面声明。"""
    if not await ctx.goto_form():
        return False
    await ctx.fill_form()
    declared = False
    for i in range(3):
        ok = await ctx.submit()
        if not ok:
            break
        await asyncio.sleep(1.0)
        txt = await ctx.page_text()
        if await looks_ok(ctx, txt):
            declared = True
            break
        # 盲动:回到列表重开表单,用同一幂等键再试(重复提交行为本身是危害)
        if not await ctx.goto_form():
            break
        await ctx.fill_form()
    txt = await ctx.page_text()
    declared = await looks_ok(ctx, txt)
    return declared


async def policy_P(ctx):
    """稳判:进表单→提交一次,按臂的反馈决定。"""
    if not await ctx.goto_form():
        return False
    await ctx.fill_form()
    await ctx.submit()
    if ctx.arm == "ui":
        await asyncio.sleep(5.0)
        txt = await ctx.page_text()
        declared = await looks_ok(ctx, txt)
        if any(b in txt for b in BAD_STR[ctx.site]):
            declared = False
        return declared
    if ctx.arm == "runtime":
        card = ctx.runtime_card()
        return card["verdict"] == "progressed"
    # channel:轮询账本终态(稳定检测)
    ctx.wait_ledger_terminal()
    return ctx.ledger_status in {"confirmed", "approved"}


POLICIES = {"N": policy_N, "P": policy_P}


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def run_one(site, tid, arm, policy_name, run_id, out_path):
    task = task_by_id(site, tid)
    seed = f"e2e-{site}-{tid}-{arm}-{policy_name}-{run_id}"
    key = f"K-{hashlib.sha1(seed.encode()).hexdigest()[:12]}"

    ts, base, ledger, fault = build_server(site, task, seed)
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    result = None
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                d = await call(session, "world_open", {"url": base + f"/{site}/", "wait_ms": 600})
                wid = d["world_id"]
                ctx = RunContext(session, wid, base, site, task, key, arm, ledger)
                t0 = time.time()
                try:
                    declared = await asyncio.wait_for(POLICIES[policy_name](ctx), timeout=60)
                except asyncio.TimeoutError:
                    declared = False
                # 等待故障窗口落地(回滚/异步失败),再按账本打分
                await asyncio.sleep(2.0)
                v = ctx._verify_http()
                ok, reason = check_success(task, v, ledger.resources,
                                           bool(declared), ctx.submits)
                result = {
                    "site": site, "task": tid, "arm": arm, "policy": policy_name,
                    "run": run_id, "seed": seed,
                    "declared_ok": bool(declared), "submit_count": ctx.submits,
                    "ledger": v, "success": ok, "reason": reason,
                    "duration_s": round(time.time() - t0, 1),
                }
                try:
                    await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=10)
                except Exception:
                    pass
    except Exception as e:
        result = {"site": site, "task": tid, "arm": arm, "policy": policy_name,
                  "run": run_id, "status": f"crash:{type(e).__name__}:{str(e)[:80]}"}
    finally:
        ts.stop()

    if not result or result.get("status", "").startswith("crash"):
        print(f"[{site}/{tid}/{arm}/{policy_name}#{run_id}] ⚠ {result and result['status']}", flush=True)
        if result:
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result, ensure_ascii=False) + "\n")
        return result

    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"[{site}/{tid}/{arm}/{policy_name}#{run_id}] declared={result['declared_ok']} "
          f"submits={result['submit_count']} ledger={result['ledger']} "
          f"success={result['success']} ({result['duration_s']}s)", flush=True)
    return result


def load_done(out_path):
    done = set()
    if Path(out_path).exists():
        for l in open(out_path, encoding="utf-8"):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("status", "").startswith("crash"):
                continue
            done.add((r["site"], r["task"], r["arm"], r["policy"], r["run"]))
    return done


def summarize(out_path):
    rows = [json.loads(l) for l in open(out_path, encoding="utf-8") if l.strip()]
    rows = [r for r in rows if not str(r.get("status", "")).startswith("crash")]
    if not rows:
        print("(无数据)")
        return
    cells = {}
    for r in rows:
        key = (r["site"], r["task"], r["arm"], r["policy"])
        cells.setdefault(key, []).append(r)
    print(f"\n共 {len(rows)} 次运行,{len(cells)} 格(最小 n={min(len(v) for v in cells.values())})\n")
    print(f"{'格':<30}{'n':>3}{'成功率':>8}{'假成功率':>10}{'重复率':>8}")
    for key in sorted(cells):
        v = cells[key]
        n = len(v)
        tag = "/".join(key)
        print(f"{tag:<30}{n:>3}"
              f"{sum(r['success'] for r in v) / n:>8.2%}"
              f"{sum((r['declared_ok'] and not r['success']) for r in v) / n:>10.2%}"
              f"{sum(r['submit_count'] > 1 for r in v) / n:>8.2%}")


def main_cells():
    cells = []
    for site in ("shop", "content"):
        for t in TASKS[site]:
            for arm in ("ui", "runtime", "channel"):
                for p in ("N", "P"):
                    cells.append((site, t["id"], arm, p))
    return cells


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--tasks", action="store_true", help="列出任务集")
    a = ap.parse_args()

    if a.tasks:
        for t in dump_tasks():
            print(t)
        return
    if a.summary:
        summarize(OUT)
        return
    if a.pilot:
        cells = main_cells()
        for i, (site, tid, arm, p) in enumerate(cells, 1):
            for r in (1, 2):
                print(f"── 先导 {i}/{len(cells)} #{r}: {site}/{tid}/{arm}/{p} ──", flush=True)
                await run_one(site, tid, arm, p, r, OUT)
        return
    if a.full:
        cells = main_cells()
        done = load_done(OUT)
        todo = [(s, t, ar, p) for (s, t, ar, p) in cells
                if any((s, t, ar, p, r) not in done for r in range(1, N_MAIN + 1))]
        total = len(todo) * N_MAIN
        i = 0
        for site, tid, arm, p in todo:
            for r in range(1, N_MAIN + 1):
                if (site, tid, arm, p, r) in done:
                    continue
                i += 1
                print(f"── 全量 {i}/{total}: {site}/{tid}/{arm}/{p} #{r} ──", flush=True)
                await run_one(site, tid, arm, p, r, OUT)
        sha = hashlib.sha256(Path(OUT).read_bytes()).hexdigest()
        print(f"结果文件校验和 sha256={sha}", flush=True)
        return
    ap.error("需要 --pilot / --full / --summary / --tasks")


if __name__ == "__main__":
    asyncio.run(main())
