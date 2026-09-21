# -*- coding: utf-8 -*-
"""站点2:内容站(content)——发布→异步审核→通过/驳回。

流程:文章列表(/content/) → 发布表单(/content/new) → POST /content/new
     → 302 → 文章详情 SSR(/content/posts/<id>)。
账本状态机:submitted → pending → approved / rejected(审核线程异步完成)。
故障(UI 可撒谎,账本永远真):
  lie422     POST 显示"发布成功"但账本未建稿(反例①)
  reject     UI 显示"已发布/审核中",账本 N ms 后 rejected(反例②变体:显示成功被驳回)
  delay_ms   详情页 N ms 后才显示终态
通道:/content/api/state、/content/api/verify?client_key=
"""
import threading
import time

from framework import Site


class ContentSite(Site):
    name = "content"
    prefix = "/content"

    APPROVE_MS = 3000           # 正常审核耗时
    REJECT_RATE = 0.0           # 正常场景的驳回率(可通过故障画像覆盖)

    def _channel(self, path, q):
        if path == "/content/api/state":
            return 200, {"posts": list(self.ledger.resources.values())}
        if path == "/content/api/verify":
            return 200, self.ledger.verify(q.get("client_key", ""))
        return None

    def handle(self, method, path, q, form, body):
        ch = self._channel(path, q)
        if ch:
            return ch

        if method == "GET" and path == "/content/":
            rows = "".join(
                f"<li><a href='/content/posts/{p['id']}'>{self.esc(p['title'])}</a> "
                f"[{self.esc(p['status'])}]</li>"
                for p in self.ledger.resources.values())
            return 200, self.page("内容站", f"<h1>文章</h1><ul>{rows}</ul>"
                                   "<p><a href='/content/new'>发布新文章</a></p>")

        if method == "GET" and path == "/content/new":
            return 200, self.page("发布", """
                <h1>发布新文章</h1>
                <form method='post' action='/content/new'>
                  <input type='hidden' name='client_key' value=''>
                  标题:<input name='title' value='我的文章'>
                  正文:<textarea name='body' rows='4'>内容……</textarea>
                  <button type='submit'>发布</button>
                </form>""")

        if method == "POST" and path == "/content/new":
            client_key = form.get("client_key", "")
            lie = self.fault.roll(self.fault.lie422)
            if lie:
                # 反例①:UI 显示发布成功,账本未建稿
                time.sleep(self.fault.delay_ms / 1000)
                return 302, "/content/success?fake=1"
            post, created = self.ledger.create_by_key(
                client_key, {"_prefix": "art", "title": form.get("title", "无题"),
                             "body": form.get("body", "")},
                status="pending")

            def _approve_delayed():
                # 审核异步:302 立即返回,详情页先"审核中",delay 后翻"已通过"
                if self.fault.delay_ms > 0:
                    time.sleep(self.fault.delay_ms / 1000)
                self.ledger.set_status(post["id"], "approved")

            if self.fault.roll(self.fault.rollback):          # 复用 rollback 位:驳回故障
                # UI 先给"已通过",账本稍后 rejected
                self.ledger.set_status(post["id"], "approved")
                def _rj():
                    time.sleep(self.fault.rollback_after_ms / 1000)
                    self.ledger.set_status(post["id"], "rejected")
                threading.Thread(target=_rj, daemon=True).start()
                return 302, f"/content/posts/{post['id']}"
            threading.Thread(target=_approve_delayed, daemon=True).start()
            return 302, f"/content/posts/{post['id']}"

        if method == "GET" and path.startswith("/content/posts/"):
            rid = path.rsplit("/", 1)[1]
            p = self.ledger.get(rid)
            if not p:
                return 404, self.page("不存在", "<p class='err'>文章不存在</p>")
            cls = {"approved": "ok", "rejected": "err", "pending": "pending"}.get(p["status"], "")
            txt = {"approved": "已通过", "rejected": "已驳回", "pending": "审核中"}.get(
                p["status"], p["status"])
            return 200, self.page("文章详情", f"""
                <h1>{self.esc(p['title'])}</h1>
                <p>{self.esc(p['body'])}</p>
                <p class='{cls}'>状态:{txt}</p>
                <p>提交时间:{self.esc(p.get('ts_str', ''))}</p>
                <p><a href='/content/'>← 返回</a></p>""")

        if method == "GET" and path == "/content/success":
            return 200, self.page("发布成功", "<h1 class='ok'>发布成功！</h1>"
                                  "<p>文章已提交。(此页不展示文章号)</p>")

        return 404, "not found"
