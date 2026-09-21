# -*- coding: utf-8 -*-
"""LLM 试验台:让真实模型经极简 CLI 操作页面,测量"读法(信道)"对判定的影响。

一次试验 = 一个独立目录(--state),内部自包含:
  - 页面服务器(serve fixtures 目录,POST ?simulate=422/500/202 注入故障,ground_truth.jsonl 留痕)
  - MCP 会话(spawn mcp/server.py,world_open 建立世界)
  - 控制端口(命令经 HTTP 转发,commands.log 审计)

臂(arm)只影响 click 返回什么,动作机制完全相同:
  ui      → 只回 "(clicked)";要看页面得自己 read(仅页面可见文本)
  receipt → 返回后果卡关键字段(page_outcome/situation/why/errors)

用法:
  python llm_trial_rig.py start --arm ui --state <dir> [--fixtures <dir>]
  python llm_trial_rig.py cmd  --state <dir> read
  python llm_trial_rig.py cmd  --state <dir> click "提交注册"
  python llm_trial_rig.py cmd  --state <dir> finish
  python llm_trial_rig.py stop --state <dir>

设计约束:
  - cmd 模式零第三方依赖(纯 urllib),任意 Python 可执行;
  - start/daemon 模式需要装有 mcp+playwright 的解释器(与 server.py 相同);
  - 提示词在两个臂之间保持完全一致——实验变量只有 click 的返回内容。
"""
import argparse
import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "mcp" / "server.py"
DEFAULT_FIXTURES = ROOT / "tests" / "fixtures"
MAX_COMMANDS = 15


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def post_json(port: int, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ── 页面服务器(带故障注入与真值留痕) ─────────────────────────────


def make_page_handler(fixtures: Path, truth_path: Path):
    lock = threading.Lock()

    class PageHandler(BaseHTTPRequestHandler):
        def _send(self, code, body=b"", ctype="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _truth(self, path, status):
            with lock:
                with truth_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": now_iso(), "path": path, "status": status},
                                       ensure_ascii=False) + "\n")

        def do_GET(self):
            p = self.path.split("?")[0].lstrip("/")
            f = fixtures / p
            if f.is_file():
                self._send(200, f.read_bytes())
            else:
                self._send(404, b"not found")

        def do_POST(self):
            if "simulate=422" in self.path:
                body = json.dumps({"error": "email_taken", "message": "账号已存在"},
                                  ensure_ascii=False).encode("utf-8")
                self._truth(self.path, 422)
                self._send(422, body, "application/json; charset=utf-8")
            elif "simulate=500" in self.path:
                body = json.dumps({"error": "payment_rollback", "message": "支付失败,已回滚"},
                                  ensure_ascii=False).encode("utf-8")
                self._truth(self.path, 500)
                self._send(500, body, "application/json; charset=utf-8")
            elif "simulate=202" in self.path:
                body = json.dumps({"task_id": "t-1", "status": "queued"},
                                  ensure_ascii=False).encode("utf-8")
                self._truth(self.path, 202)
                self._send(202, body, "application/json; charset=utf-8")
            else:
                self._truth(self.path, 200)
                self._send(200, b"{}", "application/json; charset=utf-8")

        def log_message(self, fmt, *args):
            return

    return PageHandler


# ── daemon ─────────────────────────────────────────────────────


class Daemon:
    def __init__(self, arm: str, state_dir: Path, fixtures: Path,
                 control_port: int, page_port: int, page: str = "external_facts.html"):
        self.arm = arm
        self.state_dir = state_dir
        self.fixtures = fixtures
        self.control_port = control_port
        self.page_port = page_port
        self.page = page
        self.loop = asyncio.new_event_loop()
        self.session = None
        self.world_id = None
        self.cmd_count = 0
        self.count_lock = threading.Lock()
        self.stop_event = asyncio.Event()
        self.commands_log = state_dir / "commands.log"
        self.ground_truth = state_dir / "ground_truth.jsonl"

    # ---- MCP 侧 ----
    async def _call(self, name, args, timeout=90):
        r = await asyncio.wait_for(self.session.call_tool(name, args), timeout=timeout)
        return json.loads(r.content[0].text)

    @staticmethod
    def _unquote(v):
        if isinstance(v, str) and len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    async def cmd_read(self):
        r = await self._call("world_eval", {"world_id": self.world_id,
                                            "expression": "() => document.body.innerText"})
        text = self._unquote(r.get("result") or "")
        return {"ok": True, "text": (text or "(页面无文本)")[:4000]}

    async def cmd_click(self, target: str):
        f = await self._call("world_find", {"world_id": self.world_id, "q": target})
        matches = f.get("matches") or []
        if not matches:
            return {"ok": False,
                    "text": f"未找到可见文字含「{target}」的可交互元素;可先 read 查看页面。"}
        card = await self._call("world_act",
                                {"world_id": self.world_id, "kind": "click", "id": matches[0]["id"]})
        outcome = card.get("page_outcome")
        if self.arm == "ui":
            view = "(clicked)"
        else:
            errs = card.get("errors") or (card.get("situation") or {}).get("errors")
            lines = [f"page_outcome: {outcome}",
                     f"situation: {(card.get('situation') or {}).get('type')}",
                     f"why: {card.get('why')}"]
            if errs:
                lines.append("errors: " + json.dumps(errs, ensure_ascii=False)[:600])
            view = "\n".join(lines)
        return {"ok": True, "text": view, "page_outcome": outcome}

    async def cmd_finish(self):
        try:
            if self.world_id is not None:
                await self._call("world_close", {"world_id": self.world_id})
        finally:
            self.stop_event.set()
        return {"ok": True, "text": "closed"}

    async def _mcp_main(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await asyncio.wait_for(s.initialize(), timeout=60)
                self.session = s
                url = f"http://127.0.0.1:{self.page_port}/{self.page}"
                d = await self._call("world_open", {"url": url, "wait_ms": 1500}, timeout=120)
                self.world_id = d["world_id"]
                (self.state_dir / "ready.json").write_text(
                    json.dumps({"arm": self.arm, "url": url, "page_port": self.page_port},
                               ensure_ascii=False), encoding="utf-8")
                await self.stop_event.wait()

    # ---- HTTP 控制侧 ----
    def _dispatch(self, payload: dict) -> dict:
        cmd = (payload.get("cmd") or "").strip()
        target = (payload.get("target") or "").strip()
        with self.count_lock:
            if cmd in ("read", "click"):
                self.cmd_count += 1
                if self.cmd_count > MAX_COMMANDS:
                    return {"ok": False, "text": "LIMIT_REACHED: 命令数已达上限,请直接给出最终回答。"}
        if cmd == "read":
            coro = self.cmd_read()
        elif cmd == "click":
            if not target:
                return {"ok": False, "text": "用法: click \"按钮文字\""}
            coro = self.cmd_click(target)
        elif cmd in ("finish", "shutdown"):
            coro = self.cmd_finish()
        else:
            return {"ok": False, "text": f"未知命令「{cmd}」。可用: read / click \"按钮文字\" / finish"}

        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            resp = fut.result(timeout=180)
        except Exception as e:
            resp = {"ok": False, "text": f"内部错误: {type(e).__name__}: {e}"}
        with self.count_lock:
            with self.commands_log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(
                    {"t": now_iso(), "cmd": cmd, "target": target,
                     "ok": resp.get("ok"), "page_outcome": resp.get("page_outcome")},
                    ensure_ascii=False) + "\n")
        return resp

    def _http_thread(self):
        daemon = self

        class ControlHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                try:
                    resp = daemon._dispatch(json.loads(body.decode("utf-8")))
                except Exception as e:
                    resp = {"ok": False, "text": f"bad request: {e}"}
                data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                if resp.get("text") == "closed":
                    threading.Thread(target=daemon._shutdown_all, daemon=True).start()

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", self.control_port), ControlHandler)
        httpd.serve_forever()

    def _shutdown_all(self):
        time.sleep(0.3)
        import os
        os._exit(0)

    def run(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        page = ThreadingHTTPServer(
            ("127.0.0.1", self.page_port),
            make_page_handler(self.fixtures, self.ground_truth))
        threading.Thread(target=page.serve_forever, daemon=True).start()
        threading.Thread(target=self._http_thread, daemon=True).start()
        try:
            self.loop.run_until_complete(self._mcp_main())
        except Exception as e:
            (self.state_dir / "error.txt").write_text(
                f"{type(e).__name__}: {e}", encoding="utf-8")
            raise


# ── CLI ───────────────────────────────────────────────────────


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    # daemon 模式:由 start 以分离进程拉起,不走 argparse 子命令
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        kv = {}
        for i in range(2, len(sys.argv) - 1, 2):
            kv[sys.argv[i].lstrip("-").replace("-", "_")] = sys.argv[i + 1]
        d = Daemon(arm=kv["arm"], state_dir=Path(kv["state"]),
                   fixtures=Path(kv.get("fixtures", DEFAULT_FIXTURES)),
                   control_port=int(kv["control_port"]), page_port=int(kv["page_port"]),
                   page=kv.get("page", "external_facts.html"))
        d.run()
        return

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    p_start = sub.add_parser("start")
    p_start.add_argument("--arm", choices=["ui", "receipt"], required=True)
    p_start.add_argument("--state", required=True)
    p_start.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    p_start.add_argument("--page", default="external_facts.html",
                         help="要服务的 fixture 页面文件名")

    p_cmd = sub.add_parser("cmd")
    p_cmd.add_argument("--state", required=True)
    p_cmd.add_argument("action")
    p_cmd.add_argument("rest", nargs="*")

    p_stop = sub.add_parser("stop")
    p_stop.add_argument("--state", required=True)

    a = ap.parse_args()
    state = Path(a.state)

    if a.mode == "start":
        state.mkdir(parents=True, exist_ok=True)
        control_port, page_port = free_port(), free_port()
        (state / "state.json").write_text(json.dumps(
            {"control_port": control_port, "arm": a.arm}, ensure_ascii=False), encoding="utf-8")
        log = (state / "daemon.log").open("w", encoding="utf-8")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--daemon",
             "--arm", a.arm, "--state", str(state), "--fixtures", str(a.fixtures),
             "--page", a.page,
             "--control-port", str(control_port), "--page-port", str(page_port)],
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if (state / "ready.json").is_file():
                print(f"READY arm={a.arm} state={state}")
                return
            if (state / "error.txt").is_file():
                print(f"DAEMON_ERROR: {(state / 'error.txt').read_text(encoding='utf-8')}")
                raise SystemExit(2)
            time.sleep(0.5)
        print("TIMEOUT: daemon 未在 120 秒内就绪,查看 daemon.log")
        raise SystemExit(2)

    if a.mode == "cmd":
        info = json.loads((state / "state.json").read_text(encoding="utf-8"))
        payload = {"cmd": a.action}
        if a.rest:
            payload["target"] = " ".join(a.rest)
        try:
            resp = post_json(info["control_port"], payload)
        except Exception as e:
            print(f"ERROR: 无法连接试验台({type(e).__name__});会话可能已结束。")
            raise SystemExit(1)
        print(resp.get("text", ""))
        raise SystemExit(0 if resp.get("ok") else 1)

    if a.mode == "stop":
        info = json.loads((state / "state.json").read_text(encoding="utf-8"))
        try:
            post_json(info["control_port"], {"cmd": "shutdown"}, timeout=30)
            print("STOPPED")
        except Exception:
            print("已停止或不可达")


if __name__ == "__main__":
    main()
