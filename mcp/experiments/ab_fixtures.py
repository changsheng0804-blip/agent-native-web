# -*- coding: utf-8 -*-
"""A/B 任务夹具:本地 HTTP 服务模拟真实 SPA 行为(慢接口/静默失败/实时变价)。

三个任务场景:
  T1 /ab/orders.html   查询订单:点击后调 /api/orders(延迟 2.5s 才返回)→ 渲染列表
  T2 /ab/submit.html   提交订单:点提交调 /api/submit(返回 500,页面无任何变化=静默失败)
  T3 /ab/price.html    实时变价:页面轮询 /api/price(1.5s 后 1000→1200),下单须填当前价

设计原则:行为贴近真实 SPA——DOM 由 fetch 驱动,页面自身不暴露"请求失败"的任何痕迹
(静默失败页连 console 都不打,模拟后端吞错)。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ORDERS = [
    {"id": "A1001", "amount": 328.0, "item": "机械键盘"},
    {"id": "A1002", "amount": 99.9, "item": "鼠标垫"},
    {"id": "A1003", "amount": 599.0, "item": "显示器支架"},
]

ORDERS_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>订单查询</title></head>
<body>
  <h1>订单查询</h1>
  <button id="btn-query">查询订单</button>
  <div id="status">未查询</div>
  <div id="result"></div>
  <script>
    document.getElementById('btn-query').addEventListener('click', function () {
      document.getElementById('status').textContent = '查询中…';
      fetch('/api/orders').then(function (r) { return r.json(); }).then(function (data) {
        var list = data.orders.map(function (o) {
          return '<div class="order" data-id="' + o.id + '">订单 ' + o.id +
                 ': ' + o.item + ' 金额 ' + o.amount.toFixed(1) + ' 元</div>';
        }).join('');
        document.getElementById('result').innerHTML = list;
        document.getElementById('status').textContent = '查询完成';
      });
    });
  </script>
</body></html>"""

SUBMIT_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>提交订单</title></head>
<body>
  <h1>提交订单</h1>
  <input id="order-no" value="A1001" />
  <button id="btn-submit">提交订单</button>
  <div id="status">未提交</div>
  <script>
    document.getElementById('btn-submit').addEventListener('click', function () {
      document.getElementById('status').textContent = '提交中…';
      // 后端静默失败:页面不显示任何错误痕迹,状态停留在"提交中"
      fetch('/api/submit', {method: 'POST'}).then(function (r) { return r.json(); });
    });
  </script>
</body></html>"""

PRICE_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>下单</title></head>
<body>
  <h1>商品下单</h1>
  <div>商品:无线耳机</div>
  <div>当前价格:<span id="price">1000</span> 元</div>
  <label>数量</label><input id="qty" value="1" />
  <label>下单价格</label><input id="order-price" value="1000" />
  <button id="btn-order">下单</button>
  <div id="status">未下单</div>
  <script>
    // 模拟实时行情:每 1.5s 轮询,价格 1000 → 1200
    var tick = 0;
    setInterval(function () {
      fetch('/api/price').then(function (r) { return r.json(); }).then(function (d) {
        document.getElementById('price').textContent = d.price;
        tick += 1;
        // 只在价格变化时提示(用户可见),下单价格输入框由 agent 自己维护
        if (tick === 2) { document.getElementById('status').textContent = '价格已更新'; }
      });
    }, 1500);
    document.getElementById('btn-order').addEventListener('click', function () {
      var p = document.getElementById('order-price').value;
      var q = document.getElementById('qty').value;
      document.getElementById('status').textContent = '已下单:价格 ' + p + ' × ' + q;
    });
  </script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/ab/orders.html":
            return self._send(200, ORDERS_HTML.encode(), "text/html; charset=utf-8")
        if self.path == "/ab/submit.html":
            return self._send(200, SUBMIT_HTML.encode(), "text/html; charset=utf-8")
        if self.path == "/ab/price.html":
            return self._send(200, PRICE_HTML.encode(), "text/html; charset=utf-8")
        if self.path == "/api/orders":
            time.sleep(2.5)  # 慢接口:SPA 长时间无反馈
            return self._send(200, {"orders": ORDERS})
        if self.path == "/api/price":
            now = time.time()
            price = 1200 if (now - PRICE_START) > 1.5 else 1000
            return self._send(200, {"price": price})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/submit":
            return self._send(500, {"error": "internal server error"})  # 静默失败
        return self._send(404, {"error": "not found"})


PRICE_START = time.time()


def serve(port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://127.0.0.1:{port}"


if __name__ == "__main__":
    server, base = serve()
    print("fixture server:", base)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
