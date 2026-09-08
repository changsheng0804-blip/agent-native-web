# -*- coding: utf-8 -*-
"""A6 基准:外部事实五反例(核心命题 §2.2)——固化"当前判不出",作为 Runtime 验收基准。

五个场景的共同点:**成功信号不在任何观察里**,模型再强也推不出真相。
本测试的价值不是"现在能过",而是:
  1. 把五类缺失事实固化成可跑的用例;
  2. 记录当前判定结果作为基线;
  3. 后续每补一类 Runtime 职责(证据/权威状态/因果/来源身份),对应用例应"点亮"
     (从假成功变为 unchanged/uncertain/errored),届时同步更新此基线。

**为什么用本地 HTTP 服务而不是 file://**:反例①需要真实的 HTTP 422。
file:// 下的 XHR 会以 net::ERR_FAILED(status=0)失败,那是传输错误而非后端 422——
用它验证会"因错误的原因通过",是假阳性。

判定标准(当前阶段):任何反例都不得被判成 progressed(假成功红线)。
"""
import asyncio, json, socket, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

PASS = 0
FAIL = 0
BASELINE = {}


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

    def do_POST(self):
        # 反例①:真实的 422(账号已存在)
        if "simulate=422" in self.path:
            body = json.dumps({"error": "email_taken", "message": "账号已存在"}, ensure_ascii=False).encode("utf-8")
            self._send(422, body, "application/json; charset=utf-8")
        else:
            self._send(200, b"{}", "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        return


async def call(session, name, args, timeout=60):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    return json.loads(r.content[0].text)


async def click_by_text(session, wid, text, settle=0.2):
    f = await call(session, "world_find", {"world_id": wid, "q": text})
    matches = f.get("matches") or []
    if not matches:
        return None
    await asyncio.sleep(settle)
    return await call(session, "world_act", {"world_id": wid, "kind": "click", "id": matches[0]["id"]})


async def main():
    # 本地 HTTP 服务(反例①需要真实 422)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/external_facts.html"
    print(f"本地服务: {url}\n")

    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout=30)
                d = await call(session, "world_open", {"url": url, "wait_ms": 1500})
                wid = d["world_id"]
                print(f"world_id = {wid}\n")

                cases = [
                    ("①", "提交注册", "显示成功但后端 422", "errored"),
                    ("②", "确认支付", "按钮变灰但请求 rollback", "uncertain/errored"),
                    ("③", "提交任务", "202 后异步失败(3s)", "errored"),
                    ("⑤", "点我(无副作用)", "变化非本动作引起(后台推送)", "unchanged/uncertain"),
                ]
                for tag, text, desc, target in cases:
                    card = await click_by_text(session, wid, text)
                    if card is None:
                        check(f"{tag} 找到目标「{text}」", False)
                        continue
                    po = card.get("page_outcome")
                    eff = (card.get("effect") or {}).get("verdict")
                    sit = (card.get("situation") or {}).get("type")
                    BASELINE[tag] = po
                    check(f"{tag} {desc}:不得判 progressed", po != "progressed",
                          f"page_outcome={po} verdict={eff}")
                    print(f"       当前判定={po} verdict={eff} situation={sit} | 目标态={target}")

                # ① 额外核验:必须是真 422,不是传输错误
                card1 = await click_by_text(session, wid, "提交注册")
                if card1:
                    errs = card1.get("errors") or (card1.get("situation") or {}).get("errors") or []
                    statuses = [e.get("status") for e in errs if isinstance(e, dict)]
                    check("① 归因到真实 HTTP 422(而非传输错误)", 422 in statuses,
                          f"statuses={statuses}")

                # ④ DOM 值 vs 账本值
                f = await call(session, "world_find", {"world_id": wid, "q": "模拟:查询账本"})
                m = f.get("matches") or []
                if m:
                    await call(session, "world_act", {"world_id": wid, "kind": "click", "id": m[0]["id"]})
                    r = await call(session, "world_eval", {"world_id": wid, "expression":
                        "() => ({dom: document.getElementById('dom-balance').textContent, ledger: document.getElementById('ledger').textContent})"})
                    data = json.loads(r.get("result") or "{}")
                    if isinstance(data, str):
                        data = json.loads(data)
                    check("④ DOM 值与账本值确实不一致(场景成立)",
                          data.get("dom") != data.get("ledger"),
                          f"dom={data.get('dom')} ledger={data.get('ledger')}")
                    print(f"       DOM={data.get('dom')} vs 账本={data.get('ledger')} → 期望 Runtime 报 uncertain/unchanged")

                await call(session, "world_close", {"world_id": wid})
    finally:
        httpd.shutdown()

    print("\n=== 当前基线(后续每补一类 Runtime 职责应点亮一条)===")
    for k, v in BASELINE.items():
        print(f"  {k} → {v}")
    print(f"\n===== 结果:通过 {PASS} 项,失败 {FAIL} 项 =====")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
