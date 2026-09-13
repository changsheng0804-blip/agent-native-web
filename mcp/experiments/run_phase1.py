# -*- coding: utf-8 -*-
"""Phase 1 v2(评审修订):确定性策略 harness——A1 通知来自观测层,不是环境真相。

评审(独立模型)确认的三处硬伤,本版修复:
  F1  A2 未实现:tool_read(slow=True) 从未被调用。→ 改为臂内慢读:
      arm=="A2" 时 read 自动等待 2.5s,所有策略共享,无需逐策略传参。
  F2  A1 是环境 oracle:_apply() 直接把环境真相 emit 给策略。→ 通知只来自
      页面内 MutationObserver 观测层(归因/回滚信号/settled),环境引擎的
      orders/rollbacks 只用于记分,永不流向策略。
  F3  假成功指标混淆。→ 拆分为:duplicate(净订单>1) / fs_immediate
      (声明时区域未满) / fs_deferred(声明时已满但最终审计失败=被环境欺骗) /
      final_verified(声明∧净订单==1∧终态区域正确)。tool_declare 记录声明时刻快照。

另:固定随机种子(每格独立)、region 12×12(与 Phase 0 夹具同尺度,协议已修订)、
n=20(协议已修订)。

臂(A:反馈机制,同一环境同一策略,仅工具返回与闸门不同):
  A0 静态快照  submit 裸受理;read 即时;无窗口无通知无闸门
  A1 Runtime   submit 结果卡 + 在途闸门 + 观测层通知(applied/rolled_back/settled)
  A2 朴素补丁  = A0,但 read 前固定等 2.5s

环境(B):B0 延迟2.5s / B1 延迟+20%回滚(1.2s)+噪声2格/s(区外) / B1x 回滚窗口随机0.5-6s
策略:S1 盲重试(5连发0.5s间隔) / S2 等3s再读 / S3 读判声明(耐心5s) /
     S4 等稳再判(A1:等观测层settled;A0/A2:盲等固定5s)

用法:
  python mcp/experiments/run_phase1.py --pilot / --full / --cell S4 A1 B1 / --summary
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters          # noqa: E402
from mcp.client.stdio import stdio_client                     # noqa: E402

HERE = Path(__file__).resolve().parent
SERVER = str(HERE.parent / "server.py")
FIXTURES = HERE.parent.parent / "tests" / "fixtures"
OUT = HERE / "phase1_results.jsonl"

REGION = (11, 11, 22, 22)          # r0, c0, r1, c1(12×12 = 144 格)
TARGET = "#3498db"
BG = "#101a3d"
CORRUPT = ["#7a7a7a", "#3f7f3f", "#8f2f2f", "#c8b23a"]

MAIN_CELLS = [(p, a, e) for p in ("S1", "S2", "S3", "S4")
               for a in ("A0", "A1", "A2") for e in ("B0", "B1")]   # 24 格主格
LADDER_DELAYS = [1000, 3000, 6000, 10000]
B1X_CELLS = [("S2", "A0", "B1x"), ("S2", "A2", "B1x"), ("S4", "A0", "B1x"),
             ("S4", "A1", "B1x"), ("S2", "A1", "B1x")]

N_MAIN = 20
N_LADDER = 20
N_B1X = 20


def env_cfg(env, delay_ms=2500):
    if env == "B0":
        return {"delay_ms": delay_ms, "rollback_prob": 0.0, "rollback_ms": 1200,
                "noise_per_sec": 0, "settle_ms": 1000}
    if env == "B1":
        return {"delay_ms": delay_ms, "rollback_prob": 0.2, "rollback_ms": 1200,
                "noise_per_sec": 2, "settle_ms": 1000}
    if env == "B1x":
        return {"delay_ms": delay_ms, "rollback_prob": 0.2, "rollback_ms": "rand500_6000",
                "noise_per_sec": 2, "settle_ms": 1000}
    raise ValueError(env)


class Environment:
    """环境引擎(真相只用于记分)+ 观测层接入(通知只来自页面观测层)。"""

    def __init__(self, session, wid, cfg, arm, seed):
        self.session = session
        self.wid = wid
        self.cfg = cfg
        self.arm = arm
        self.seed = seed
        self.rng = random.Random(seed)       # 独立 RNG:回滚/噪声全部用它(可复现)
        self.orders = 0
        self.landed = 0
        self.rollbacks = 0
        self.in_flight = False
        self.declared = False
        self.declare_snapshot = None
        self.submit_ts = []
        self.events = []
        self.pending = set()
        self.resolved = {}                   # oid → settled 通知(观测层给出)
        self.observer_log = []               # 观测层通知审计副本
        self.closed = False
        self.noise_task = None
        self.watcher_task = None
        self.region = REGION

    async def _eval(self, expr):
        r = await asyncio.wait_for(
            self.session.call_tool("world_eval", {"world_id": self.wid, "expression": expr}), timeout=30)
        return json.loads(r.content[0].text)["result"]

    async def _obj(self, expr):
        v = await self._eval(expr)
        return json.loads(v) if isinstance(v, str) else v

    async def render_cells(self, updates):
        lst = json.dumps([{"r": u[0], "c": u[1], "color": u[2]} for u in updates],
                         separators=(",", ":"))
        await self._eval(f"__p1.setCells({lst})")

    def region_cells(self):
        r0, c0, r1, c1 = self.region
        return [[r, c] for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]

    # ── 观测层接入:唯一的通知源 ──
    async def start_observer_watcher(self):
        if self.arm != "A1":
            return

        async def _watch():
            while not self.closed:
                await asyncio.sleep(0.25)              # 低频轮询(与噪声/策略错开,防 MCP 过载丢事件)
                try:
                    ns = await self._obj("__p1.drainNotices()")
                except Exception:
                    continue
                for n in ns or []:
                    self.observer_log.append(n)
                    if n.get("type") == "settled":
                        oid = str(n.get("window"))
                        self.resolved[oid] = n
                        if self.in_flight and oid == str(self.orders):
                            self.in_flight = False       # 结果已由观测层确认,闸门释放
        self.watcher_task = asyncio.create_task(_watch())

    async def stop_watcher(self):
        self.closed = True
        if self.watcher_task:
            self.watcher_task.cancel()
        if self.noise_task:
            self.noise_task.cancel()

    async def wait_settled(self, oid, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if str(oid) in self.resolved:
                return self.resolved[str(oid)]
            await asyncio.sleep(0.15)
        return None

    def any_rolled_back(self, oid):
        """settled 之后观测层发出的完整性破坏通知(按窗口 id)。"""
        return any(n.get("type") == "rolled_back" and str(n.get("window")) == str(oid)
                   for n in self.observer_log)

    # ── 噪声(只在外围,不进入目标区;语义:干扰归因,不摧毁交付物)──
    async def start_noise(self):
        if not self.cfg["noise_per_sec"]:
            return

        async def _loop():
            interval = 1.0 / self.cfg["noise_per_sec"]
            while not self.closed:
                await asyncio.sleep(interval)
                r = 1 + int(self.rng.random() * 23)
                c = 1 + int(self.rng.random() * 38)
                if self.region[0] <= r <= self.region[2] and self.region[1] <= c <= self.region[3]:
                    continue
                bad = CORRUPT[int(self.rng.random() * len(CORRUPT))]
                try:
                    await self.render_cells([[r, c, bad]])
                except Exception:
                    pass
        self.noise_task = asyncio.create_task(_loop())

    # ── 动作通道 ──
    async def submit(self):
        self.submit_ts.append(time.time())
        if self.arm == "A1" and self.in_flight:
            return {"allowed": False, "reason": "in-flight",
                    "note": "在途锁生效:上一笔订单结果未定,本次提交被拒绝,未产生新订单。"}
        self.orders += 1
        oid = self.orders
        self.events.append({"t": time.time(), "type": "submitted", "order": oid})
        if self.arm == "A1":
            self.in_flight = True
            # 开观测窗口(只声明"在等这个效果",不携带任何真相)
            await self._eval(f'__p1.openWindow("{oid}", "{TARGET}", '
                             f'{self.region[0]}, {self.region[1]}, {self.region[2]}, {self.region[3]})')
        task = asyncio.create_task(self._apply(oid))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        if self.arm == "A1":
            return {"allowed": True, "order_id": oid, "status": "in-flight",
                    "note": "订单已受理,结果未定。稳定报告将由观测层推送。"}
        return {"order_id": oid, "accepted": True}

    def _rollback_delay(self):
        v = self.cfg["rollback_ms"]
        if isinstance(v, str) and v.startswith("rand"):
            lo, hi = v[4:].split("_")
            return int(lo) + int(self.rng.random() * (int(hi) - int(lo)))
        return v

    async def _apply(self, oid):
        await asyncio.sleep(self.cfg["delay_ms"] / 1000)
        await self.render_cells([[r, c, TARGET] for r, c in self.region_cells()])
        self.landed += 1
        self.events.append({"t": time.time(), "type": "applied", "order": oid})
        if self.rng.random() < self.cfg["rollback_prob"]:
            await asyncio.sleep(self._rollback_delay() / 1000)
            await self.render_cells([[r, c, BG] for r, c in self.region_cells()])
            self.rollbacks += 1
            self.events.append({"t": time.time(), "type": "rolled_back", "order": oid})
        # 无任何 emit——settled 由观测层静默窗给出,闸门释放由 watcher 完成

    async def read_snapshot(self):
        r0, c0, r1, c1 = self.region
        return await self._obj(f"__p1.snapshot({r0},{c0},{r1},{c1},\"{TARGET}\")")

    async def tool_submit(self):
        return json.dumps(await self.submit(), ensure_ascii=False)

    async def tool_read(self):
        if self.arm == "A2":
            await asyncio.sleep(2.5)            # A2 朴素补丁:臂内慢读(评审 F1 修复)
        return json.dumps(await self.read_snapshot(), ensure_ascii=False)

    async def tool_declare(self):
        self.declared = True
        self.declare_snapshot = await self.read_snapshot()   # 声明时刻快照(评审 F3 修复)
        return json.dumps({"recorded": True,
                           "note": "已记录完成声明与当时画面状态。"}, ensure_ascii=False)

    async def verify(self):
        while self.pending:
            await asyncio.gather(*list(self.pending))
        await asyncio.sleep(1.0)                # 终态稳定窗(最终审计)
        snap = await self.read_snapshot()
        net = self.orders - self.rollbacks
        region_ok = snap["filled"] == snap["total"] and snap["other"] == 0
        declare_filled = bool(self.declare_snapshot
                              and self.declare_snapshot["filled"] == self.declare_snapshot["total"])
        final_verified = bool(self.declared and net == 1 and region_ok)
        return {
            "orders": self.orders, "rollbacks": self.rollbacks, "net_orders": net,
            "duplicate": net > 1,
            "declare_filled": declare_filled,
            "final_verified": final_verified,
            "fs_immediate": bool(self.declared and not declare_filled),
            "fs_deferred": bool(self.declared and declare_filled and not final_verified),
            "rollback_detected": sum(1 for n in self.observer_log
                                     if n.get("type") == "rolled_back"
                                     or (n.get("type") == "settled" and n.get("rolled_back"))),
            "region_ok": region_ok, "landed": self.landed,
        }


# ===== 确定性策略(决策者耐心档位的下界)=====
async def policy_S1(env):
    for _ in range(5):                                   # 连续盲重试(全部落在在途窗口内)
        await env.tool_submit()
        await asyncio.sleep(0.5)
    await asyncio.sleep(1.0)
    await env.tool_declare()


async def policy_S2(env):
    for _ in range(3):
        await env.tool_submit()
        await asyncio.sleep(3.0)
        snap = json.loads(await env.tool_read())
        if snap["filled"] == snap["total"]:
            await env.tool_declare()
            return
    await env.tool_declare()


async def policy_S3(env):
    submits = 0
    last_submit = 0.0
    t0 = time.time()
    while True:
        if submits == 0:
            await env.tool_submit()
            submits = 1
            last_submit = time.time()
        snap = json.loads(await env.tool_read())
        if snap["filled"] == snap["total"]:
            await env.tool_declare()
            return
        if time.time() - last_submit >= 5.0 and submits < 3:      # 耐心=5s
            await env.tool_submit()
            submits += 1
            last_submit = time.time()
        if time.time() - t0 > 40:
            await env.tool_declare()
            return


async def policy_S4(env):
    """同一谨慎意图:A1 等观测层 settled 信号 + 确认窗;A0/A2 只能盲等固定时长。"""
    CONFIRM_MS = 1.5
    for _ in range(3):
        await env.tool_submit()
        if env.arm == "A1":
            n = await env.wait_settled(env.orders, timeout=15)
            if n is None:
                await env.tool_declare()
                return
            rolled = bool(n.get("rolled_back"))
            t_end = time.time() + CONFIRM_MS          # 确认窗:settled 后再观察,期间完整性破坏则补提交
            while time.time() < t_end and not rolled:
                await asyncio.sleep(0.15)
                rolled = env.any_rolled_back(env.orders)
        else:
            await asyncio.sleep(5.0)
            rolled = False
        snap = json.loads(await env.tool_read())
        if snap["filled"] == snap["total"] and not rolled:
            await env.tool_declare()
            return
    await env.tool_declare()


POLICIES = {"S1": policy_S1, "S2": policy_S2, "S3": policy_S3, "S4": policy_S4}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].lstrip("/")
        f = FIXTURES / path
        if f.is_file():
            self._send(200, f.read_bytes())
        else:
            self._send(404, b"not found")

    def log_message(self, *a):
        return


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def run_one(policy, arm, env_name, run_id, out_path, delay_ms=2500):
    cfg = env_cfg(env_name, delay_ms)
    seed = f"{policy}-{arm}-{env_name}-{delay_ms}-{run_id}"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/phase1_canvas.html"

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    result = None
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                d = await call(session, "world_open", {"url": url, "wait_ms": 600})
                wid = d["world_id"]

                env = Environment(session, wid, cfg, arm, seed)
                await env.start_noise()
                await env.start_observer_watcher()
                t0 = time.time()
                try:
                    await asyncio.wait_for(POLICIES[policy](env), timeout=60)
                except asyncio.TimeoutError:
                    pass
                await env.stop_watcher()
                verify = await env.verify()
                patience = ([round(env.submit_ts[i + 1] - env.submit_ts[i], 2)
                             for i in range(len(env.submit_ts) - 1)])
                result = {
                    "policy": policy, "arm": arm, "env": env_name,
                    "delay_ms": delay_ms, "run": run_id, "seed": seed,
                    "orders": verify["orders"], "rollbacks": verify["rollbacks"],
                    "net_orders": verify["net_orders"], "duplicate": verify["duplicate"],
                    "declare_filled": verify["declare_filled"],
                    "final_verified": verify["final_verified"],
                    "fs_immediate": verify["fs_immediate"],
                    "fs_deferred": verify["fs_deferred"],
                    "rollback_detected": verify["rollback_detected"],
                    "region_ok": verify["region_ok"], "patience_s": patience,
                    "steps": len(env.events),
                    "duration_s": round(time.time() - t0, 1),
                    "events": env.events,
                }
                await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
    finally:
        httpd.shutdown()

    with open(out_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    tag = f"[{policy}/{arm}/{env_name}{('@' + str(delay_ms)) if delay_ms != 2500 else ''}#{run_id}]"
    print(f"{tag} orders={result['orders']} net={result['net_orders']} "
          f"dup={result['duplicate']} decl_filled={result['declare_filled']} "
          f"fs_i={result['fs_immediate']} fs_d={result['fs_deferred']} "
          f"verified={result['final_verified']} rollDet={result['rollback_detected']}"
          f"({result['duration_s']}s)", flush=True)
    return result


def summarize(out_path):
    rows = [json.loads(l) for l in open(out_path, encoding="utf-8") if l.strip()]
    if not rows:
        print("(无数据)")
        return
    cells = {}
    for r in rows:
        key = (r["policy"], r["arm"], r["env"], r.get("delay_ms", 2500))
        cells.setdefault(key, []).append(r)
    print(f"\n共 {len(rows)} 次运行,{len(cells)} 个格(最小 n={min(len(v) for v in cells.values())})\n")
    hdr = f"{'格':<24}{'n':>3}{'重复率':>8}{'fs即时':>8}{'fs延迟':>8}{'最终验证':>8}{'回滚检出':>8}"
    print(hdr)
    for key in sorted(cells, key=lambda k: (k[2], k[0], k[1])):
        v = cells[key]
        n = len(v)
        tag = f"{key[0]}/{key[1]}/{key[2]}" + (f"@{key[3]}ms" if key[3] != 2500 else "")
        print(f"{tag:<24}{n:>3}"
              f"{sum(r['duplicate'] for r in v) / n:>8.2%}"
              f"{sum(r['fs_immediate'] for r in v) / n:>8.2%}"
              f"{sum(r['fs_deferred'] for r in v) / n:>8.2%}"
              f"{sum(r['final_verified'] for r in v) / n:>8.2%}"
              f"{sum(r['rollback_detected'] for r in v):>6}/{sum(r['rollbacks'] for r in v)}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--cell", nargs=3, metavar=("POLICY", "ARM", "ENV"))
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--delay", type=int, default=2500)
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()

    if a.summary:
        summarize(OUT)
        return
    if a.pilot:
        cells = [(p, arm, env, 2500) for (p, arm, env) in MAIN_CELLS] + \
                [("S3", "A0", "B0", d) for d in LADDER_DELAYS] + \
                [(p, arm, env, 2500) for (p, arm, env) in B1X_CELLS]
        for i, (p, arm, env, d) in enumerate(cells, 1):
            for r in (1, 2):
                print(f"── 先导 {i}/{len(cells)} 第{r}次: {p}/{arm}/{env}@{d} ──", flush=True)
                await run_one(p, arm, env, r, OUT, delay_ms=d)
        return
    if a.full:
        cells = [(p, arm, env, 2500, N_MAIN) for (p, arm, env) in MAIN_CELLS] + \
                [("S3", "A0", "B0", d, N_LADDER) for d in LADDER_DELAYS] + \
                [(p, arm, env, 2500, N_B1X) for (p, arm, env) in B1X_CELLS]
        total = sum(n for *_, n in cells)
        i = 0
        for p, arm, env, d, n in cells:
            for r in range(1, n + 1):
                i += 1
                print(f"── 全量 {i}/{total}: {p}/{arm}/{env}@{d} #{r} ──", flush=True)
                await run_one(p, arm, env, r, OUT, delay_ms=d)
        # 冻结数据审计:文件校验和
        sha = hashlib.sha256(Path(OUT).read_bytes()).hexdigest()
        print(f"结果文件校验和 sha256={sha}", flush=True)
        return
    if not a.cell:
        ap.error("需要 --pilot / --full / --cell / --summary")
    p, arm, env = a.cell
    await run_one(p, arm, env, a.run, OUT, delay_ms=a.delay)


if __name__ == "__main__":
    asyncio.run(main())
