# -*- coding: utf-8 -*-
"""测试网络与控制台静默失败监听 (借鉴 Chrome DevTools MCP)。

覆盖:
  1. 点击按钮触发接口 422 报错但 DOM 无变化 → page_outcome 从 unchanged 自动翻转为 errored,
     situation.type = network_error, why 包含真实接口状态码与报错 detail。
  2. 点击按钮触发前端 console.error 但 DOM 无变化 → page_outcome 自动归因为 errored,
     situation.type = console_error, why 包含控制台错误文本。
  3. 正常修改 DOM 的动作不受影响 → 正常 progressed。
"""
import asyncio
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "silent_failure.html"

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

async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)

class MockHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FIXTURE_PATH.read_bytes())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/submit-account":
            self.send_response(422)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "code": "USERNAME_RESERVED",
                "message": "该用户名已被系统保留，请更换其他名字"
            }, ensure_ascii=False).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

async def main():
    # 1. 启动本地 Mock 服务
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}/"

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)

            # 2. 打开测试页面
            w = await call(session, "world_open", {"url": url, "wait_ms": 500})
            wid = w["world_id"]
            check("world_open ready", w.get("ready") is True)

            # 3. 测试接口 422 报错但 DOM 无变化
            card_net = await call(session, "world_act", {
                "world_id": wid,
                "kind": "click",
                "id": "btn-api-fail"
            })
            check("接口报错时 page_outcome 为 errored", card_net.get("page_outcome") == "errored", f"got {card_net.get('page_outcome')}")
            check("situation.type 为 network_error", card_net.get("situation", {}).get("type") == "network_error", f"got {card_net.get('situation')}")
            check("why 包含 422 状态码", "422" in (card_net.get("why") or ""), f"got why: {card_net.get('why')}")
            check("why 包含真实业务错误详情", "系统保留" in (card_net.get("why") or ""), f"got why: {card_net.get('why')}")
            check("next.suggested 包含接口报错指导", "接口报错" in (card_net.get("next", {}).get("suggested") or ""), f"got: {card_net.get('next')}")

            # 4. 测试前端 console.error 报错但 DOM 无变化
            card_con = await call(session, "world_act", {
                "world_id": wid,
                "kind": "click",
                "id": "btn-console-fail"
            })
            check("控制台报错时 page_outcome 为 errored", card_con.get("page_outcome") == "errored", f"got {card_con.get('page_outcome')}")
            check("situation.type 为 console_error", card_con.get("situation", {}).get("type") == "console_error", f"got {card_con.get('situation')}")
            check("why 包含控制台报错文本", "前端校验拦截" in (card_con.get("why") or ""), f"got why: {card_con.get('why')}")

            # 5. 测试正常修改 DOM 的成功动作
            card_ok = await call(session, "world_act", {
                "world_id": wid,
                "kind": "click",
                "id": "btn-success"
            })
            check("正常动作时 page_outcome 为 progressed", card_ok.get("page_outcome") == "progressed", f"got {card_ok.get('page_outcome')}")

            # 6. 关闭世界
            await call(session, "world_close", {"world_id": wid})

    print(f"\nTotal: {PASS + FAIL}, Passed: {PASS}, Failed: {FAIL}")
    if FAIL > 0:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
