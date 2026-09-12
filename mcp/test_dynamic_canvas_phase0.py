# -*- coding: utf-8 -*-
"""Phase 0 机制验证:动态画布观测层三原语(归因 / settled / 在途闸门)。

预注册协议:docs/实验方案-动态画布实时反馈预注册.md(判据 P0.1-P0.5,不得事后修改)
夹具:tests/fixtures/dynamic_canvas.html(无外部依赖,离线可跑,自驱动场景)
 ground truth = 注入器自身的变更日志(每次 DOM 变更都盖 cause 章),
 观察者 = MutationObserver 事件驱动(无轮询,无漏计)。

判据(来自协议 §1,失败即 Phase 0 不通过):
  P0.1 归因准确率 = 100%(观察者分类与注入器 ground truth 逐条一致,总数 ≥ 280)
  P0.2 settled 误报 = 0,延迟 ≤ 1200ms,次数 ≥ 3
  P0.3 闸门:10 次盲重试 → 实际生效恰 1 次(其余 9 次被在途锁拦截)
  P0.4 观测开销:单批处理峰值 ≤ 30ms(事件驱动,无全量轮询)
  P0.5 保真:乐观回滚 20/20 检出;F4 丢弃 18 格后观察者所见 = 实际生效 42 格

通过标准:全部 ✅(Phase 0 通过后才允许开跑协议 Phase 1/2)。
"""
import asyncio
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

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

    def log_message(self, fmt, *args):
        return


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


def as_obj(v):
    """world_eval 的 result 可能是对象,也可能是 JSON 字符串——统一成对象。"""
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {"text": v}
    return v


async def main():
    # 本地 HTTP 服务(与 test_external_facts 同模式;file:// 下 MutationObserver 行为有差异)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/dynamic_canvas.html"

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                d = await call(session, "world_open", {"url": url, "wait_ms": 800})
                wid = d["world_id"]

                r = await call(session, "world_eval",
                               {"world_id": wid, "expression": "window.__dc.startPhase0()"})
                started = as_obj(r["result"])
                check("场景开机", started.get("started") is True, str(started))

                deadline = time.time() + 45
                done = False
                while time.time() < deadline:
                    r = await call(session, "world_eval",
                                   {"world_id": wid, "expression": "window.__dc.done"})
                    done = as_obj(r["result"]) is True
                    if done:
                        break
                    await asyncio.sleep(0.5)
                check("场景在 45s 时限内完成", done)

                r = await call(session, "world_eval",
                               {"world_id": wid, "expression": "window.__dc.report"})
                rep = as_obj(r["result"])

                if not done or not isinstance(rep, dict) or rep.get("error"):
                    check("报告可用", False, str(rep)[:200])
                else:
                    att = rep["attribution"]
                    print(f"\n  [report] 归因 {att['matched']}/{att['total']} (acc={att['accuracy']}) · "
                          f"settled={rep['settled']['count']} (fp={rep['settled']['falsePositives']}, "
                          f"max={rep['settled']['maxLatencyMs']}ms) · 闸门 {rep['gate']['applied']}/{rep['gate']['attempts']} · "
                          f"回滚 {rep['rollback']['detected']}/{rep['rollback']['injected']} · "
                          f"F4 {rep['loss']['applied']}/{rep['loss']['injected']} · "
                          f"观测开销 max={rep['cost']['maxMs']}ms\n")

                    # P0.1 归因准确率 = 100%
                    check("P0.1 归因准确率 = 100%",
                          att["accuracy"] == 1.0 and att["total"] >= 280,
                          f"accuracy={att['accuracy']} total={att['total']} inj={att['injCount']} debug={json.dumps(rep.get('debug'), ensure_ascii=False)}")
                    # P0.2 settled
                    check("P0.2 settled 误报 = 0", rep["settled"]["falsePositives"] == 0)
                    check("P0.2 settled 延迟 ≤ 1200ms",
                          rep["settled"]["maxLatencyMs"] <= 1200,
                          f"max={rep['settled']['maxLatencyMs']}ms")
                    check("P0.2 settled 次数 ≥ 3", rep["settled"]["count"] >= 3,
                          f"count={rep['settled']['count']}")
                    # P0.3 闸门
                    g = rep["gate"]
                    check("P0.3 闸门:10 次盲重试 → 生效恰 1 次",
                          g["attempts"] == 10 and g["applied"] == 1 and g["blocked"] == 9, str(g))
                    # P0.4 观测开销
                    check("P0.4 观测开销峰值 ≤ 30ms", rep["cost"]["maxMs"] <= 30,
                          f"max={rep['cost']['maxMs']}ms")
                    # P0.5 保真
                    rb = rep["rollback"]
                    check("P0.5 乐观回滚 20/20 检出",
                          rb["injected"] == 20 and rb["detected"] == 20, str(rb))
                    ls = rep["loss"]
                    check("P0.5 F4:丢弃 18 格,观察者所见 = 实际生效 42 格",
                          ls["injected"] == 60 and ls["dropped"] == 18
                          and ls["applied"] == 42 and ls["observedAgentChanges"] == 42, str(ls))

                await asyncio.wait_for(session.call_tool("world_close", {"world_id": wid}), timeout=15)
    finally:
        httpd.shutdown()

    print(f"\nPhase 0 结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
