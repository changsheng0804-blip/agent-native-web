# -*- coding: utf-8 -*-
"""动作实现簇——click/fill/batch_fill/press/wait/screenshot/eval/click_at/navigate 与 _act_one 聚合执行、world_act 步骤循环。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖方向:aw_core ← aw_runtime ← 本簇。
"""
import base64, json, time
from urllib.parse import urlsplit
import mcp.types as types
from PIL import Image, ImageChops, ImageDraw, ImageStat
try:
    from aw_core import (
    _sources_for_card,
    )
except ImportError:
    from mcp.aw_core import (
    _sources_for_card,
    )
try:
    from aw_runtime import (
    SCREENSHOT_DIR,
    _activate_new_page,
    _ensure_page_runtime,
    _evaluate,
    _evaluate_query_retry,
    _has_new_page,
    _known_page_tokens,
    _ok,
    _page_signal_snapshot,
    _record_route_memory,
    _resolve_id,
    _result_payload,
    _task_enqueue_actions,
    _task_mark_queue,
    _verify_action_precondition,
    _wait_world_ready,
    _world,
    )
except ImportError:
    from mcp.aw_runtime import (
    SCREENSHOT_DIR,
    _activate_new_page,
    _ensure_page_runtime,
    _evaluate,
    _evaluate_query_retry,
    _has_new_page,
    _known_page_tokens,
    _ok,
    _page_signal_snapshot,
    _record_route_memory,
    _resolve_id,
    _result_payload,
    _task_enqueue_actions,
    _task_mark_queue,
    _verify_action_precondition,
    _wait_world_ready,
    _world,
    )
try:
    from aw_timeline import (
    _record_action_evidence,
    )
except ImportError:
    from mcp.aw_timeline import (
    _record_action_evidence,
    )
try:
    from aw_outcome import (
    _anomaly_check,
    _click_region_snapshot,
    _errored_card,
    _occlusion_attach,
    _occlusion_probe,
    _outcome_card,
    _region_snapshot_at,
    _wait_click_effect,
    )
except ImportError:
    from mcp.aw_outcome import (
    _anomaly_check,
    _click_region_snapshot,
    _errored_card,
    _occlusion_attach,
    _occlusion_probe,
    _outcome_card,
    _region_snapshot_at,
    _wait_click_effect,
    )
try:
    from aw_taskgraph import (
    _contract_gate,
    )
except ImportError:
    from mcp.aw_taskgraph import (
    _contract_gate,
    )


def _build_locator(w, ent):
    """根据原生网页世界元素信息构建 Playwright locator(行动层整合)。
    优先级:页面原生 id > placeholder 属性 > ARIA role+可访问名 > 文本。找不到返回 None。
    """
    page = w["page"]
    attrs = ent.get("attributes") or {}
    text = (ent.get("text") or "").strip()
    semantic = ent.get("semantic") or ""
    acc_name = (attrs.get("ariaLabel") or "").strip() or (attrs.get("placeholder") or "").strip() or text

    def _count(loc):
        try:
            return loc.count()
        except Exception:
            return 0

    # 1. 页面原生 id(精确唯一;多匹配时优先可见的那个——SPA 常保留隐藏副本)
    if attrs.get("id"):
        loc = page.locator(f'[id="{attrs["id"]}"]')
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 2. placeholder 属性(输入框常见)
    if attrs.get("placeholder"):
        loc = page.locator(f'[placeholder="{attrs["placeholder"]}"]')
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 3. ARIA role + 可访问名(Playwright 语义定位)
    pw_roles = {
        "button": "button", "link": "link", "input": "textbox",
        "combobox": "combobox", "listbox": "listbox", "option": "option",
        "tab": "tab", "tablist": "tablist", "heading": "heading",
        "navigation": "navigation", "search": "searchbox", "dialog": "dialog",
    }
    if semantic in pw_roles and acc_name and len(acc_name) <= 80:
        loc = page.get_by_role(pw_roles[semantic], name=acc_name, exact=False)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    # 3a. 已知卡片样式时锁定顶层容器。仅用 role+长文本可能在动态站点中
    # 命中重叠的可访问子树；card-shine 是 DOM 的结构事实，适合优先定位外层卡。
    class_name = str(attrs.get("className") or "")
    if semantic == "link" and "card-shine" in class_name and text:
        try:
            title_hint = text[:60]
            loc = page.locator('[role="link"].card-shine').filter(has_text=title_hint)
            if _count(loc) == 1:
                return loc
            if _count(loc) > 1:
                loc_vis = loc.filter(visible=True)
                if _count(loc_vis) == 1:
                    return loc_vis
        except Exception:
            pass
    # 3b. 长文本语义定位:聚合站常用 role=link 的整张卡片承载点击，
    # 但卡片文本超过 Playwright accessible-name 的短文本限制且没有 href。
    # 用卡片自身的可见文本过滤 role，避免动态列表重排后退回易漂移的坐标点击。
    if semantic in pw_roles and text:
        text_hint = text[:80]
        try:
            loc = page.get_by_role(pw_roles[semantic]).filter(has_text=text_hint)
            if _count(loc) == 1:
                return loc
            if _count(loc) > 1:
                loc_vis = loc.filter(visible=True)
                if _count(loc_vis) == 1:
                    return loc_vis
        except Exception:
            pass
    # 4. 文本唯一匹配(短文本)
    if text and 1 <= len(text) <= 50:
        loc = page.get_by_text(text, exact=True)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
        loc = page.get_by_text(text, exact=False)
        if _count(loc) == 1:
            return loc
        elif _count(loc) > 1:
            loc_vis = loc.filter(visible=True)
            if _count(loc_vis) == 1:
                return loc_vis
    return None


def _click_locator_nowait(locator, timeout=10000):
    """发出点击但不等待目标页面完成导航。

    外部转链站点可能加载很慢；点击是否发出与外部页面何时稳定是两个不同
    的事实。优先使用 Playwright 的 no_wait_after，旧版本不支持时兼容回退。
    """
    try:
        locator.click(timeout=timeout, no_wait_after=True)
    except TypeError:
        locator.click(timeout=timeout)


def _refresh_core_status(wid, settle_ms=300):
    """操作后等防抖+渲染,主动刷新内核状态(状态卡反映操作结果)

    导航竞态保护:点击触发导航后旧文档执行上下文随即销毁,此时 evaluate
    会阻塞等待新文档(实测 GitHub 导航最长阻塞 ~30s,动作反馈被拖慢)。
    刷新旧文档本身无意义——3s 短超时,失败即放弃,后果卡由 URL 事实判定。
    """
    w = _world(wid)
    time.sleep(settle_ms / 1000)
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }", timeout=3000)
    except Exception:
        pass


def _fill_visible(wid, text):
    """验证目标文本是否已落入页面某个"可见且未被覆盖"的输入框。

    背景:SPA 对话框(如 Google Flights)点击输入框后会新建一个可见输入框副本,
    原输入框被覆盖。Playwright locator.fill() 只要求元素可见可编辑、不检测遮挡,
    会把值填进被覆盖的旧框而"静默成功"。此验证用 elementFromPoint 排除被覆盖框。
    """
    try:
        ok = _evaluate(
            wid,
            """(text) => {
                const nodes = [...document.querySelectorAll('input, textarea, [contenteditable="true"]')].filter(n => {
                    const r = n.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) return false;
                    const s = getComputedStyle(n);
                    if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) === 0) return false;
                    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                    const top = document.elementFromPoint(cx, cy);
                    // 顶层是自己或包含自己 = 未被覆盖
                    return top === n || n.contains(top) || (top && top.contains(n));
                });
                return nodes.some(n => ((n.value || '') + ' ' + (n.textContent || '')).includes(text));
            }""",
            text,
        )
        return bool(ok)
    except Exception:
        return False


def _t_world_click(args, before_signal=None):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")

    # 视觉证据开关:显式要求时才截前后帧(视觉 diff 兜底),避免每次操作的开销
    visual_evidence = bool(args.get("visual_evidence", False))

    # 点击前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    url_before = before_signal["url"]

    # 遮挡归因:检查元素中心点是否被上层弹窗/遮罩层挡住(结构化 covered_by/at/action,不改变点击行为)
    occl_probe = _occlusion_probe(wid, target_id=target)

    attrs = ent.get("attributes") or {}
    href = str(attrs.get("href") or "")
    # 外部转链入口已经有真实 href 时，直接发起导航比在可能已换文档的
    # 转链页上等待 locator.click 更可靠；wait_until=commit 只等导航开始，
    # 后续结果仍由统一后果卡和 URL 事实确认。
    try:
        parsed_href = urlsplit(href)
    except Exception:
        parsed_href = None
    if (ent.get("semantic") == "link" and parsed_href and parsed_href.scheme in ("http", "https")
            and parsed_href.netloc and "nodebits.xyz" not in parsed_href.netloc):
        try:
            w["page"].goto(href, wait_until="commit", timeout=5000)
        except Exception:
            # 外部站点可能连接超时，但 URL/导航请求已经发出；交给后果卡
            # 判断当前页面事实，不再回退到旧页的重复点击。
            pass
        ret = {"world_id": wid, "clicked": target, "method": "external-navigation", "href": href}
        _occlusion_attach(ret, occl_probe)
        effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1200)
        if effect:
            ret["effect"] = effect
        return _outcome_card(wid, "world_click", args, ret, before_signal)
    card_like = (ent.get("semantic") == "link" and not attrs.get("href")
                 and "card-shine" in str(attrs.get("className") or ""))
    if card_like:
        baseline = args.get("_before_page_tokens") or _known_page_tokens(wid)
        # 动态聚合页可能先把卡片绘制出来，再绑定 React 点击事件；只在
        # 动作前状态仍为 loading 时短等，避免“看得见但点不动”。普通稳定页
        # 不增加等待，且超过上限仍继续一次点击并由后果卡判定。
        if (before_signal or {}).get("state") == "loading":
            ready_deadline = time.time() + 1.2
            while time.time() < ready_deadline:
                try:
                    if _page_signal_snapshot(wid).get("state") == "stable":
                        break
                except Exception:
                    pass
                time.sleep(0.1)
        # 动态列表会在查询与点击之间重绘；先按当前文本重新解析顶层卡片，
        # 避免直接使用已经脱离文档的旧 DOM 引用。
        card_loc = _build_locator(w, ent)
        if card_loc:
            try:
                # 使用真实鼠标点击让前端事件委托有机会完成绑定；locator 会在
                # 动作时重新解析当前节点，且本分支只触发一次点击。
                _click_locator_nowait(card_loc, timeout=5000)
                _refresh_core_status(wid)
                card_ret = {"world_id": wid, "clicked": target, "method": "card-locator"}
                _occlusion_attach(card_ret, occl_probe)
                card_effect = _wait_click_effect(wid, snap_before, url_before)
                if card_effect:
                    card_ret["effect"] = card_effect
                card_outcome = _outcome_card(wid, "world_click", args, card_ret, before_signal)
                # 定位点击已成功发出后，无论证据窗是否立即看到跳转，都不再
                # 用旧 DOM 进行第二次点击；否则动态转链可能打开两个店铺页。
                return card_outcome
            except Exception as exc:
                # 点击期间页面可能已经成功打开新标签页，但后续的旧页证据
                # 读取因重绘/导航抛错。只要检测到新页或 URL 已变化，就不要
                # 再拿旧卡片编号走鼠标兜底；直接用统一出口收口为导航结果。
                try:
                    navigated = _has_new_page(wid, baseline) or (w["page"].url != url_before)
                except Exception:
                    navigated = False
                if navigated:
                    ret = {"world_id": wid, "clicked": target, "method": "card-locator", "warning": str(exc)[:200]}
                    try:
                        return _outcome_card(wid, "world_click", args, ret, before_signal)
                    except Exception:
                        pass
        # 这类卡片没有可访问网址，且动态重绘会让坐标/长文本 locator 漂移；
        # 直接对运行时已确认的外层 DOM 节点触发 React 点击处理器。
        # 仅限无 href 的 card-shine，普通按钮/链接仍走下方 Playwright 路径。
        try:
            dom_clicked = _evaluate(wid, """(id) => {
                const e = agentWorld._runtime.world.elements.get(id);
                if (!e || !e._el || !e._el.isConnected) return false;
                e._el.click();
                return true;
            }""", target)
            if not dom_clicked:
                raise RuntimeError("卡片已被动态重绘，旧构件编号失效")
            _refresh_core_status(wid)
            ret = {"world_id": wid, "clicked": target, "method": "dom-card-click"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before)
            if effect:
                ret["effect"] = effect
            # DOM 点击已经真实发出后，不再退回 locator/鼠标再次点击：动态转链
            # 可能只是尚未把新页暴露给 Playwright，重复点击会造成双开、长等待，
            # 甚至把第二家店的结果误归因给第一家。统一交给后果卡判定；若暂时
            # 没有跳转，返回 unchanged/uncertain，由上层停止并重新探索。
            return _outcome_card(wid, "world_click", args, ret, before_signal)
        except Exception:
            # DOM click 失败时继续走原有安全降级。
            pass

    loc = _build_locator(w, ent)
    if loc:
        try:
            # Playwright locator:自动等待可见/稳定/可点击,错误信息清晰
            _click_locator_nowait(loc, timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "clicked": target, "method": "locator"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before)
            if effect:
                ret["effect"] = effect
            return _outcome_card(wid, "world_click", args, ret, before_signal)
        except Exception as e:
            loc_err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        loc_err = "no-locator"
    # 兜底:坐标鼠标手势(原生网页世界实时 rect + scrollIntoView)
    rect = _evaluate(
        wid,
        """(id) => {
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return null;
            el._el.scrollIntoView({ block: 'center', inline: 'center' });
            const r = el._el.getBoundingClientRect();
            return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
        }""",
        target,
    )
    if not rect or rect["w"] <= 0 or rect["h"] <= 0:
        raise ValueError(f"构件不可见或不存在: {args['id']} (locator: {loc_err})")
    cx = rect["x"] + rect["w"] // 2
    cy = rect["y"] + rect["h"] // 2
    w["page"].mouse.move(cx, cy)
    w["page"].mouse.down()
    w["page"].mouse.up()
    _refresh_core_status(wid)
    ret = {"world_id": wid, "clicked": target, "method": "mouse-gesture", "at": [cx, cy], "locator_note": loc_err}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_click", args, ret, before_signal)


def _t_world_fill(args, before_signal=None):
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    text = args["text"]
    type_delay_ms = int(args.get("type_delay_ms", 0))
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    visual_evidence = bool(args.get("visual_evidence", False))
    # 填表前:冻结目标空间区域(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    url_before = w["page"].url
    # 遮挡归因(Phase 3):填表目标被上层元素挡住时结构化报告(不改变行为)
    occl_probe = _occlusion_probe(wid, target_id=target)
    loc = _build_locator(w, ent)
    if loc:
        try:
            if type_delay_ms > 0:
                # 逐字打字:模拟真实键盘输入,触发受控组件/自动联想下拉
                # 缺陷修复(2026-09-02 弱模型验证):press_sequentially 不清空现有值,
                # 二次输入会追加污染("ap"→"apapple")。先 fill("") 清空(React 兼容),
                # 再逐字打字——等价于真人"先清空再输入"。
                try:
                    loc.fill("", timeout=5000)
                except Exception:
                    pass  # 元素本身为空或 fill 清空失败时继续(打字仍可追加)
                loc.press_sequentially(text, delay=type_delay_ms, timeout=10000)
            else:
                # Playwright fill:自动等待 + React 兼容输入 + 清晰错误
                loc.fill(text, timeout=10000)
            # 关键验证:locator 不检测遮挡,SPA 会把值填进被覆盖的旧输入框而"静默成功"。
            # 未在可见输入框验证到文本 → 判定失败,降级到 js-setter(自带覆盖层切换)。
            filled_ok = _fill_visible(wid, text)
            if filled_ok:
                _refresh_core_status(wid)
                method = "locator-sequential-type" if type_delay_ms > 0 else "locator-fill"
                ret = {"world_id": wid, "filled": target, "text": text, "method": method}
                _occlusion_attach(ret, occl_probe)
                effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=True)
                if effect:
                    ret["effect"] = effect
                return _outcome_card(wid, "world_fill", args, ret, before_signal)
            fill_err = "fill 后未在可见输入框验证到文本(可能被 SPA 覆盖层拦截)"
        except Exception as e:
            fill_err = f"{type(e).__name__}: {str(e)[:200]}"
    else:
        fill_err = "no-locator"
    # 兜底:JS setter(React 受控组件 + 覆盖层自动切换)
    r = _evaluate(
        wid,
        """(args) => {
            const id = args.id, text = args.text;
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return { ok: false, reason: 'not-found' };
            let node = el._el;
            if (node.tagName !== 'INPUT' && node.tagName !== 'TEXTAREA') {
                node = node.querySelector('input, textarea, [contenteditable="true"]');
                if (!node) return { ok: false, reason: 'no-fillable-child', tag: el._el.tagName };
            }
            // 若目标被上层元素覆盖(如 SPA 的激活态输入框副本),切换到实际可见层
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                const cx = rect.x + rect.width / 2, cy = rect.y + rect.height / 2;
                const top = document.elementFromPoint(cx, cy);
                if (top && top !== node && !node.contains(top)) {
                    const topFill = (top.tagName === 'INPUT' || top.tagName === 'TEXTAREA')
                        ? top : top.querySelector('input, textarea, [contenteditable="true"]');
                    if (topFill) node = topFill;
                }
            }
            node.focus();
            const proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(node, text);
            node.dispatchEvent(new Event('input', { bubbles: true }));
            node.dispatchEvent(new Event('change', { bubbles: true }));
            return { ok: true, tag: node.tagName };
        }""",
        {"id": target, "text": text},
    )
    if not r.get("ok"):
        raise ValueError(f"fill 失败: {r} (locator: {fill_err})")
    _refresh_core_status(wid)
    ret = {"world_id": wid, "filled": target, "text": text, "target_tag": r.get("tag"), "method": "js-setter", "locator_note": fill_err}
    _occlusion_attach(ret, occl_probe)
    # js-setter 兜底路径同样验证"值是否进入可见输入框"作为生效证据
    filled_ok = _fill_visible(wid, text)
    effect = _wait_click_effect(wid, snap_before, url_before, max_wait_ms=1500, fill_verified=filled_ok)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_fill", args, ret, before_signal)


def _t_world_batch_fill(args, before_signal=None):
    """批量填入表单字段:单次 MCP 往返完成多个输入框填写。
    逐字段容错:单个字段失败记录 error 并继续,不中断整个批次。
    返回:旧结构(batch_count/ok_count/results)+ 统一后果卡(聚合判定)。
    """
    wid = args["world_id"]
    fields = args.get("fields") or []
    if not fields:
        raise ValueError("fields 列表不能为空")
    results = []
    for f in fields:
        fid = f.get("id")
        if not fid:
            results.append({"id": None, "ok": False, "error": "缺少 id"})
            continue
        try:
            sub_args = {
                "world_id": wid,
                "id": fid,
                "text": f.get("text", ""),
                "type_delay_ms": int(f.get("type_delay_ms", 0)),
            }
            res = _t_world_fill(sub_args)
            if res and res[0].type == "text":
                data = json.loads(res[0].text)
                results.append({"id": fid, "target": data.get("filled"), "method": data.get("method"), "ok": True})
        except Exception as e:
            results.append({"id": fid, "ok": False, "error": f"{type(e).__name__}: {str(e)[:150]}"})
    _refresh_core_status(wid)
    ok_count = sum(1 for r in results if r.get("ok"))
    ret = {"world_id": wid, "batch_count": len(results), "ok_count": ok_count, "results": results}

    # 统一后果卡(聚合判定):全过→progressed;部分过→uncertain;全败→errored
    w = _world(wid)
    try:
        before = before_signal or _page_signal_snapshot(wid)
    except Exception:
        before = {}
    try:
        after = _page_signal_snapshot(wid)
    except Exception:
        after = {}
    if ok_count == len(results) and len(results) > 0:
        po, conf, wh = "progressed", "high", f"批量填入 {ok_count}/{len(results)} 字段全部成功"
    elif ok_count > 0:
        po, conf, wh = "uncertain", "medium", f"批量填入部分成功({ok_count}/{len(results)} 个字段)"
    else:
        po, conf, wh = "errored", "high", "批量填入全部失败"
    ret.update({
        "channel": "outcome",
        "page_outcome": po,
        "situation": {"type": "form" if po == "progressed" else "none", "to_url": None},
        "confidence": conf,
        "why": wh,
        "target": {"id": [f.get("id") for f in fields], "name": None, "fingerprint": None},
        "action": {"kind": "batch_fill", "via": "self"},
        "effect": {"verdict": "effected" if po == "progressed" else "no-change", "observed": [], "region_changed": {"new": 0, "gone": 0}},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": bool(before.get("url") and before.get("url") != (after.get("url") or before.get("url"))),
            "state": after.get("state", "unknown"),
            "anomaly": False,
        },
        "overlays": {"new": [], "gone": []},
        "sources": {},
        "next": {"guide_stale": False, "suggested": None, "candidates": []},
        "evidence_seq": int(w.get("evidence_seq", 0)) + 1,
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
    })
    # P2a/P2b 同口径(本卡绕过 _outcome_card,在此补齐):异常真信号 + 来源标记填实。
    try:
        ret["page"]["anomaly"] = _anomaly_check(int(wid))
    except Exception:
        pass
    try:
        ret["sources"] = _sources_for_card(ret)
    except Exception:
        pass
    return _ok(ret)


def _t_world_press(args, before_signal=None):
    """按编号聚焦并按按键(如 Enter/Escape/Tab)。返回 effect 生效报告 + 统一后果卡。"""
    wid = args["world_id"]
    target = _resolve_id(wid, args["id"])
    key = args["key"]
    w = _world(wid)
    ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
    if not ent:
        raise ValueError(f"构件不存在: {args['id']}")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    visual_evidence = bool(args.get("visual_evidence", False))
    # 按键前:冻结目标空间区域 + URL(生效报告的证据基线)
    snap_before = _click_region_snapshot(wid, target, capture_frame=visual_evidence)
    url_before = w["page"].url
    # 按 key 决定是否开启"弹窗消失"证据(Escape 关弹窗/菜单 = 生效)
    disappear_ok = key.lower() in ("escape", "esc")
    # 遮挡归因(Phase 3):按键目标被上层元素挡住时结构化报告
    occl_probe = _occlusion_probe(wid, target_id=target)
    loc = _build_locator(w, ent)
    if loc:
        try:
            loc.press(key, timeout=10000)
            _refresh_core_status(wid)
            ret = {"world_id": wid, "pressed": target, "key": key, "method": "locator-press"}
            _occlusion_attach(ret, occl_probe)
            effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
            if effect:
                ret["effect"] = effect
            return _outcome_card(wid, "world_press", args, ret, before_signal)
        except Exception as e:
            raise ValueError(f"按键失败: {type(e).__name__}: {str(e)[:200]}")
    # 兜底:JS focus + dispatch keydown/keyup + 真实键盘事件(比纯 JS dispatch 更可靠)
    ok = _evaluate(
        wid,
        """(args) => {
            const el = agentWorld._runtime.world.elements.get(args.id);
            if (!el) return false;
            let node = el._el;
            if (node.tagName !== 'INPUT' && node.tagName !== 'TEXTAREA') {
                const child = node.querySelector('input, textarea, [contenteditable="true"]');
                if (child) node = child;
            }
            node.focus();
            for (const t of ['keydown', 'keyup']) {
                node.dispatchEvent(new KeyboardEvent(t, { key: args.key, bubbles: true }));
            }
            return true;
        }""",
        {"id": target, "key": key},
    )
    if not ok:
        raise ValueError(f"构件不存在: {args['id']}")
    try:
        w["page"].keyboard.press(key)
    except Exception:
        pass
    _refresh_core_status(wid)
    ret = {"world_id": wid, "pressed": target, "key": key, "method": "native-keyboard"}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before, disappear_ok=disappear_ok)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_press", args, ret, before_signal)


def _t_world_wait(args):
    wid = args["world_id"]
    mode = args["mode"]
    timeout_ms = int(args.get("timeout_ms", 30000))
    f = {}
    if args.get("role"):
        f["role"] = args["role"]
    if args.get("text"):
        f["text"] = args["text"]
    if args.get("name"):
        f["name"] = args["name"]
    w = _world(wid)
    try:
        _activate_new_page(wid, poll_ms=0)
    except Exception:
        pass
    page = w["page"]
    _ensure_page_runtime(page)
    # 事件驱动(替代 0.3s 轮询):内核 waitFor 注册 waiter,
    # MutationObserver flush 命中条件即 resolve;超时由内核 setTimeout 兜底。
    # Playwright evaluate 自动 await Promise;临时放大 page 默认超时,避免
    # timeout_ms > 30s 时被 Playwright 先掐断。
    prev_timeout = 30000
    try:
        page.set_default_timeout(timeout_ms + 3000)
        result = page.evaluate(
            "(a) => agentWorld._runtime.waitFor(a.filter, a.mode, a.timeout_ms)",
            {"filter": f, "mode": mode, "timeout_ms": timeout_ms},
        )
    except Exception as e:
        result = {"matched": False, "mode": mode, "timeout_ms": timeout_ms, "error": str(e)[:120]}
    finally:
        page.set_default_timeout(prev_timeout)
    out = {"world_id": wid, "matched": bool(result.get("matched")), "mode": mode, "filter": f}
    if result.get("matched"):
        out["count"] = result.get("count", 0)
        out["driven"] = "event"
    else:
        out["timeout_ms"] = timeout_ms
        out["driven"] = "timeout"
    return _ok(out)


def _t_world_screenshot(args):
    wid = args["world_id"]
    w = _world(wid)
    annotated = bool(args.get("annotated", False))
    return_base64 = bool(args.get("return_base64", True))
    path = SCREENSHOT_DIR / f"world{wid}_{int(time.time())}.png"
    
    if args.get("id"):
        target = _resolve_id(wid, args["id"])
        ent = _evaluate(wid, "(id) => agentWorld.query.getEntity(id)", target)
        box = ent["bounds"]
        w["page"].screenshot(path=str(path), clip={"x": box["x"], "y": box["y"], "width": box["w"], "height": box["h"]})
        desc = f"构件 {target} ({ent['name']})"
    elif annotated:
        # Set-of-Mark 模式: 截取视口并在可交互构件上绘制半透明编号标注框
        raw_path = SCREENSHOT_DIR / f"raw_world{wid}_{int(time.time())}.png"
        w["page"].screenshot(path=str(raw_path), full_page=False)
        ents = _evaluate(wid, "(f) => agentWorld.query.findEntities(f)", {"interactive": True, "inViewport": True}) or []
        
        img = Image.open(raw_path).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        
        marked = 0
        for ent in ents:
            box = ent.get("bounds", {})
            x, y, bw, bh = box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)
            if bw <= 4 or bh <= 4:
                continue
            eid = ent.get("id", "")
            ename = ent.get("name", "")[:14]
            draw.rectangle([x, y, x + bw, y + bh], outline=(255, 20, 100, 240), width=2)
            label = f"[{eid}] {ename}"
            tag_w = max(36, len(label) * 7 + 6)
            draw.rectangle([x, max(0, y - 16), x + tag_w, max(16, y)], fill=(255, 20, 100, 210))
            draw.text((x + 3, max(0, y - 15)), label, fill=(255, 255, 255, 255))
            marked += 1
            
        combined = Image.alpha_composite(img, overlay).convert("RGB")
        combined.save(path, "PNG")
        desc = f"Set-of-Mark 视口标注图 (标记 {marked} 个可交互构件)"
    else:
        w["page"].screenshot(path=str(path), full_page=True)
        desc = "整页"

    ret_dict = {"world_id": wid, "target": desc, "path": str(path)}
    contents = [types.TextContent(type="text", text=json.dumps(ret_dict, ensure_ascii=False, indent=2))]
    if return_base64 and path.exists():
        with open(path, "rb") as f:
            b64_str = base64.b64encode(f.read()).decode("utf-8")
        contents.append(types.ImageContent(type="image", data=b64_str, mimeType="image/png"))
    return contents


def _t_world_eval(args):
    """世界内 JS 执行(调试/特殊查询,结果截断保护)"""
    wid = args["world_id"]
    expr = args["expression"]
    w = _world(wid)
    # CDP 会话安全闸门:world_eval 是任意 JS,可绕过 visibility 过滤层直接读整页文本,
    # 在 CDP 连接的用户浏览器会话中可能触达登录态/凭据。IPI 攻防实测确认该后门存在,
    # 故 CDP 会话下禁用 world_eval,强制走结构化查询(world_entities/world_entity)。
    if w.get("cdp_url"):
        raise ValueError("world_eval 在 CDP 会话中已禁用(安全边界:任意 JS 可绕过过滤层触达登录会话/隐藏内容);请改用 world_entities/world_entity 结构化查询")
    try:
        result = _evaluate_query_retry(wid, expr, attempts=3)
    except Exception as e:
        # 外部店铺刚导航时 document/body 可能尚未建立；返回统一 pending，
        # 让调用方按 retry_after_ms 重试，而不是收到无法解析的错误文本。
        message = str(e)
        if ("Cannot read properties of null" in message or "Execution context" in message
                or "agentWorld" in message or "Target page, context or browser has been closed" in message):
            try:
                current_url = w["page"].url[:300]
            except Exception:
                current_url = ""
            return _ok({"world_id": wid, "pending": True, "retry_after_ms": 500,
                        "url": current_url, "reason": "页面正在加载，暂时无法读取脚本结果",
                        "error_type": type(e).__name__})
        raise ValueError(f"evaluate 失败: {type(e).__name__}: {str(e)[:200]}")
    try:
        text = json.dumps(result, ensure_ascii=False, default=str)
    except Exception:
        text = str(result)
    if len(text) > 8000:
        text = text[:8000] + f"...(截断,共 {len(text)} 字符)"
    return _ok({"world_id": wid, "result": text})


def _t_world_click_at(args, before_signal=None):
    """视口坐标点击(原生网页世界外元素兜底,坐标来自截图/视觉)。
    以坐标为中心拍区域证据基线,返回统一后果卡。"""
    wid = args["world_id"]
    x = int(args["x"])
    y = int(args["y"])
    w = _world(wid)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    snap_before = _region_snapshot_at(wid, x, y)
    url_before = w["page"].url
    # 遮挡归因(Phase 3):坐标模式——报告命中点顶层元素,遮罩类(role=dialog/backdrop)标 covered
    occl_probe = _occlusion_probe(wid, x=x, y=y)
    w["page"].mouse.click(x, y)
    _refresh_core_status(wid)
    ret = {"world_id": wid, "clicked_at": [x, y], "method": "mouse-coords"}
    _occlusion_attach(ret, occl_probe)
    effect = _wait_click_effect(wid, snap_before, url_before)
    if effect:
        ret["effect"] = effect
    return _outcome_card(wid, "world_click_at", args, ret, before_signal)


def _t_world_navigate(args, before_signal=None):
    """世界内导航(无需关闭重开)。返回统一后果卡(navigation)。
    导航后旧 el_N 编号全部失效:target.id 恒为 null,world_epoch +1。"""
    wid = args["world_id"]
    url = args["url"]
    wait_ms = int(args.get("wait_ms", 2000))
    w = _world(wid)
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = {}
    w["page"].goto(url, wait_until="domcontentloaded", timeout=60000)
    _wait_world_ready(w["page"])
    if wait_ms:
        w["page"].wait_for_timeout(wait_ms)
    w["epoch"] = int(w.get("epoch", 0)) + 1
    summary = _evaluate(wid, "agentWorld.query.getPageSummary()")
    ret = {"world_id": wid, "url": url, "summary": summary}
    return _outcome_card(wid, "world_navigate", args, ret, before_signal)


# ── 阶段 B 收口:默认协议 3 新工具 ─────────────────────────────
# world_find → 定位构件;world_act → 唯一行动入口(含聚合 steps);
# world_outcome → 幂等读最近一张后果卡。全部复用既有内核与 _outcome_card,不另起炉灶。


ACT_DISPATCH = {
    "click": ("world_click", _t_world_click),
    "fill": ("world_fill", _t_world_fill),
    "press": ("world_press", _t_world_press),
    "batch_fill": ("world_batch_fill", _t_world_batch_fill),
}


def _act_one(wid, step, before_signal):
    """把 world_act 的一个动作步骤分发到既有动作实现(复用统一后果卡)。"""
    kind = step.get("kind")
    if kind not in ACT_DISPATCH:
        raise ValueError(f"world_act 不支持的 kind: {kind!r}(支持 click/fill/press/batch_fill)")
    inner_name, handler = ACT_DISPATCH[kind]
    sub_args = {k: v for k, v in step.items() if k != "kind"}
    sub_args["world_id"] = wid
    result = handler(sub_args, before_signal)
    # 证据记录与既有动作一致(inner_name 入库,保持证据信道语义不变)
    if before_signal is not None:
        try:
            _record_action_evidence(int(wid), inner_name, sub_args, before_signal, result)
        except Exception:
            pass
    return result


def _t_world_act(args, before_signal=None):
    """默认协议:唯一行动入口。kind=click|fill|press|batch_fill → 统一后果卡。

    steps 数组 = 聚合执行(等价 RFC 的 world_run):单个 MCP 往返内顺序执行多个动作,
    每步都走 _outcome_card 同一出口;任一步 errored 即停止。返回最后一步的卡 + steps 明细。
    """
    wid = args["world_id"]
    steps = args.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise ValueError("world_act 的 steps 必须是非空列表")
        if len(steps) > 20:
            raise ValueError("连续动作最多 20 步")
        queued = _task_enqueue_actions(wid, steps)
        cards = []
        for idx, step in enumerate(steps):
            # 聚合动作的任务身份沿用外层；每一步仍可单独声明 operation，避免把
            # “填写”和“提交”错误合并成一个业务操作。
            step = dict(step)
            for metadata_key in ("task_id", "executor"):
                if metadata_key not in step and args.get(metadata_key) is not None:
                    step[metadata_key] = args[metadata_key]
            step["_action_id"] = queued[idx]["action_id"]
            step["_before_page_tokens"] = list(_known_page_tokens(wid))
            _task_mark_queue(wid, idx, "executing", started_at=int(time.time() * 1000))
            try:
                before = _page_signal_snapshot(wid)
            except Exception:
                before = None
            try:
                blocked = _contract_gate(wid, step, before)
                if blocked is not None:
                    card = _result_payload(blocked)
                    cards.append(card)
                    break
                _verify_action_precondition(wid, step)
                res = _act_one(wid, step, before)
                card = _result_payload(res)
            except Exception as e:
                card = _result_payload(_errored_card(wid, f"world_act.step{idx + 1}", step, before, e))
            cards.append(card)
            _record_route_memory(wid, f"world_{step.get('kind', 'act')}", step, before, card)
            expected = step.get("expected_outcome")
            if expected and card.get("page_outcome") != expected:
                card["expected_outcome_mismatch"] = {"expected": expected, "actual": card.get("page_outcome")}
                _task_mark_queue(wid, idx, "stopped", finished_at=int(time.time() * 1000),
                                 page_outcome=card.get("page_outcome"), expected_outcome_mismatch=True)
                break
            # 连续任务的安全闸门：只有实时确认 progressed 才允许进入下一步。
            # unchanged/uncertain/pending/challenged 都意味着当前页面事实尚未
            # 可靠确认，继续写操作可能把上一页结果误归因给当前目标。
            if card.get("page_outcome") != "progressed":
                _task_mark_queue(wid, idx, "stopped", finished_at=int(time.time() * 1000),
                                 page_outcome=card.get("page_outcome"))
                break
            _task_mark_queue(wid, idx, "done", finished_at=int(time.time() * 1000), page_outcome=card.get("page_outcome"))
        last = dict(cards[-1])
        last["steps"] = cards
        last["step_count"] = len(cards)
        last["action"] = {"kind": "act-sequence", "via": "self"}
        last["channel"] = "outcome"
        # P0-1:整单语义。主标签 = 末步卡(任一步 errored 即停,故 errored 必为末步);
        # 另附聚合记账,长任务 FP/FN 以此为准,不再只看末步。
        _outcomes = [c.get("page_outcome") for c in cards]
        _first_bad = next((i for i, o in enumerate(_outcomes) if o != "progressed"), None)
        _seqs = [c.get("evidence_seq") for c in cards
                 if isinstance(c.get("evidence_seq"), int)]
        last["step_outcomes"] = _outcomes
        last["all_progressed"] = _first_bad is None
        last["first_failure_idx"] = _first_bad
        if _seqs:
            last["seq_range"] = {"first": _seqs[0], "last": _seqs[-1]}
        return _ok(last)

    kind = args.get("kind") or "click"
    if kind not in ACT_DISPATCH:
        raise ValueError(f"world_act 不支持的 kind: {kind!r}(支持 click/fill/press/batch_fill)")
    if before_signal is None:
        try:
            before_signal = _page_signal_snapshot(wid)
        except Exception:
            before_signal = None
    try:
        blocked = _contract_gate(wid, args, before_signal)
        if blocked is not None:
            return blocked
        _verify_action_precondition(wid, args)
        return _act_one(wid, args, before_signal)
    except Exception as e:
        return _errored_card(wid, f"world_act({kind})", args, before_signal, e)
