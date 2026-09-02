# -*- coding: utf-8 -*-
"""弱模型验证 harness:MCP world_* 工具的 HTTP 常驻代理(会话常驻版)

弱模型 executor 每步只需:
  curl http://127.0.0.1:PORT/call -d '{"tool":"...","args":{...}}'
一次 world_open,页面状态跨调用存活;内部一个事件循环线程持有
stdio_client + ClientSession,所有调用 run_coroutine_threadsafe 投递。

用法:
  python world_proxy.py --url <url> [--port 8787] [--wait_ms 2500]
响应: {"ok":true,"data":...} 或 {"ok":false,"error":"..."}
"""
import argparse
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PY = str(Path(__file__).resolve().parent / "server.py")

STATE = {"loop": None, "session": None, "wid": None, "boot_url": None, "boot_wait": 2500}


async def _boot():
    """在事件循环中常驻启动 stdio client + session,并打开 world(整个进程生命周期复用)"""
    boot_url = STATE["boot_url"]
    params = StdioServerParameters(command=sys.executable, args=[SERVER_PY])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            STATE["session"] = session
            res = await asyncio.wait_for(
                session.call_tool("world_open", {"url": boot_url, "wait_ms": STATE["boot_wait"]}),
                timeout=120,
            )
            data = json.loads(res.content[0].text)
            STATE["wid"] = data["world_id"]
            print(f"[world_proxy] world #{STATE['wid']} opened: {data.get('url', '')[:80]}", flush=True)
            # 长驻:保持 session 存活直到进程结束
            await asyncio.Event().wait()


def _run_loop():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    STATE["loop"] = loop
    loop.run_until_complete(_boot())


def call_tool_sync(tool, args):
    """跨线程投递工具调用到事件循环(串行)"""
    without_wid = ("world_open", "world_list", "world_close")
    if "world_id" not in args and STATE["wid"] is not None and tool not in without_wid:
        args = dict(args)
        args["world_id"] = STATE["wid"]

    async def _do():
        session = STATE["session"]
        r = await asyncio.wait_for(session.call_tool(tool, args), timeout=120)
        return json.loads(r.content[0].text)

    fut = asyncio.run_coroutine_threadsafe(_do(), STATE["loop"])
    return fut.result(timeout=130)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln).decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception as e:
            self._resp({"ok": False, "error": f"JSON 解析失败: {e}"})
            return
        tool = body.get("tool")
        args = body.get("args") or {}
        if not tool:
            self._resp({"ok": False, "error": "缺少 tool"})
            return
        try:
            result = call_tool_sync(tool, args)
            self._resp({"ok": True, "data": result})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._resp({"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"})

    def do_GET(self):
        self._resp({"ok": True, "world_id": STATE["wid"], "alive": STATE["session"] is not None,
                    "tools": ["world_entities", "world_entity", "world_map", "world_state",
                              "world_changes", "world_change_digest", "world_evidence", "world_guide",
                              "world_resolve", "world_fill", "world_click", "world_press",
                              "world_wait", "world_screenshot", "world_close", "world_list"]})

    def _resp(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--wait_ms", type=int, default=2500)
    a = ap.parse_args()
    STATE["boot_url"] = a.url
    STATE["boot_wait"] = a.wait_ms
    t = threading.Thread(target=_run_loop, daemon=True)
    t.start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"[world_proxy] listening on 127.0.0.1:{a.port} url={a.url}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()