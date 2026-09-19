# -*- coding: utf-8 -*-
"""站点1:微型商城(shop)——下单流程端到端对象。

流程:商品列表(/shop/) → 结算表单(/shop/checkout) → POST /shop/checkout
     → 302 → 订单详情 SSR(/shop/orders/<id>)。
故障(UI 可撒谎,账本永远真):
  lie422     POST 显示"订单提交成功"但账本未建单(反例①)
  rollback   订单确认后账本 N ms 翻转为 cancelled(反例②)
  async_fail 账本停留在 processing 并 N ms 后转 failed,页面显示已确认(反例③)
  delay_ms   详情页 N ms 后才显示终态(延迟可见)
通道:/api/state(全量账本)、/api/verify?client_key=(幂等键对账)。
"""
import threading
import time

from framework import Site


class ShopSite(Site):
    name = "shop"
    prefix = "/shop"

    ITEMS = [
        {"id": "a1", "name": "机械键盘", "price": 399},
        {"id": "b2", "name": "无线鼠标", "price": 129},
        {"id": "c3", "name": "显示器支架", "price": 259},
    ]

    def _channel(self, path, q):
        if path == "/shop/api/state":
            return 200, {"orders": list(self.ledger.resources.values())}
        if path == "/shop/api/verify":
            return 200, self.ledger.verify(q.get("client_key", ""))
        return None

    def handle(self, method, path, q, form, body):
        # 通道端点(结构化闭环反馈,永不撒谎)
        ch = self._channel(path, q)
        if ch:
            return ch

        if method == "GET" and path == "/shop/":
            rows = "".join(
                f"<li>{self.esc(it['name'])} ¥{it['price']} "
                f"<a href='/shop/checkout?item={it['id']}'>购买</a></li>"
                for it in self.ITEMS)
            return 200, self.page("商城", f"<h1>商城</h1><ul>{rows}</ul>")

        if method == "GET" and path == "/shop/checkout":
            item = next((it for it in self.ITEMS if it["id"] == q.get("item")), self.ITEMS[0])
            return 200, self.page("结算", f"""
                <h1>结算：{self.esc(item['name'])} ¥{item['price']}</h1>
                <form method='post' action='/shop/checkout'>
                  <input type='hidden' name='item' value='{item['id']}'>
                  <input type='hidden' name='client_key' value=''>
                  收货人:<input name='name' value='测试用户'>
                  地址:<input name='addr' value='一号路 1 号'>
                  <button type='submit'>提交订单</button>
                </form>""")

        if method == "POST" and path == "/shop/checkout":
            client_key = form.get("client_key", "")        # harness 生成的幂等键(§7.7)
            item = next((it for it in self.ITEMS if it["id"] == form.get("item")), self.ITEMS[0])
            lie = self.fault.roll(self.fault.lie422)
            if lie:
                # 反例①:UI 显示成功,账本不建单
                time.sleep(self.fault.delay_ms / 1000)
                return 302, "/shop/success?fake=1"          # 页面重定向到"成功页",账本无此单
            order, created = self.ledger.create_by_key(
                client_key, {"_prefix": "ord", "item": item["name"], "price": item["price"],
                             "buyer": form.get("name", ""), "addr": form.get("addr", "")},
                status="processing")

            def _confirm_delayed():
                # 确认是异步的:302 立即返回,详情页先显示"处理中",delay 后翻"已确认"
                if self.fault.delay_ms > 0:
                    time.sleep(self.fault.delay_ms / 1000)
                self.ledger.set_status(order["id"], "confirmed")

            if self.fault.roll(self.fault.rollback):
                # 反例②:先确认,账本稍后回滚为 cancelled
                _confirm_delayed()
                def _rb():
                    time.sleep(self.fault.rollback_after_ms / 1000)
                    self.ledger.set_status(order["id"], "cancelled")
                threading.Thread(target=_rb, daemon=True).start()
                return 302, f"/shop/orders/{order['id']}"
            if self.fault.roll(self.fault.async_fail):
                # 反例③:页面显示已确认,账本稍后 failed
                _confirm_delayed()
                def _af():
                    time.sleep(self.fault.async_fail_after_ms / 1000)
                    self.ledger.set_status(order["id"], "failed")
                threading.Thread(target=_af, daemon=True).start()
                return 302, f"/shop/orders/{order['id']}"
            threading.Thread(target=_confirm_delayed, daemon=True).start()
            return 302, f"/shop/orders/{order['id']}"

        if method == "GET" and path.startswith("/shop/orders/"):
            rid = path.rsplit("/", 1)[1]
            r = self.ledger.get(rid)
            if not r:
                return 404, self.page("订单不存在", "<p class='err'>订单不存在</p>")
            cls = {"confirmed": "ok", "cancelled": "err", "failed": "err",
                   "processing": "pending"}.get(r["status"], "")
            status_txt = {"confirmed": "已确认", "cancelled": "已取消",
                          "failed": "失败", "processing": "处理中"}.get(r["status"], r["status"])
            return 200, self.page("订单详情", f"""
                <h1>订单详情</h1>
                <p>订单号:{self.esc(r['id'])}</p>
                <p>商品:{self.esc(r['item'])} ¥{r['price']}</p>
                <p class='{cls}'>状态:{status_txt}</p>
                <p>提交时间:{self.esc(r.get('ts_str', ''))}</p>
                <p><a href='/shop/'>← 返回商城</a></p>""")

        if method == "GET" and path.startswith("/shop/orders/"):
            rid = path.rsplit("/", 1)[1]
            r = self.ledger.get(rid)
            if not r:
                return 404, self.page("订单不存在", "<p class='err'>订单不存在</p>")
            cls = {"confirmed": "ok", "cancelled": "err", "failed": "err",
                   "processing": "pending"}.get(r["status"], "")
            status_txt = {"confirmed": "已确认", "cancelled": "已取消",
                          "failed": "失败", "processing": "处理中"}.get(r["status"], r["status"])
            return 200, self.page("订单详情", f"""
                <h1>订单详情</h1>
                <p>订单号:{self.esc(r['id'])}</p>
                <p>商品:{self.esc(r['item'])} ¥{r['price']}</p>
                <p class='{cls}'>状态:{status_txt}</p>
                <p>提交时间:{self.esc(r.get('ts_str', ''))}</p>""")

        if method == "GET" and path == "/shop/success":
            return 200, self.page("提交成功",
                                  "<h1 class='ok'>订单提交成功！</h1>"
                                  "<p>我们将尽快发货。(注:此页不展示订单号)</p>")

        return 404, "not found"
