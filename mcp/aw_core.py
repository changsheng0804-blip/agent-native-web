# -*- coding: utf-8 -*-
"""纯常量与无状态函数层——自 mcp/server.py 拆出,行为不变。

分层约束:本层不得引用任何运行时状态(_worlds/_evaluate/_task_* 等);
有状态的实现留在 server.py,后续再按原语层/特性簇拆分。
"""
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import mcp.types as types


CANONICAL_TOOLS = {"world_open", "world_guide", "world_find", "world_act", "world_outcome", "world_close"}


CANONICAL_ORDER = ["world_open", "world_guide", "world_find", "world_act", "world_outcome", "world_close"]


ACTION_NAMES = {"world_click", "world_click_at", "world_fill", "world_batch_fill", "world_press", "world_navigate"}


TRACKED_ACTION_NAMES = ACTION_NAMES | {"world_act"}


AUTH_COOKIE_HINTS = ["passport", "session", "token", "sid", "uid", "unb", "sso", "login", "auth"]


ASSUMPTION_INTERVAL_S = 1.0   # 每前提最小检查间隔


ASSUMPTION_RECHECK_S = 0.3    # 通知前二次校验间隔


TIMELINE_MAX = 600


ACTION_EVIDENCE_PRE_S = 0.5


_IMPORTANT_ROLES = {
    "dialog", "alertdialog", "menu", "form", "button", "input", "combobox",
    "listbox", "option", "link", "navigation", "tab", "tablist", "searchbox",
    "textbox", "select", "details", "summary", "tooltip",
}
# digest 强信号角色(窄口径):只认"几乎必是操作结果"的语义。
# 重型 SPA 整体重渲染时,页面外壳(button/link/navigation)会大量"假新增"刷屏,
# 若把它们标高,真信号(弹窗)会被挤出 highlights(digest 价值评估实测:噪声 29 vs 强信号 5)。


_DIGEST_HIGH_ROLES = {
    "dialog", "alertdialog", "menu", "option", "listbox", "combobox",
    "input", "select", "searchbox", "textbox",
}
# 内容性角色 → 中重要性


_MEDIUM_ROLES = {
    "heading", "list", "listitem", "article", "section", "region",
    "card", "banner", "contentinfo", "main", "complementary",
    # 外壳/重渲染常见角色:digest 出现不一定是操作结果,降为中(仅影响 digest,不影响 effect)
    "button", "link", "navigation", "tab", "tablist", "form", "details", "summary",
}


_ROLE_LABEL = {
    "dialog": "弹窗", "alertdialog": "警告弹窗", "menu": "菜单", "button": "按钮",
    "input": "输入框", "combobox": "组合框", "listbox": "列表", "option": "选项",
    "link": "链接", "navigation": "导航", "tab": "标签页", "tablist": "标签栏",
    "searchbox": "搜索框", "textbox": "文本框", "select": "选择器",
    "details": "折叠区", "summary": "折叠标题", "tooltip": "提示",
    "heading": "标题", "list": "列表", "listitem": "列表项", "article": "文章",
    "section": "区块", "region": "区域", "card": "卡片", "banner": "页头",
    "contentinfo": "页脚", "main": "主体", "complementary": "侧栏",
    "form": "表单", "content": "内容", "img": "图片", "video": "视频",
    "table": "表格", "navigation2": "导航",
}


SOURCE_FACT = "fact"


SOURCE_EVIDENCE = "evidence"


SOURCE_INFERENCE = "inference"


SOURCE_UNTRUSTED = "untrusted"

# 统一后果卡字段 → 来源(白名单,不随页面内容变化;键=实际卡片字段,支持点分路径)


CARD_SOURCE_RULES = {
    "page.before_url": SOURCE_FACT,
    "page.after_url": SOURCE_FACT,
    "page.url_changed": SOURCE_FACT,
    "page.state": SOURCE_FACT,
    "changes_seq": SOURCE_FACT,
    "evidence_seq": SOURCE_FACT,
    "world_epoch": SOURCE_FACT,
    "target.id": SOURCE_FACT,
    "target.fingerprint": SOURCE_FACT,
    "target.name": SOURCE_UNTRUSTED,
    "why": SOURCE_EVIDENCE,
    "effect.verdict": SOURCE_EVIDENCE,
    "effect.observed": SOURCE_EVIDENCE,
    "overlays": SOURCE_EVIDENCE,
    "situation.type": SOURCE_INFERENCE,
    "next.suggested": SOURCE_INFERENCE,
    "recipes": SOURCE_INFERENCE,
    "handoff": SOURCE_INFERENCE,
    "error": SOURCE_EVIDENCE,
}


STYLE_DIFF_PROPS = ("backgroundColor", "color", "opacity", "visibility",
                    "display", "transform", "borderTopColor")


STYLE_SNAPSHOT_MAX = 40


def _lite_mode():
    return os.environ.get("AGENT_WORLD_LITE", "").strip().lower() in ("1", "true", "yes", "on")


def _verdict_mode():
    """消融实验开关(默认 full = 正常行为)。

      full  正常:返回完整后果卡(含 page_outcome 五态判定)
      no-verdict  L1:剥掉合成判定,但保留原始证据(errors/网络状态码/observed/why)
      structure   L0:只保留结构信息,剥掉判定与证据(模拟无验证层)

    用途:C1 消融实验——同一模型、同一任务,只改返回值,比较假成功率。
    只影响对外返回,不改变内部记录(轨迹/缓存仍是完整卡)。
    """
    return os.environ.get("AGENT_WORLD_VERDICT_MODE", "full").strip().lower()


# L0 保留的字段(纯结构/事实,不含任何"生效性"信号)
_STRUCTURE_ONLY_KEYS = {
    "world_id", "channel", "target", "action", "page", "overlays",
    "sources", "evidence_seq", "changes_seq", "world_epoch", "status", "task_state",
    "matches", "count", "ambiguous", "entities", "entity", "url", "title",
}
# L1 保留(结构 + 原始证据),剥掉的是合成判定
_VERDICT_KEYS = {"page_outcome", "situation", "confidence", "why", "next", "recipes", "handoff", "effect"}


def _strip_status_verdict(status):
    """剥掉状态卡里泄漏判定的字段(消融实验用)。

    status.task.last_outcome.page_outcome 是**合成判定**的回声,
    若不清掉,即使主卡已剥离判定,模型仍能从状态卡读到结论。
    """
    if not isinstance(status, dict):
        return status
    out = dict(status)
    task = out.get("task")
    if isinstance(task, dict):
        task = dict(task)
        task.pop("last_outcome", None)
        out["task"] = task
    return out


def _apply_verdict_mode(payload):
    """按 AGENT_WORLD_VERDICT_MODE 裁剪后果卡(消融实验用;full 时原样返回)。

    只裁剪**对外返回**,内部轨迹/缓存仍保存完整卡,保证实验可审计。
    """
    mode = _verdict_mode()
    if mode == "full" or not isinstance(payload, dict):
        return payload
    out = dict(payload)
    if mode == "no-verdict":
        # L1:剥掉合成判定,但保留原始证据(errors 是事实,不是判定)
        for k in _VERDICT_KEYS:
            out.pop(k, None)
        errs = payload.get("errors")
        if errs is None:
            errs = (payload.get("situation") or {}).get("errors")
        if errs:
            out["errors"] = errs
        # action_evidence 里的 decision 是**合成结论**(如"已生效,继续下一步"),
        # 会泄漏判定口径;只保留原始网络/DOM 证据(requests/failures/transition)。
        ae = out.get("action_evidence")
        if isinstance(ae, dict):
            ae = {k: v for k, v in ae.items() if k != "decision"}
            out["action_evidence"] = ae
        out["status"] = _strip_status_verdict(out.get("status"))
        # sources 里的 effect.verdict / why 来源标注会暗示"有判定存在",一并清掉
        src = out.get("sources")
        if isinstance(src, dict):
            out["sources"] = {k: v for k, v in src.items() if not k.startswith(("effect.", "why"))}
        out["verdict_mode"] = "no-verdict"
    elif mode == "structure":
        # L0:只保留结构/事实,剥掉判定与证据
        out = {k: v for k, v in out.items() if k in _STRUCTURE_ONLY_KEYS}
        out["status"] = _strip_status_verdict(out.get("status"))
        src = out.get("sources")
        if isinstance(src, dict):
            out["sources"] = {k: v for k, v in src.items() if not k.startswith(("effect.", "why"))}
        out["verdict_mode"] = "structure"
    return out


def _same_origin(left, right):
    try:
        a, b = urlsplit(str(left or "")), urlsplit(str(right or ""))
        if a.scheme == "file" or b.scheme == "file":
            return a.scheme == b.scheme and Path(a.path).parent == Path(b.path).parent
        return (a.scheme, a.netloc) == (b.scheme, b.netloc)
    except Exception:
        return False


def _page_node_identity(signal):
    """页面节点组合身份的轻量版本：网址模式、标题、覆盖层和状态。"""
    signal = signal or {}
    url = str(signal.get("url") or "")
    try:
        parsed = urlsplit(url)
        path = re.sub(r"/(?:\d{2,}|[0-9a-f]{8,})", "/:param", parsed.path or "/")
        url_pattern = f"{parsed.scheme}://{parsed.netloc}{path}"[:300]
    except Exception:
        url_pattern = url[:300]
    regions = []
    for key in ("dialogs", "menus"):
        regions.extend(str(x.get("name") or x.get("id") or "")[:80].lower()
                       for x in signal.get(key, []) or [])
    basis = {"url_pattern": url_pattern,
             "title": re.sub(r"\s+", " ", str(signal.get("title") or "").strip().lower())[:120],
             "regions": sorted(regions)[:8], "state": signal.get("state") or "unknown"}
    raw = json.dumps(basis, ensure_ascii=False, sort_keys=True)
    return "node_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], basis


def _evidence_norm_url(url):
    try:
        p = urlsplit(str(url))
        return f"{p.netloc}{p.path or '/'}"[:160]
    except Exception:
        return str(url)[:160]


def _nav_url(url):
    """导航判定用的 URL 规范形:剥离 fragment(hash)。

    纯 hash 变化(`/page` → `/page#tab2`)是同一文档内的锚点跳转,不是导航。
    以往用整串 URL 比较,`#tab2` 会被判成 progressed/navigation,让 agent 误以为
    已跳转并去读"新页"(实测复现:aw_hash_fixture.html#tab2)。
    fragment 在导航语义上不改变文档,因此从判定键中剔除。
    """
    raw = str(url or "")
    try:
        p = urlsplit(raw)
        # 无 fragment 时原样返回,保留 query(表单提交常靠 query 变化体现)
        return f"{p.scheme}://{p.netloc}{p.path}?{p.query}" if p.query else f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return raw.split("#", 1)[0]


def _nav_url_changed(before_url, after_url):
    """URL 是否发生了**导航级**变化(fragment-only 变化不算)。"""
    if not before_url or not after_url:
        return bool(before_url) != bool(after_url)
    return _nav_url(before_url) != _nav_url(after_url)


def _signal_items(signal, key):
    return signal.get(key, []) if isinstance(signal, dict) else []


def _signal_delta(before, after, key):
    """返回信道中新增和消失的覆盖层,只保留小量可读证据。"""
    def item_key(item):
        if not isinstance(item, dict):
            return str(item)
        return (item.get("id"), item.get("name"), item.get("text"))

    before_map = {item_key(x): x for x in _signal_items(before, key)}
    after_map = {item_key(x): x for x in _signal_items(after, key)}
    new_keys = after_map.keys() - before_map.keys()
    gone_keys = before_map.keys() - after_map.keys()
    return {
        "new": [after_map[k] for k in new_keys][:8],
        "gone": [before_map[k] for k in gone_keys][:8],
    }


def _entity_match(e, filters):
    """world_find 的候选后置过滤(小集合内精确过滤,复用内核语义口径)。"""
    if "fingerprint" in filters and (e.get("fingerprint") or "") != str(filters["fingerprint"]):
        return False
    if "role" in filters and (e.get("semantic") or "") != filters["role"]:
        return False
    if "tag" in filters and (e.get("tag") or "").lower() != str(filters["tag"]).lower():
        return False
    if "name" in filters and filters["name"] not in (e.get("name") or ""):
        return False
    if "text" in filters and filters["text"] not in (e.get("text") or ""):
        return False
    if "interactive" in filters and bool(e.get("interactive")) != bool(filters["interactive"]):
        return False
    if "inViewport" in filters and bool(e.get("inViewport")) != bool(filters["inViewport"]):
        return False
    return True


def _anomaly_from_counts(visible_dom, world_count):
    """环境异常纯判定(与 _status 同口径):可见 DOM 远多于世界元素即异常。
    阈值 35%/50 个沿用状态卡实战值(Booking.com 误报教训),改动须两处同步。"""
    try:
        return bool(visible_dom and visible_dom > 50 and (world_count or 0) < visible_dom * 0.35)
    except Exception:
        return False


def _target_state_flip(before_state, after_state):
    """目标自身状态是否翻转(状态切换类交互的证据,如 tab 的 aria-selected、
    折叠的 aria-expanded、勾选 checked)。返回 (flipped, what)"""
    if not before_state or not after_state:
        return False, None
    for key in ("ariaSelected", "ariaExpanded", "checked"):
        b = before_state.get(key)
        a = after_state.get(key)
        if b is not None or a is not None:
            if (b or None) != (a or None):
                return True, key
    # className 变化(弱信号,仅当前面三个都无差异时考虑)
    bc = (before_state.get("className") or "").strip()
    ac = (after_state.get("className") or "").strip()
    if bc and ac and bc != ac:
        return True, "className"
    return False, None


def _event_importance(evt):
    """单条变更事件的重要性分级(high/medium/low)——供 digest/变更流使用。
    依据:事件类型(结构性 add/remove > update > visibility) × 语义角色。
    注意:high 只给"强信号"角色(_DIGEST_HIGH_ROLES)——重型 SPA 重渲染时
    外壳(button/link/navigation)大量假新增,若标高会把真弹窗挤出 highlights。
    旧事件(内核补 semantic 前记录)缺 semantic 时从 name 前缀推断。
    """
    etype = evt.get("type")
    semantic = evt.get("semantic") or ""
    if not semantic:
        name = evt.get("name") or ""
        semantic = name.split(".")[0] if name else ""
    if etype == "visibility":
        return "low"
    if etype in ("add", "remove"):
        if semantic in _DIGEST_HIGH_ROLES:
            return "high"
        if semantic in _IMPORTANT_ROLES or semantic in _MEDIUM_ROLES:
            return "medium"
        return "medium"  # 新增/移除默认中(结构变化),具体由 digest 归纳
    # update
    if semantic in _DIGEST_HIGH_ROLES:
        return "medium"  # 强信号构件更新值得看
    return "low"


def _sources_for_card(card):
    """按白名单为卡片字段打来源标签(支持点分路径如 page.url/target.name)。"""
    out = {}
    for path, tag in CARD_SOURCE_RULES.items():
        node = card
        ok = True
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                ok = False
                break
            node = node[part]
        if ok:
            out[path] = tag
    return out


def _guide_terms(task):
    """从一句任务描述提取少量搜索锚点,不让导览层读取完整页面文本。"""
    stopwords = {
        "请帮我", "帮我", "帮助", "找到", "查找", "查看", "打开", "进入", "点击", "确认",
        "页面", "网页", "网站", "当前", "任务", "并", "和", "的", "一个", "一下", "区域",
        "操作", "完成", "是否", "然后", "之后", "上方", "里面", "这个", "那个",
        "为", "把", "将", "开启", "开通", "设置", "标记为", "发布到", "选择", "提交", "保存",
        "find", "open", "go", "to", "the", "a", "an", "and", "on", "in", "page", "confirm",
    }
    raw = re.findall(r"[a-z0-9][a-z0-9_-]*|[\u4e00-\u9fff]{2,}", str(task).lower())
    terms = []
    aliases = {
        "拉取请求": "pull requests",
        "合并请求": "pull requests",
        "问题": "issues",
        "查找": "search",
        "检索": "search",
        "筛选": "filter",
        "搜索": "search",
        "发布": "release",
        "标签": "tag",
        "模型": "model",
        "弹窗": "dialog",
    }
    for item in raw:
        for source, alias in aliases.items():
            if source in item and alias not in terms:
                terms.append(alias)
        if re.match(r"^[a-z0-9]", item):
            # 英文/数字词:精确停用词过滤。绝不能对连写英文做子串 replace——
            # 停用词 "a" 会把 "star" 拆成 "st"(实测:全场元素 name 都含 st,
            # 候选全 4 分大平局,Notifications 抢走 Star 任务的 first)。
            if item not in stopwords and len(item) >= 2 and item not in terms:
                terms.append(item)
        else:
            # 中文段无空格分隔,停用词只能按子串剔除(如"打开仓库"剔除"打开")
            cleaned = item
            for stop in sorted(stopwords, key=len, reverse=True):
                cleaned = cleaned.replace(stop, " ")
            for term in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned):
                if term not in stopwords and len(term) >= 2 and term not in terms:
                    terms.append(term)
    expanded = list(terms)
    for term in terms:
        alias = aliases.get(term)
        if alias and alias not in expanded:
            expanded.append(alias)
    # 网页上“筛选”经常由搜索输入框承载,两者应作为同一任务焦点。
    if "filter" in expanded and "search" not in expanded:
        expanded.append("search")
    if "search" in expanded and "filter" not in expanded:
        expanded.append("filter")
    return expanded[:16]


def _build_click_effect(before_rows, after_rows, url_changed=False, before_dialogs=None,
                        after_dialogs=None, before_target_state=None, after_target_state=None,
                        disappear_ok=False, fill_verified=False):
    """空间区域 diff → 操作生效报告。
    判定优先级(从强到弱):
      1. fill_verified: 填表值已进入可见输入框 → effected/high(填表专属强证据)
      2. URL 变化 → effected/high(导航/提交类)
      3. 全页出现"新的可见 dialog/menu"(点击前没有、点击后有)→ effected/high
         —— 远距弹窗兜底:弹窗出现在 ±200px 区域外时,靠全页 dialog 扫描识别(F1 修复)
      4. disappear_ok 且"点击前有可见 dialog、点击后没了" → effected/high
         —— 按键关闭弹窗兜底:按 Escape 关弹窗 = 弹窗消失 = 生效
      5. 目标自身状态翻转(aria-selected/aria-expanded/checked/class)→ effected/high
         —— 状态切换类交互兜底:tab/折叠/勾选无新构件,只有目标状态变
      6. 目标区域新增关键构件(dialog/button/menu/option 等)→ effected/high
      7. 区域有变化但无关键构件 → changed/medium
      8. 区域无变化+URL 未变 → no-change
    """
    before_ids = {r[0] for r in before_rows}
    after_ids = {r[0] for r in after_rows}
    new_rows = [r for r in after_rows if r[0] not in before_ids]
    gone_rows = [r for r in before_rows if r[0] not in after_ids]
    key_rows = [r for r in new_rows if r[1] in _IMPORTANT_ROLES]

    observed = []
    for r in key_rows[:8]:
        observed.append({"type": "add", "id": r[0], "semantic": r[1], "name": r[2]})

    # 全页新出现 dialog/menu 兜底(远距弹窗 F1 修复)
    # 但两者证据强度不同(FP 收紧):
    #   dialog/alertdialog 是模态接管(aria-modal),几乎必为本次动作引发 → 可判 effected
    #   menu 是非模态浮层,可能由悬停/后台脚本/其它控件触发 → 单独出现只能判 changed(uncertain)
    before_d = set((d[0] for d in before_dialogs or []))
    new_overlay_items = [d for d in (after_dialogs or []) if d[0] not in before_d]
    new_dialogs = [d for d in new_overlay_items if d[1] in ("dialog", "alertdialog")]
    new_menus = [d for d in new_overlay_items if d[1] == "menu"]
    # 全页消失的 dialog(按键关闭弹窗兜底)
    after_d = set((d[0] for d in after_dialogs or []))
    gone_dialogs = [d for d in (before_dialogs or []) if d[0] not in after_d]

    if fill_verified:
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": "填表值已进入可见输入框",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if url_changed:
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": "URL 变化(导航/提交类)",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if new_dialogs:
        names = "、".join(f"{_ROLE_LABEL.get(d[1], d[1])} {d[2]}" for d in new_dialogs[:5])
        for d in new_dialogs[:8]:
            if not any(o["id"] == d[0] for o in observed):
                observed.append({"type": "add", "id": d[0], "semantic": d[1], "name": d[2]})
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面出现新的弹窗/菜单(可能远离目标): {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if disappear_ok and gone_dialogs:
        names = "、".join(f"{_ROLE_LABEL.get(d[1], d[1])} {d[2]}" for d in gone_dialogs[:5])
        for d in gone_dialogs[:8]:
            observed.append({"type": "remove", "id": d[0], "semantic": d[1], "name": d[2]})
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"弹窗/菜单已关闭: {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    state_flip, state_key = _target_state_flip(before_target_state, after_target_state)
    if state_flip:
        label = {"ariaSelected": "选中态(aria-selected)", "ariaExpanded": "展开态(aria-expanded)",
                 "checked": "勾选(checked)", "className": "样式(className)"}.get(state_key, state_key)
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"目标自身状态变化: {label} 翻转",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    # 仅新增非模态 menu(无 dialog):证据不足以判"生效"——菜单可能由悬停/后台脚本触发,
    # 与本次点击无因果关系。降级为 changed(→ uncertain,让 agent 复核一次),不报假成功。
    # 必须在 key_rows 之前判断:menu 属于 _IMPORTANT_ROLES,否则会被"关键构件"分支
    # 抢先判成 effected(实测风险:点失效按钮但页面另处弹出无关菜单 → 假成功)。
    if new_menus:
        names = "、".join(f"{_ROLE_LABEL.get(d[1], d[1])} {d[2]}" for d in new_menus[:5])
        for d in new_menus[:8]:
            if not any(o["id"] == d[0] for o in observed):
                observed.append({"type": "add", "id": d[0], "semantic": d[1], "name": d[2]})
        return {
            "verdict": "changed",
            "confidence": "medium",
            "why": f"页面出现新的菜单(非模态,无法确认由本次动作引发): {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if key_rows:
        names = "、".join(f"{_ROLE_LABEL.get(r[1], r[1])} {r[2]}" for r in key_rows[:5])
        return {
            "verdict": "effected",
            "confidence": "high",
            "why": f"目标区域出现关键构件: {names}",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    if new_rows or gone_rows:
        return {
            "verdict": "changed",
            "confidence": "medium",
            "why": "目标区域有变化但无关键交互构件",
            "observed": observed,
            "region_changed": {"new": len(new_rows), "gone": len(gone_rows)},
        }
    return {
        "verdict": "no-change",
        "confidence": "high",
        "why": "目标区域无变化(点击可能未生效,或效果发生在远处)",
        "observed": [],
        "region_changed": {"new": 0, "gone": 0},
    }
