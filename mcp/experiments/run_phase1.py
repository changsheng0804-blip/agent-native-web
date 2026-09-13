# -*- coding: utf-8 -*-
"""Phase 1(修订 v1.1):确定性策略 harness——被试是脚本策略,不是 LLM。

协议修订记录 v1.1(2026-09-13):原 LLM 行为实验降级为策略 harness,理由:
  1) 触发预注册红线 #3(LLM 行为主导 → 实验退化为模型能力评估);
  2) 行为结论模型相关,违背 B3"不依赖特定模型"原则;
  3) 零 LLM 成本、大样本、完全可复现。
其余因素(臂/环境/判据)不变。

臂(A:反馈机制,同一环境同一策略,仅工具返回与通知不同):
  A0 静态快照  submit 裸受理;read_region 即时读;无闸门无通知
  A1 Runtime   submit 结果卡(in-flight);在途闸门;通知 applied/rolled_back/settled
  A2 朴素补丁  = A0,但 read_region 前固定等待 2.5s(工程补丁,红线 #1 裁决对象)

环境(B):
  B0  单纯延迟 delay_ms
  B1  延迟 2.5s + 20% 概率回滚(申请后 rollback_ms=1.2s 逆转) + 噪声 2 格/s(目标区外)
  B1x 延迟 2.5s + 20% 概率回滚(rollback_ms 随机 0.5-6s)——固定等待无法覆盖未知窗口
  B0<d> 梯子:仅延迟,delay_ms ∈ {1000,3000,6000,10000}

策略(确定性,模拟"决策者耐心档位"的下界):
  S1 盲重试    提交×5(间隔 1s)→ 声明(不读画面,对应"模型不主动用工具"的下界)
  S2 等N再查   提交→等 3s→读→满则声明,否则再提交(最多 3 次)
  S3 读判声明  提交→每 1s 读一次→满则声明;5s 未满则补提交(耐心=5s,最多 3 次)
  S4 等稳再判  A1:等 settled 通知→读→回滚则补提交;A0/A2:等固定 5s→读→同样判断
               (同一"谨慎意图":有 Runtime 拿信号,无 Runtime 只能盲等)

语义(与 §7.15 对齐):每次 submit = 一笔订单(副作用在提交时即发生);
净订单 = 提交数 - 回滚数;净订单 > 1 = 重复下单;declared ∧ ¬verified = 假成功。

用法:
  python mcp/experiments/run_phase1.py --pilot          # 先导:主格各 2 次
  python mcp/experiments/run_phase1.py --full           # 全量:主格 n=20 + 梯子 + B1x
  python mcp/experiments/run_phase1.py --cell S3 A0 B0  # 单格
  python mcp/experiments/run_phase1.py --summary        # 汇总 phase1_results.jsonl
"""
import argparse
import asyncio
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

MAIN_CELLS = [("S1", a, e) for a in ("A0", "A1", "A2") for e in ("B0", "B1")]
LADDER_DELAYS = [1000, 3000, 6000, 10000]
B1X_CELLS = [("S2", "A0", "B1x"), ("S2", "A2", "B1x"), ("S4", "A0", "B1x"),
             ("S4", "A1", "B1x"), ("S2", "A1", "B1x")]
S4_CELLS = [("S4", a, e) for a in ("A0", "A1", "A2") for e in ("B0", "B1")]

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
    """环境引擎(真相持有者):延迟/回滚/噪声/订单计数全在 harness 侧,页面是哑渲染器。"""

    def __init__(self, session, wid, cfg, arm):
        self.session = session
        self.wid = wid
        self.cfg = cfg
        self.arm = arm
        self.orders = 0
        self.landed = 0
        self.rollbacks = 0
        self.in_flight = False
        self.declared = False
        self.submit_ts = []
        self.events = []
        self.notices = []
        self.pending = set()
        self.noise_on = False
        self.noise_task = None
        self.region = REGION

    def emit(self, notice):
        if self.arm == "A1":
            self.notices.append(notice)

    def drain_notices(self):
        n = list(self.notices)
        self.notices.clear()
        return n

    async def _eval(self, expr):
        r = await asyncio.wait_for(
            self.session.call_tool("world_eval", {"world_id": self.wid, "expression": expr}), timeout=30)
        return json.loads(r.content[0].text)["result"]

    async def render_cells(self, updates):
        lst = json.dumps([{"r": u[0], "c": u[1], "color": u[2]} for u in updates],
                         separators=(",", ":"))
        await self._eval(f"__p1.setCells({lst})")

    def region_cells(self):
        r0, c0, r1, c1 = self.region
        return [[r, c] for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]

    async def start_noise(self):
        if not self.cfg["noise_per_sec"]:
            return
        self.noise_on = True

        async def _loop():
            interval = 1.0 / self.cfg["noise_per_sec"]
            while self.noise_on:
                await asyncio.sleep(interval)
                r = 1 + int(random.random() * 23)
                c = 1 + int(random.random() * 38)
                if self.region[0] <= r <= self.region[2] and self.region[1] <= c <= self.region[3]:
                    continue
                bad = CORRUPT[int(random.random() * len(CORRUPT))]
                try:
                    await self.render_cells([[r, c, bad]])
                except Exception:
                    pass
        self.noise_task = asyncio.create_task(_loop())

    async def stop_noise(self):
        self.noise_on = False
        if self.noise_task:
            self.noise_task.cancel()

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
        task = asyncio.create_task(self._apply(oid))
        self.pending.add(task)
        task.add_done_callback(self.pending.discard)
        if self.arm == "A1":
            return {"allowed": True, "order_id": oid, "status": "in-flight",
                    "note": "订单已受理,结果未定。稳定报告将随后推送。"}
        return {"order_id": oid, "accepted": True}

    def _rollback_delay(self):
        v = self.cfg["rollback_ms"]
        if isinstance(v, str) and v.startswith("rand"):
            lo, hi = v[4:].split("_")
            return int(lo) + int(random.random() * (int(hi) - int(lo)))
        return v

    async def _apply(self, oid):
        await asyncio.sleep(self.cfg["delay_ms"] / 1000)
        await self.render_cells([[r, c, TARGET] for r, c in self.region_cells()])
        self.landed += 1
        self.events.append({"t": time.time(), "type": "applied", "order": oid})
        self.emit({"type": "applied", "order": oid})
        rolled = False
        if random.random() < self.cfg["rollback_prob"]:
            await asyncio.sleep(self._rollback_delay() / 1000)
            await self.render_cells([[r, c, BG] for r, c in self.region_cells()])
            self.rollbacks += 1
            rolled = True
            self.events.append({"t": time.time(), "type": "rolled_back", "order": oid})
            self.emit({"type": "rolled_back", "order": oid})
        await asyncio.sleep(self.cfg["settle_ms"] / 1000)
        if self.arm == "A1" and self.in_flight:
            self.in_flight = False
            self.emit({"type": "settled", "order": oid, "rolled_back": rolled})

    async def read_snapshot(self):
        r0, c0, r1, c1 = self.region
        snap = await self._eval(f"__p1.snapshot({r0},{c0},{r1},{c1},\"{TARGET}\")")
        if isinstance(snap, str):
            snap = json.loads(snap)
        return snap

    async def tool_submit(self):
        return json.dumps(await self.submit(), ensure_ascii=False)

    async def tool_read(self, slow=False):
        if slow:
            await asyncio.sleep(2.5)
        return json.dumps(await self.read_snapshot(), ensure_ascii=False)

    async def tool_declare(self):
        self.declared = True
        return json.dumps({"recorded": True}, ensure_ascii=False)

    async def verify(self):
        while self.pending:
            await asyncio.gather(*list(self.pending))
        await asyncio.sleep(1.0)
        snap = await self.read_snapshot()
        net = self.orders - self.rollbacks
        region_ok = snap["filled"] == snap["total"] and snap["other"] == 0
        verified = bool(self.declared and net == 1 and region_ok)
        return {"orders": self.orders, "rollbacks": self.rollbacks, "net_orders": net,
                "region_ok": region_ok, "landed": self.landed,
                "verified_success": verified, "duplicate": net > 1}


# ===== 确定性策略(决策者耐心档位的下界)=====
async def policy_S1(env):
    for _ in range(5):                                   # 连续盲重试(§7.15 同款:全部落在在途窗口内)
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
        if time.time() - t0 > 40:                                  # 兜底
            await env.tool_declare()
            return


async def policy_S4(env):
    """同一谨慎意图:有 Runtime 拿 settled 信号;无 Runtime 只能盲等固定时长。"""
    for _ in range(3):
        await env.tool_submit()
        if env.arm == "A1":
            waited = 0.0
            settled = None
            while waited < 15.0:
                await asyncio.sleep(0.2)
                waited += 0.2
                for n in env.drain_notices():
                    if n.get("type") == "settled":
                        settled = n
                if settled:
                    break
            if not settled:
                await env.tool_declare()
                return
            rolled = settled.get("rolled_back", False)
        else:
            await asyncio.sleep(5.0)                               # 盲等固定 5s
            rolled = False
        snap = json.loads(await env.tool_read())
        if snap["filled"] == snap["total"] and not rolled:
            await env.tool_declare()
            return
        # 未生效或被回滚 → 补提交(下一轮循环)
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

                env = Environment(session, wid, cfg, arm)
                await env.start_noise()
                t0 = time.time()
                try:
                    await asyncio.wait_for(POLICIES[policy](env), timeout=60)
                except asyncio.TimeoutError:
                    pass
                await env.stop_noise()
                verify = await env.verify()
                patience = ([round(env.submit_ts[i + 1] - env.submit_ts[i], 2)
                             for i in range(len(env.submit_ts) - 1)])
                result = {
                    "policy": policy, "arm": arm, "env": env_name,
                    "delay_ms": delay_ms, "run": run_id,
                    "orders": verify["orders"], "rollbacks": verify["rollbacks"],
                    "net_orders": verify["net_orders"], "duplicate": verify["duplicate"],
                    "declared": env.declared, "verified_success": verify["verified_success"],
                    "false_success": bool(env.declared and not verify["verified_success"]),
                    "region_ok": verify["region_ok"], "patience_s": patience,
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
          f"dup={result['duplicate']} declared={result['declared']} "
          f"verified={result['verified_success']} fs={result['false_success']} "
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
    print(f"{'格':<24}{'n':>3}{'订单均':>6}{'回滚均':>6}{'重复率':>8}{'假成功率':>10}{'声明率':>8}{'验证率':>8}")
    for key in sorted(cells, key=lambda k: (k[2], k[0], k[1])):
        v = cells[key]
        n = len(v)
        tag = f"{key[0]}/{key[1]}/{key[2]}" + (f"@{key[3]}ms" if key[3] != 2500 else "")
        print(f"{tag:<24}{n:>3}{sum(r['orders'] for r in v) / n:>6.2f}"
              f"{sum(r['rollbacks'] for r in v) / n:>6.2f}"
              f"{sum(r['duplicate'] for r in v) / n:>8.2%}"
              f"{sum(r['false_success'] for r in v) / n:>10.2%}"
              f"{sum(r['declared'] for r in v) / n:>8.2%}"
              f"{sum(r['verified_success'] for r in v) / n:>8.2%}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="先导:主格 18 + 梯子 4 + B1x 5,各 2 次")
    ap.add_argument("--full", action="store_true", help="全量:n=20 × 全部格")
    ap.add_argument("--cell", nargs=3, metavar=("POLICY", "ARM", "ENV"),
                    help="单格,如: S3 A0 B0")
    ap.add_argument("--run", type=int, default=1)
    ap.add_argument("--delay", type=int, default=2500, help="梯子用:延迟 ms")
    ap.add_argument("--summary", action="store_true")
    a = ap.parse_args()

    if a.summary:
        summarize(a.out if hasattr(a, "out") else OUT)
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
        return
    if not a.cell:
        ap.error("需要 --pilot / --full / --cell / --summary")
    p, arm, env = a.cell
    await run_one(p, arm, env, a.run, OUT, delay_ms=a.delay)


if __name__ == "__main__":
    asyncio.run(main())
