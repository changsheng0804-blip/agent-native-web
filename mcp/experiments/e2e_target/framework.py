# -*- coding: utf-8 -*-
"""靶场框架(端到端测试对象):多站点注册 + 账本 + 故障注入 + 结构化闭环通道。

设计要点(需求:可插拔多对象 + 完整闭环反馈通道):
  1. **多站点插拔**:每个站点 = 一个 Site 子类包(路由/账本 schema/故障画像/任务集)。
     框架只做通用骨架:本地 HTTP、幂等键账本、故障注入、通道端点。
  2. **闭环反馈通道**(WebMCP 端游形态):
       GET /api/state                 → 权威账本状态(JSON,永不撒谎)
       GET /api/verify?client_key=X   → §7.7 幂等键对账(EXISTS 谓词)
     页面 UI 照常可撒谎(故障注入只骗 UI);通道是站点声明的契约,harness/Runtime 消费。
  3. **故障注入在服务器侧**(开跑前配置,页面源码与 HTML 均不含故障参数——防 agent 读源码作弊)。
  4. **干扰排除**:localhost、无风控验证码、固定种子。

故障画像(fault profile,每轮开跑前指定):
  delay_ms     UI 可见延迟(提交后页面详情 N ms 后才显示终态)
  lie422       UI 显示成功,但账本未建单(反例①)
  rollback     UI 显示已确认,账本 N ms 后翻转为 cancelled(反例②)
  async_fail   UI 显示已确认,账本停留在 processing 并 N ms 后转 failed(反例③)
页面按画像决定"给 UI 看什么";账本永远记真实状态;通道永远出账本真相。
"""
import html
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Ledger:
    """带幂等键的资源账本(§7.7 落地):create_by_key 保证同 key 只建一个资源。"""

    def __init__(self, seed):
        self.rng = random.Random(seed)
        self.by_key = {}          # client_key → resource_id
        self.resources = {}       # resource_id → dict(status, ts, ...)

    def next_id(self, prefix):
        return f"{prefix}-{self.rng.randint(100000, 999999)}"

    def create_by_key(self, client_key, payload, status="submitted"):
        """幂等:同 key 返回已有资源;否则新建。返回 (resource, created_bool)。"""
        if client_key and client_key in self.by_key:
            rid = self.by_key[client_key]
            return self.resources[rid], False
        rid = self.next_id(payload.get("_prefix", "res"))
        payload["id"] = rid
        payload["client_key"] = client_key
        payload["status"] = status
        payload["ts"] = time.time()
        payload["ts_str"] = time.strftime("%H:%M:%S")
        self.resources[rid] = payload
        if client_key:
            self.by_key[client_key] = rid
        return payload, True

    def get(self, rid):
        return self.resources.get(rid)

    def set_status(self, rid, status):
        r = self.resources.get(rid)
        if r:
            r["status"] = status
            r["ts_str"] = time.strftime("%H:%M:%S")
        return r

    def verify(self, client_key):
        """§7.7 幂等键 EXISTS 谓词:按 key 查资源状态。"""
        if not client_key:
            return {"exists": False}
        rid = self.by_key.get(client_key)
        if not rid:
            return {"exists": False}
        r = self.resources[rid]
        return {"exists": True, "id": rid, "status": r["status"]}


class FaultProfile:
    """服务器侧故障画像(开跑前配置;不进入任何页面源码)。"""

    def __init__(self, seed, delay_ms=0, lie422=0.0, rollback=0.0,
                 rollback_after_ms=3000, async_fail=0.0, async_fail_after_ms=3000):
        self.rng = random.Random(seed)
        self.delay_ms = delay_ms
        self.lie422 = lie422
        self.rollback = rollback
        self.rollback_after_ms = rollback_after_ms
        self.async_fail = async_fail
        self.async_fail_after_ms = async_fail_after_ms

    def roll(self, prob):
        return prob > 0 and self.rng.random() < prob


class Site:
    """站点基类:子类实现 routes() 与页面渲染。"""

    name = "base"
    prefix = "/base"
    FAULT_KEYS = []

    def __init__(self, ledger, fault, store):
        self.ledger = ledger
        self.fault = fault
        self.store = store          # 跨路由状态(site 自用)

    # 子类实现
    def handle(self, path, method, query, form, body):
        raise NotImplementedError

    def page(self, title, body_html):
        return (f"<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                f"<title>{html.escape(title)}</title>"
                f"<style>body{{font-family:sans-serif;margin:24px;max-width:640px}}"
                f".ok{{color:green}}.err{{color:red}}.pending{{color:#b58900}}"
                f"input,textarea{{width:100%;margin:4px 0}}button{{margin-top:8px}}</style>"
                f"</head><body>{body_html}</body></html>")

    def esc(self, s):
        return html.escape(str(s))


class TargetServer:
    """多站点靶场:一个端口,按前缀路由到各站点。"""

    def __init__(self, sites, seed):
        self.sites = sites          # {name: Site}
        self.seed = seed

    def start(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_factory())
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        port = self.httpd.server_address[1]
        return f"http://127.0.0.1:{port}"

    def stop(self):
        self.httpd.shutdown()

    def _handler_factory(self):
        sites = self.sites

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body, ctype="text/html; charset=utf-8"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                    ctype = "application/json; charset=utf-8"
                elif isinstance(body, str):
                    if code in (301, 302):                  # 重定向:body 是 Location
                        self.send_response(code)
                        self.send_header("Location", body)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _route(self):
                path = self.path.split("?")[0]
                q = {}
                if "?" in self.path:
                    for kv in self.path.split("?", 1)[1].split("&"):
                        if "=" in kv:
                            k, v = kv.split("=", 1)
                            q[k] = v
                site = None
                for s in sites.values():
                    if path == s.prefix or path.startswith(s.prefix + "/"):
                        site = s
                        break
                return site, path, q

            def do_GET(self):
                site, path, q = self._route()
                if not site:
                    self._send(404, "not found")
                    return
                try:
                    code, body = site.handle("GET", path, q, {}, {})
                    self._send(code, body)
                except Exception as e:                      # 服务器不崩:任何异常给 500 JSON
                    self._send(500, {"error": type(e).__name__, "msg": str(e)[:200]})

            def do_POST(self):
                site, path, q = self._route()
                if not site:
                    self._send(404, "not found")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8", "replace")
                form = {}
                for kv in raw.split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        from urllib.parse import unquote_plus
                        form[unquote_plus(k)] = unquote_plus(v)
                try:
                    code, body = site.handle("POST", path, q, form, raw)
                    self._send(code, body)
                except Exception as e:
                    self._send(500, {"error": type(e).__name__, "msg": str(e)[:200]})

            def log_message(self, *a):
                pass

        return H


def build_target(site_builders, seed):
    """site_builders: [(name, SiteClass)] → TargetServer(带统一账本与通道)。"""
    ledger = Ledger(seed)
    fault = FaultProfile(seed)
    sites = {}
    for name, cls in site_builders:
        sites[name] = cls(ledger, fault, {})
    return TargetServer(sites, seed)
