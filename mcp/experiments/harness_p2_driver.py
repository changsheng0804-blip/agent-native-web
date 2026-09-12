# -*- coding: utf-8 -*-
"""P2 子代理 harness 驱动器:为一个盲跑子代理准备隔离的 world + 唯一观测通道。

设计要点(与 API 版口径对齐,但堵住"子代理能自己上网"的漏洞):
  1. **夹具脱敏**:剥掉 HTML/JS 注释(注释里写着"真实账本可能与显示不一致"= 泄题),
     并以中性路径 `/portal` 提供 —— 即使子代理去抓页面源码,也读不到实验设计。
  2. **真相不可达**:只提供页面本身与 `/api/export`(与 API 版同返回 202,不泄题);
     **不提供 `/api/ledger`** —— API 版里模型只有 world_act,本就碰不到后端账本,
     这里保持同等观测面(否则子代理 POST 一下就能拿到 ¥0,测试失效)。
  3. **唯一通道**:子代理通过 world_proxy 的 HTTP 端点调用 world_* 工具,
     页面信息只能从工具返回里来 —— 与 API 版 LlmAgent 的观测面一致。

用法:
  python mcp/experiments/harness_p2_driver.py --scenario ledger --group C --run 1
"""
import argparse
import atexit
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
MCP_DIR = HERE.parent
ROOT = MCP_DIR.parent
sys.path.insert(0, str(HERE))

from three_questions import (  # noqa: E402
    SCENARIOS, SYSTEM_PROMPT_C, SYSTEM_PROMPT_N,
)

PROXY = MCP_DIR / "world_proxy.py"
FIXTURES = ROOT / "tests" / "fixtures"


def sanitize(html: str) -> str:
    """剥掉 HTML 注释与整行 JS 注释(泄题风险),保留可执行代码不变。"""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    out = []
    for line in html.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(line)
    return "\n".join(out)


class Portal(BaseHTTPRequestHandler):
    """只提供脱敏页面与 /api/export;其余一律 404(尤其 /api/ledger 不暴露)。"""

    page = b""
    page_path = "/portal"

    def do_GET(self):
        if self.path.split("?")[0] == self.page_path:
            self._send(200, self.page)
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if self.path.startswith("/api/export"):
            # 与 API 版一致:202 排队中(最终失败不可观察)
            self._send(202, json.dumps({"task_id": "EXP-20481", "state": "queued"}).encode(),
                       "application/json; charset=utf-8")
        else:
            self._send(404, b"not found")

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def proxy_call(port, tool, args, timeout=120):
    body = json.dumps({"tool": tool, "args": args}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/call", data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def wait_ready(port, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                d = json.loads(r.read().decode("utf-8"))
                if d.get("alive") and d.get("world_id") is not None:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True, choices=sorted(SCENARIOS))
    ap.add_argument("--group", required=True, choices=["N", "F", "C"])
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--log", default="")
    a = ap.parse_args()

    sc = SCENARIOS[a.scenario]

    # 1. 脱敏夹具服务
    raw = (FIXTURES / sc["fixture"]).read_text(encoding="utf-8")
    Portal.page = sanitize(raw).encode("utf-8")
    fport = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", fport), Portal)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{fport}/portal"

    # 2. 常驻 MCP 代理(一个端口 = 一个隔离 world)
    pport = a.port or free_port()
    env = dict(os.environ)
    env["AGENT_WORLD_VERDICT_MODE"] = "full"
    logf = open(a.log, "w", encoding="utf-8") if a.log else subprocess.DEVNULL
    proc = subprocess.Popen([sys.executable, str(PROXY), "--url", url,
                             "--port", str(pport), "--wait_ms", "2500"],
                            stdout=logf, stderr=subprocess.STDOUT, env=env, cwd=str(MCP_DIR))

    def _cleanup():
        try:
            proc.terminate()
        except Exception:
            pass
        httpd.shutdown()
    atexit.register(_cleanup)

    if not wait_ready(pport):
        print(json.dumps({"error": "proxy 未就绪"}, ensure_ascii=False))
        return 1

    # 3. 预解析按钮 id(把定位从被测者职责里去掉,与 API 版同口径)
    r = proxy_call(pport, "world_find", {"q": sc["button"]})
    matches = (r.get("data") or {}).get("matches") or []
    bid = ""
    for m in matches:
        if m.get("semantic") == "button" or (m.get("name") or "").startswith("button."):
            bid = m["id"]
            break
    if not bid and matches:
        bid = matches[0].get("id")

    brief = {
        "scenario": a.scenario, "group": a.group, "run": a.run,
        "endpoint": f"http://127.0.0.1:{pport}/call",
        "button_id": bid,
        "question": sc["question"],
        "system_prompt": SYSTEM_PROMPT_C if a.group == "C" else SYSTEM_PROMPT_N,
        "fact": sc["fact"] if a.group == "F" else None,
    }
    print("BRIEF_JSON " + json.dumps(brief, ensure_ascii=False), flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
