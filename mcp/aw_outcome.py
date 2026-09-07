# -*- coding: utf-8 -*-
"""统一后果卡簇——page_outcome 五态判定、挑战检测、点击效果(区域快照/样式 diff/视觉阈值)、遮挡归因、错误卡。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖:aw_core/aw_runtime/aw_timeline(aw_core ← aw_runtime ← aw_timeline ← 本簇)。
"""
import json, math, time
from PIL import Image, ImageChops, ImageStat
try:
    from aw_core import (
    STYLE_DIFF_PROPS,
    STYLE_SNAPSHOT_MAX,
    _anomaly_from_counts,
    _build_click_effect,
    _signal_delta,
    _sources_for_card,
    )
except ImportError:
    from mcp.aw_core import (
    STYLE_DIFF_PROPS,
    STYLE_SNAPSHOT_MAX,
    _anomaly_from_counts,
    _build_click_effect,
    _signal_delta,
    _sources_for_card,
    )
try:
    from aw_runtime import (
    SCREENSHOT_DIR,
    VISUAL_RMS_THRESHOLD,
    _activate_new_page,
    _evaluate,
    _ok,
    _page_signal_snapshot,
    _resolve_id,
    _runtime_context,
    _world,
    )
except ImportError:
    from mcp.aw_runtime import (
    SCREENSHOT_DIR,
    VISUAL_RMS_THRESHOLD,
    _activate_new_page,
    _evaluate,
    _ok,
    _page_signal_snapshot,
    _resolve_id,
    _runtime_context,
    _world,
    )
try:
    from aw_timeline import (
    _trace_store,
    )
except ImportError:
    from mcp.aw_timeline import (
    _trace_store,
    )
try:
    from task_runtime import (
    build_trace_entry,
    new_id,
    persistence_enabled,
    )
except ImportError:
    from mcp.task_runtime import (
    build_trace_entry,
    new_id,
    persistence_enabled,
    )
try:
    from business_runtime import (
    attach_business_runtime,
    )
except ImportError:
    from mcp.business_runtime import (
    attach_business_runtime,
    )


def _is_submit_trigger(wid, target_id, key=None):
    """点击/按键目标是否为"疑似提交动作"触发元素(form 关联 / type=submit)。

    page_outcome 的 challenged 检测只在疑似提交动作后启用,避免普通点击
    (点广告/点链接/点装饰)被页面里常驻的 fixed 遮罩 iframe(如支付组件、地图)
    误触挑战判定。对应 Claude 评审的 submit_trigger 精确化建议。
    key 非 None 时仅 Enter 视为提交类按键。
    """
    if key is not None and str(key).lower() not in ("enter",):
        return False
    try:
        res = _evaluate(
            wid,
            """(id) => {
                const el = agentWorld._runtime.world.elements.get(id);
                if (!el || !el._el) return false;
                const n = el._el;
                // 1. 自身是 submit 类型
                const tag = n.tagName.toLowerCase();
                if (tag === 'button' || tag === 'input') {
                    const t = (n.getAttribute('type') || '').toLowerCase();
                    if (t === 'submit') return true;
                }
                // 2. 位于 form 内(button 默认行为=提交)
                if (n.closest && n.closest('form')) return true;
                // 3. input 按 Enter 隐式提交
                if (tag === 'input') return true;
                return false;
            }""",
            target_id,
        )
        return bool(res)
    except Exception:
        return False


def _challenge_detection(wid):
    """结构化挑战检测:新出现的"固定全屏遮罩 + iframe 子元素"。

    特征①(强信号,对应 8.4 评审修正):
      存在 position:fixed(覆盖大部分视口)的容器,且内部有可见 iframe 子元素。
    不依赖任何 CAPTCHA 提供商 URL 白名单(提供商换 CDN/代理/第一方域名);②③
    (sandbox/焦点捕获)不做为 challenged 依据,仅作 uncertain 的辅助证据。
    返回 None(无挑战)或 {type, confidence, evidence[]}。
    """
    try:
        raw = _evaluate(
            wid,
            """() => {
                const vw = window.innerWidth, vh = window.innerHeight;
                const fixed = [];
                // 找所有 fixed 定位容器(排除纯装饰:面积需覆盖大部分视口)
                for (const el of document.querySelectorAll('*')) {
                    const s = getComputedStyle(el);
                    if (s.position !== 'fixed') continue;
                    const r = el.getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area < vw * vh * 0.25) continue;  // 小于 25% 视口不算全屏遮罩
                    // 容器内是否有可见 iframe
                    const ifr = el.querySelector('iframe');
                    if (ifr) {
                        const ir = ifr.getBoundingClientRect();
                        if (ir.width < 50 || ir.height < 50) continue;
                        fixed.push({
                            w: Math.round(r.width), h: Math.round(r.height),
                            ifrW: Math.round(ir.width), ifrH: Math.round(ir.height),
                            ifrSrc: (ifr.src || '').slice(0, 120),
                            bg: (s.backgroundColor || ''),
                        });
                        break;  // 一个就够
                    }
                }
                return JSON.stringify(fixed.slice(0, 2));
            }""",
        )
        data = json.loads(raw) if isinstance(raw, str) else raw
        if data:
            f = data[0]
            return {
                "type": "modal_iframe_challenge",
                "confidence": "medium",
                "evidence": [
                    f"新出现 fixed 全屏遮罩(约 {f['w']}x{f['h']}px)内含 iframe({f['ifrW']}x{f['ifrH']}px)",
                    f"iframe 来源: {f['ifrSrc'] or '(同源/空白)'}",
                ],
            }
        return None
    except Exception:
        return None


def _build_page_outcome(wid, before_signal, after_signal, submit_trigger=False, effect=None,
                        overlays_changed=False, check_errors=False):
    """把动作前后信号 + 局部判定合成统一后果卡主标签(五态,平铺字符串)。

    progressed  effect.verdict ∈ {effected, visual-effected}(导航/弹窗/状态翻转/填表验证/视觉)
    challenged 疑似提交动作后出现 fixed 全屏遮罩+iframe(CAPTCHA/二次验证)→ 停止上报人工
    errored    表单错误信号(role=alert / aria-invalid,仅表单类动作后检测)→ 读错误修正重试
              或网络/控制台静默失败(HTTP 4xx/5xx / console.error)→ 即使 DOM 无变化也能精准归因
    uncertain  effect.verdict == changed(有变化但性质不明)→ 最多补一次 world_state
    uncertain  effect.verdict == unknown(证据管线缺席,生效未知)→ 复核一次,绝不当失败
    unchanged  其余(没有有效变化)→ 按失败路径处理

    设计:合并在 server 端完成,agent 收到的不是四条独立信号而是预合成主标签。
    返回 (page_outcome, situation, confidence, why)。
    """
    # 浏览器级网络错误页不是“已完成导航”。如果只依据 URL 发生变化，
    # chrome-error://chromewebdata/ 会被误报成 progressed，进而让上层继续
    # 读取错误页或沿着错误路线执行。先用页面 URL 事实拦截这一类结果。
    after_url_fact = str((after_signal or {}).get("url") or "")
    if after_url_fact.startswith("chrome-error://"):
        return (
            "errored",
            {"type": "browser_network_error", "to_url": None,
             "errors": [{"url": after_url_fact}]},
            "high",
            f"浏览器打开目标页面失败: {after_url_fact[:160]}",
        )
    url_changed = before_signal.get("url") != after_signal.get("url")
    effect = effect or {}
    verdict = effect.get("verdict")

    # 1. challenged:仅疑似提交动作后启用挑战检测(避免常驻遮罩组件误报)
    if submit_trigger:
        challenge = _challenge_detection(wid)
        if challenge:
            why = "页面被挑战遮罩/验证墙拦截: " + " ".join(challenge.get("evidence", []))[:200]
            return (
                "challenged",
                {"type": challenge.get("type", "challenge"), "to_url": None,
                 "evidence": challenge.get("evidence", [])},
                challenge.get("confidence", "medium"),
                why,
            )

    # 2. errored:表单错误信号(role=alert / aria-invalid,仅表单类动作后检测,
    #    避免普通点击被页面常驻错误提示元素误判)
    if check_errors:
        try:
            err_signal = _evaluate(
                wid,
                """() => {
                    const alerts = [...document.querySelectorAll('[role="alert"], [aria-live="assertive"], .gl-field-error, [aria-invalid="true"]')]
                        .filter(e => {
                            const s = getComputedStyle(e);
                            const r = e.getBoundingClientRect();
                            if (s.display === 'none' || s.visibility === 'hidden') return false;
                            return r.width > 0 && r.height > 0;
                        })
                        .map(e => ({ tag: e.tagName.toLowerCase(), text: (e.textContent || '').trim().slice(0, 80) }))
                        .filter(x => x.text && x.text.length > 1)
                        .slice(0, 5);
                    return JSON.stringify(alerts);
                }""",
            )
            alerts = json.loads(err_signal) if isinstance(err_signal, str) else (err_signal or [])
            if alerts:
                names = "、".join(a.get("text", "")[:40] for a in alerts[:3])
                return (
                    "errored",
                    {"type": "form_validation_error", "to_url": None, "errors": alerts},
                    "high",
                    f"检测到 {len(alerts)} 个错误信号元素: {names}",
                )
        except Exception:
            pass

    # 3. effect 判定(局部判定 + 全局纠正之后)
    if verdict in ("effected", "visual-effected"):
        situation_type = "navigation" if url_changed else \
            ("overlay" if overlays_changed else \
             ("form" if "填表值" in (effect.get("why") or "") else \
              ("state-flip" if "状态变化" in (effect.get("why") or "") else \
               ("visual" if verdict == "visual-effected" or "视觉" in (effect.get("why") or "") else "none"))))
        return (
            "progressed",
            {"type": situation_type, "to_url": after_signal.get("url") if url_changed else None},
            effect.get("confidence") or "high",
            effect.get("why") or "操作已生效",
        )
    if verdict == "changed":
        return (
            "uncertain",
            {"type": "none", "to_url": None},
            effect.get("confidence") or "medium",
            effect.get("why") or "有变化但无法确认是否生效",
        )
    if verdict == "unknown":
        # P1:证据缺席→ uncertain(复核一次),绝不映射为 unchanged(未知不是失败)。
        return (
            "uncertain",
            {"type": "none", "to_url": None},
            effect.get("confidence") or "low",
            effect.get("why") or "证据缺失,生效未知",
        )

    # 4. 静默失败气泡(借鉴 Chrome DevTools MCP):
    #    DOM 无变化 → 但如果动作窗口内捕获到了 HTTP 4xx/5xx 或 console.error,
    #    升级为 errored 并注入真实报错原因,彻底消灭"不明 unchanged"盲目重试死循环。
    try:
        w = _world(wid)
        net_list = w.get("network_errors") or []
        con_list = w.get("console_errors") or []
        before_net = before_signal.get("_net_err_cursor", len(net_list))
        before_con = before_signal.get("_console_err_cursor", len(con_list))
        new_net = net_list[before_net:]
        new_con = con_list[before_con:]

        if new_net:
            err = new_net[0]
            detail = err.get("detail") or ""
            detail_str = detail[:120] if detail else ""
            application_error = err.get("kind") == "application_error"
            why_prefix = "操作引发后端业务错误" if application_error else "操作引发后端接口报错"
            why_str = f"{why_prefix}: HTTP {err['status']} {err.get('url', '')}"
            if detail_str:
                why_str += f" — {detail_str}"
            return (
                "errored",
                {
                    "type": "network_application_error" if application_error else "network_error",
                    "to_url": None,
                    "errors": [{"url": e["url"], "status": e["status"], "kind": e.get("kind"), "detail": (e.get("detail") or "")[:120]}
                               for e in new_net[:5]],
                },
                "high",
                why_str,
            )
        if new_con:
            texts = [c["text"] for c in new_con[:3]]
            return (
                "errored",
                {
                    "type": "console_error",
                    "to_url": None,
                    "errors": [{"text": c["text"]} for c in new_con[:5]],
                },
                "medium",
                f"操作引发前端控制台异常: {texts[0][:120]}",
            )
    except Exception:
        pass

    return (
        "unchanged",
        {"type": "none", "to_url": None},
        effect.get("confidence") or "high",
        effect.get("why") or "未观察到任何生效证据",
    )


def _finalize_click_result(wid, ret, before_signal, after_signal=None):
    """把局部点击结果和页面整体信号合并成最小闭环反馈。

    URL 变化和新弹窗是强证据,即使点击目标附近没有变化,也不能报告 no-change。
    after_signal 可由调用方传入(统一后果卡复用同一份快照,避免重复 evaluate)。
    """
    if after_signal is None:
        after_signal = _page_signal_snapshot(wid)
    url_changed = before_signal.get("url") != after_signal.get("url")
    title_changed = before_signal.get("title") != after_signal.get("title")
    dialog_delta = _signal_delta(before_signal, after_signal, "dialogs")
    menu_delta = _signal_delta(before_signal, after_signal, "menus")
    new_overlays = dialog_delta["new"] + menu_delta["new"]
    gone_overlays = dialog_delta["gone"] + menu_delta["gone"]
    feedback = {
        "source": "global-page-state",
        "page": {
            "before_url": before_signal.get("url"),
            "after_url": after_signal.get("url"),
            "url_changed": url_changed,
            "before_title": before_signal.get("title"),
            "after_title": after_signal.get("title"),
            "title_changed": title_changed,
            "before_state": before_signal.get("state"),
            "after_state": after_signal.get("state"),
        },
        "overlays": {
            "new": new_overlays[:8],
            "gone": gone_overlays[:8],
            "changed": bool(new_overlays or gone_overlays),
        },
        "changes_seq": {
            "before": before_signal.get("changes_seq", 0),
            "after": after_signal.get("changes_seq", 0),
        },
    }
    ret["feedback"] = feedback

    effect = ret.get("effect")
    if effect:
        effect["global"] = {
            "url_changed": url_changed,
            "new_overlays": new_overlays[:8],
            "title_changed": title_changed,
        }

    # 全局页面事实优先于目标局部区域判断,纠正导航/弹窗的误报。
    if url_changed:
        if effect and effect.get("verdict") != "effected":
            effect["local_verdict"] = effect.get("verdict")
            effect["local_why"] = effect.get("why")
        if not effect:
            effect = {"observed": [], "region_changed": {"new": 0, "gone": 0}}
            ret["effect"] = effect
        effect.update({
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面整体发生导航: URL 从 {before_signal.get('url')} 变为 {after_signal.get('url')}",
        })
    elif new_overlays and (not effect or effect.get("verdict") != "effected"):
        if effect:
            effect["local_verdict"] = effect.get("verdict")
            effect["local_why"] = effect.get("why")
        else:
            effect = {"observed": [], "region_changed": {"new": 0, "gone": 0}}
            ret["effect"] = effect
        names = "、".join((x.get("name") or x.get("text") or x.get("id", "")) for x in new_overlays[:4])
        effect.update({
            "verdict": "effected",
            "confidence": "high",
            "why": f"页面整体出现新的弹窗/菜单: {names}",
        })
    return ret


# ── 统一后果卡(阶段 A:所有动作的同一出口)──────────────────────
# page_outcome 五态:progressed | challenged | errored | uncertain | unchanged
# 主标签为平铺字符串(弱模型只读 page_outcome 一个键),卡片其余字段为证据与契约。

# ── 来源标记(F2 安全收口)──────────────────────────────────────
# 每条返回的字段来源四分类(规则写死,不让模型猜):
#   fact       页面客观事实(URL/el id/bounds/aria 状态/changes_seq)
#   evidence   本次动作前后差分证据(observed/verdict/visual_diff_score)
#   inference  服务端/导览推断(guide.candidates/next.suggested/匹配分)
#   untrusted  页面自由文本(text/name/aria-label/placeholder/title/forms.value)——不得当指令


def _anomaly_check(wid):
    """供小票 page.anomaly 的轻量检测:主 frame 可见元素 vs 世界元素数。
    任何失败默认 False(宁可漏报,不误报)。每次动作约 +2 次 evaluate。"""
    try:
        w = _world(int(wid))
        if id(w.get("page")) in (w.get("runtime_pending_tokens") or set()):
            # 新页仍在导航时不做深诊断；动作后果先依据页签/网址事实返回，
            # 避免 anomaly 检测把运行时初始化等待重复叠加到动作延迟。
            return False
        core = _evaluate(int(wid), "() => agentWorld.query.getStatus()") or {}
        page = w["page"]
        target = None
        for f in page.frames:
            try:
                if f.url and not f.url.startswith("about:"):
                    target = f
                    break
            except Exception:
                continue
        if target is None:
            return False
        visible = target.evaluate(
            "[...document.querySelectorAll('*')].filter(e => { const t = e.tagName.toLowerCase(); if (['br','hr','script','style','link','meta','noscript','svg','path','g','defs','use'].includes(t)) return false; const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0 && r.width > 3 && r.height > 3; }).length"
        )
        world_count = (core.get("world") or {}).get("elements", 0)
        return _anomaly_from_counts(visible, world_count)
    except Exception:
        return False


def _outcome_card(wid, action, args, ret, before_signal):
    """所有动作的统一出口:在动作返回上追加统一后果卡(五态 page_outcome)。

    结构 = 旧返回字段(超集,兼容现有客户端)+ 卡片字段。
    主标签(page_outcome/situation/confidence/why)位于字段最前。
    """
    _oc_t0 = None
    w = _world(int(wid))
    try:
        before = before_signal or _page_signal_snapshot(int(wid))
    except Exception:
        before = {}
    # 导航是否已开始(URL 相对动作前已变):导航后旧 el_N 已失效,再解析目标只会
    # evaluate 阻塞等待新文档(实测 GitHub 可拖 ~18s);后果卡由 URL 事实判定。
    _navigated = False
    try:
        _navigated = bool(w["page"].url and before and (w["page"].url != before.get("url")))
    except Exception:
        _navigated = False
    # 在转到新标签页后旧 el_N 不再存在，但后果卡仍应保留目标身份。
    submit_trigger = False
    resolved = None
    ent = None
    target_arg = args.get("id")
    if target_arg and action != "world_navigate" and not _navigated:
        try:
            resolved = _resolve_id(int(wid), target_arg)
        except Exception:
            resolved = None
        if resolved:
            try:
                ent = _evaluate(int(wid), "(id) => agentWorld.query.getEntity(id)", resolved)
            except Exception:
                ent = None
    if action == "world_click" and resolved:
        submit_trigger = _is_submit_trigger(int(wid), resolved)
    elif action == "world_press" and resolved:
        submit_trigger = _is_submit_trigger(int(wid), resolved, args.get("key"))
    # 只有“无 href 的可交互链接/卡片”有较高概率通过 JavaScript window.open，
    # 对这类目标给一个很短的发现窗口；普通按钮/填写动作不增加等待。
    page_poll_ms = 0
    if action in ("world_click", "world_press") and isinstance(ent, dict):
        attrs = ent.get("attributes") or {}
        if (ent.get("semantic") == "link" and not attrs.get("href")) or str(ent.get("name") or "").startswith("link."):
                # window.open 的 page 事件可能晚于点击返回；卡片专属窗口稍微
                # 放宽到 800ms，确保动作后果卡先于后续 world_state 看见新页。
                page_poll_ms = 800
    new_pages = _activate_new_page(int(wid), args.get("_before_page_tokens"), poll_ms=page_poll_ms)
    try:
        # 导航已开始(URL 相对动作前已变)时用轻量 after 快照:不等新文档 evaluate
        # (GitHub 实测导航期间 evaluate 可阻塞 ~20s,动作反馈被拖慢;URL 变化是导航强证据)
        fast_after = False
        try:
            fast_after = (w["page"].url != (before or {}).get("url"))
        except Exception:
            fast_after = False
        after = _page_signal_snapshot(int(wid), fast=fast_after)
    except Exception:
        after = {}
    # 保留旧 feedback/全局纠正逻辑(URL/弹窗覆盖局部判定)
    ret = _finalize_click_result(int(wid), ret, before, after)
    if new_pages:
        ret.setdefault("feedback", {})["pages"] = {
            "new": new_pages,
            "active_url": after.get("url"),
            "active_page": next((x.get("page_id") for x in new_pages if x.get("active")), None),
        }

    fb = ret.get("feedback") or {}
    page_fb = fb.get("page") or {}
    ov_fb = fb.get("overlays") or {}
    url_changed = bool(page_fb.get("url_changed"))
    new_overlays = ov_fb.get("new") or []
    gone_overlays = ov_fb.get("gone") or []
    effect = ret.get("effect") or {}
    # P1:证据管线缺席兜底。快照缺失等竞态下 effect 为空,以往会一路掉进 unchanged(把未知判成失败);
    # 现合成 unknown 证据,由 _build_page_outcome 映射为 uncertain(复核一次,而非当失败)。
    if not isinstance(effect, dict) or not effect.get("verdict"):
        effect = {"verdict": "unknown", "confidence": "low",
                  "why": "证据管线未返回 effect(快照缺失或采集中断),生效未知",
                  "observed": []}
        ret["effect"] = effect

    check_errors = action in ("world_fill", "world_batch_fill", "world_press") or submit_trigger
    page_outcome, situation, confidence, why = _build_page_outcome(
        int(wid), before, after,
        submit_trigger=submit_trigger, effect=effect,
        overlays_changed=bool(new_overlays or gone_overlays),
        check_errors=check_errors,
    )

    # Phase 3 遮挡归因:unchanged + 目标被遮挡 → why 并入归因,消除"含糊的 unchanged"
    occlusion = ret.get("occlusion")
    if page_outcome == "unchanged" and occlusion and occlusion.get("covered"):
        occl_hint = ret.get("obscured_note") or ""
        if occl_hint:
            why = f"{why};{occl_hint}"
        by = occlusion.get("covered_by") or {}
        situation = {"type": "occluded", "to_url": None,
                     "covered_by": by, "at": occlusion.get("at") or [],
                     "action": occlusion.get("action")}

    # 目标身份(最佳努力;导航后旧 el_N 全部失效,target.id 置空,只留 URL)
    if action == "world_navigate":
        target = {"id": None, "name": str(args.get("url", ""))[:300], "fingerprint": None}
    else:
        target = {
            "id": (ent or {}).get("id"),
            "name": (ent or {}).get("name"),
            "fingerprint": (ent or {}).get("fingerprint") if ent else None,
        }

    # 导览失效判定:全局事实变了,旧导览不可信
    guide_stale = bool(url_changed or new_overlays or gone_overlays or page_fb.get("title_changed"))
    # P2a:page.anomaly 接真信号(与状态卡同口径的轻量检测),异常安全默认 False。
    try:
        _anomaly = _anomaly_check(int(wid))
    except Exception:
        _anomaly = False

    # 阶段 C: 自愈处方 (recipes) 与 人机交接 (handoff) 协议
    handoff = None
    recipes = []
    if page_outcome == "challenged":
        handoff = {
            "required": True,
            "type": "human_challenge",
            "reason": why,
            "suggested": "页面触发人机验证或固定遮罩,请通知用户在可见窗口协助完成",
            "resume_condition": "challenge_cleared",
        }
        next_suggested = "页面被挑战遮罩/验证墙拦截,请暂停自动推进并转交人工处理"
    elif page_outcome == "unchanged":
        # 探测是否受活动弹窗/遮罩阻挡
        dialogs = (after.get("dialogs") if after else None) or []
        if not dialogs:
            try:
                core_status = _evaluate(int(wid), "() => agentWorld.query.getStatus()") or {}
                dialogs = core_status.get("dialogs") or []
            except Exception:
                dialogs = []
        if dialogs:
            d_id = dialogs[0].get("id") or target_arg
            d_name = dialogs[0].get("name") or d_id or "活动弹窗"
            recipes = [
                {"action": "world_act", "kind": "press", "id": d_id, "key": "Escape", "why": f"当前存在未关闭的活动弹窗({d_name}),优先按 Escape 退出"},
                {"action": "world_find", "q": "关闭", "why": "寻找弹窗内的关闭按钮并点击"},
            ]
            next_suggested = f"检测到存在活动弹窗({d_name}),当前操作未生效可能受其阻挡,建议按 Escape 或先关闭弹窗"
        else:
            # R4:纯 unchanged 也必须给机器可读恢复出口(换目标/重导览),不得留空
            next_suggested = ("重新调用 world_guide" if guide_stale
                              else "当前操作未生效,换目标或重新调用 world_guide(同一目标不得重复硬点)")
    elif page_outcome == "errored" and situation.get("type") in {"network_error", "network_application_error"}:
        err_info = (situation.get("errors") or [{}])[0]
        status_code = err_info.get("status", "")
        if situation.get("type") == "network_application_error":
            next_suggested = f"操作引发后端业务错误(HTTP {status_code}),请根据业务错误修正参数或换路径,无需原地盲目重复提交"
        else:
            next_suggested = f"操作引发后端接口报错(HTTP {status_code}),请根据报错修正参数或换路径,无需原地盲目重复提交"
    elif page_outcome == "errored" and situation.get("type") == "console_error":
        next_suggested = "操作引发前端控制台异常,请检查输入合法性或重新导览"
    else:
        next_suggested = "重新调用 world_guide" if guide_stale else None

    card_data = {
        "world_id": int(wid),
        "channel": "outcome",
        "page_outcome": page_outcome,
        "situation": situation,
        "confidence": confidence,
        "why": why,
        "target": target,
        "action": {"kind": action.split("_", 1)[1], "via": "self"},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": url_changed,
            "state": after.get("state", "unknown"),
            "anomaly": _anomaly,
        },
        "overlays": {"new": new_overlays[:8], "gone": gone_overlays[:8]},
        "next": {
            "guide_stale": guide_stale,
            "suggested": next_suggested,
            "candidates": (occlusion.get("candidates") or []) if (occlusion and occlusion.get("covered")) else [],
        },
        "evidence_seq": int(w.get("evidence_seq", 0)) + 1,
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
    }
    if situation.get("type") in ("network_error", "network_application_error", "console_error") and "errors" in situation:
        card_data["errors"] = situation["errors"]
    if handoff:
        card_data["handoff"] = handoff
    if recipes:
        card_data["recipes"] = recipes

    ret.update(card_data)
    # F2 来源标记:白名单字段打标签,页面自由文本(如 target.name)标 untrusted
    ret["sources"] = _sources_for_card(ret)
    return _ok(ret)


def _errored_card(wid, action, args, before_signal, exc):
    """动作执行异常 → 统一后果卡 page_outcome=errored(结构化返回,保留错误信息)。"""
    w = _world(int(wid))
    try:
        before = before_signal or _page_signal_snapshot(int(wid))
    except Exception:
        before = {}
    # P0-1:errored 卡也消耗一个序号。不 mint 的话,它的序号会与上一张成功卡重复,
    # world_outcome(since=上一序号) 将返回 none,把这张 errored 藏掉(对账黑洞)。
    try:
        after = _page_signal_snapshot(int(wid))
    except Exception:
        after = {}
    # P0-1 续:mint 序号(独立 try,与快照无关)。不 mint 则与上一张成功卡同号,对账黑洞。
    try:
        w["evidence_seq"] = int(w.get("evidence_seq", 0)) + 1
    except Exception:
        pass
    # P2b:异常真信号(与 _outcome_card 同口径),异常安全。
    try:
        _err_anomaly = _anomaly_check(int(wid))
    except Exception:
        _err_anomaly = False
    _err_card = {
        "world_id": int(wid),
        "channel": "outcome",
        "page_outcome": "errored",
        "situation": {"type": "error", "to_url": None},
        "confidence": "high",
        "why": "动作执行抛异常,未获得生效判定(可能已部分生效)",
        "target": {"id": args.get("id"), "name": None, "fingerprint": None},
        "action": {"kind": action.split("_", 1)[1] if action.startswith("world_") else action, "via": "self"},
        # P2b:errored 不是无证据,而是"未评估"。verdict 用 unevaluated(与 unknown/changed 并列第三态),
        # 映射仍为 errored(异常本身即结论),效果字段诚实声明未评估而非留空。
        "effect": {"verdict": "unevaluated", "confidence": "high",
                   "why": "动作抛异常,效果未评估(见 error 字段)", "observed": []},
        "page": {
            "before_url": before.get("url"),
            "after_url": after.get("url") or before.get("url"),
            "url_changed": bool(before.get("url") and before.get("url") != (after.get("url") or before.get("url"))),
            "state": after.get("state", "unknown"),
            "anomaly": _err_anomaly,
        },
        "overlays": {"new": [], "gone": []},
        "sources": {},
        "next": {"guide_stale": False, "suggested": None, "candidates": []},
        "evidence_seq": int(w.get("evidence_seq", 0)),
        "changes_seq": {"before": before.get("changes_seq", 0), "after": after.get("changes_seq", 0)},
        "world_epoch": int(w.get("epoch", 0)),
        "error": f"{type(exc).__name__}: {str(exc)[:300]}",
    }
    # P2b:空壳填实——对真卡打来源标记(error 字段已纳入白名单)。
    try:
        _err_card["sources"] = _sources_for_card(_err_card)
    except Exception:
        pass
    # 异常动作也必须进入轨迹,否则失败分支会从任务图中消失。
    error_trace = None
    try:
        if args.get("task_id"):
            w["task_id"] = str(args.get("task_id"))[:120]
        w["trace_step_seq"] = int(w.get("trace_step_seq", 0)) + 1
        error_trace = build_trace_entry(
            trace_id=str(w.get("trace_id") or new_id("trace")),
            task_id=str(w.get("task_id") or new_id("task")),
            step_index=int(w["trace_step_seq"]),
            action=action,
            args=args,
            before=before,
            after=after,
            payload=_err_card,
            evidence_seq=int(w.get("evidence_seq", 0)),
            world_epoch=int(w.get("epoch", 0)),
            context=_runtime_context(w),
        )
        attach_business_runtime(
            error_trace,
            w.get("business_state_rules"),
            w.get("operation_contracts"),
        )
        w.setdefault("trace_log", []).append(error_trace)
        if len(w["trace_log"]) > 200:
            del w["trace_log"][:-200]
    except Exception:
        pass
    if persistence_enabled() and error_trace is not None:
        try:
            _trace_store.append(str(w.get("task_id") or ""), error_trace)
        except Exception:
            pass
    return _ok(_err_card)


def _region_snapshot_at(wid, x, y):
    """坐标点击的生效证据基线:以 (x,y) 为中心 ±200px 构造区域(不依赖构件编号)。

    返回结构与 _click_region_snapshot 相同(region/rows/dialogs/target),复用同一套证据窗。
    """
    raw = _evaluate(
        wid,
        """(p) => {
            const pad = 200;
            const reg = { x0: p.x - pad, y0: p.y - pad, x1: p.x + pad, y1: p.y + pad };
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const ex = e.bounds.x, ey = e.bounds.y;
                if (ex + e.bounds.w > reg.x0 && ex < reg.x1 && ey + e.bounds.h > reg.y0 && ey < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            let target = null;
            const top = document.elementFromPoint(p.x, p.y);
            if (top) {
                target = {
                    page_id: (top.getAttribute && top.getAttribute('id')) || '',
                    state: {
                        ariaSelected: top.getAttribute('aria-selected'),
                        ariaExpanded: top.getAttribute('aria-expanded'),
                        checked: typeof top.checked === 'boolean' ? top.checked : top.hasAttribute('checked'),
                        className: (top.className && top.className.baseVal !== undefined ? top.className.baseVal : top.className) || ''
                    }
                };
            }
            return JSON.stringify({ region: reg, rows, dialogs, target });
        }""",
        {"x": x, "y": y},
    )
    if not raw:
        return None
    return json.loads(raw)


# L2 样式快照层:区域元素计算样式属性表。DOM 行 diff 与目标状态都哑火时,
# 先比计算样式(结构化、可解释、免截图),再落到像素兜底(L4)。


def _region_styles(wid, ids):
    """取一批世界构件的计算样式快照 {id: {prop: value}}。失败返回 {}。"""
    uniq = list(dict.fromkeys(ids))[:STYLE_SNAPSHOT_MAX]
    if not uniq:
        return {}
    try:
        raw = _evaluate(wid, """(payload) => {
            const out = {};
            for (const id of payload.ids) {
                const e = agentWorld._runtime.world.elements.get(id);
                if (!e || !e._el || !e._el.isConnected) continue;
                try {
                    const cs = getComputedStyle(e._el);
                    const m = {};
                    for (const p of payload.props) m[p] = cs[p];
                    out[id] = m;
                } catch (err) {}
            }
            return JSON.stringify(out);
        }""", {"ids": uniq, "props": list(STYLE_DIFF_PROPS)})
    except Exception:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _click_region_snapshot(wid, target_id, capture_frame=False):
    """点击前冻结目标空间区域:以目标 bounds 中心 ±CLICK_REGION_PAD 为矩形。
    返回 (region, rows)——region 是固定坐标,点击后 target 可能消失也用它做 diff。
    附带全页可见 dialog/menu 集合(远距弹窗兜底)与目标自身状态(状态切换兜底,如 tab/折叠/勾选)。
    rows: 区域内构件 [id, semantic, name]
    dialogs: 全页可见 dialog/alertdialog/menu 构件 [id, semantic, name]
    target: {page_id, state} —— state={ariaSelected, ariaExpanded, checked, className}
    capture_frame: 是否额外截取区域截图(供视觉 diff 兜底;默认不截,避免每次操作的开销)
    """
    raw = _evaluate(
        wid,
        """(id) => {
            const el = agentWorld._runtime.world.elements.get(id);
            if (!el) return null;
            const b = el.bounds;
            const pad = 200;
            const reg = { x0: b.x - pad, y0: b.y - pad, x1: b.x + b.w + pad, y1: b.y + b.h + pad };
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const x = e.bounds.x, y = e.bounds.y;
                if (x + e.bounds.w > reg.x0 && x < reg.x1 && y + e.bounds.h > reg.y0 && y < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 全页可见弹窗/菜单(远距弹窗兜底)
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 目标自身状态(用页面原生 id 寻 DOM,SPA 重建后依然可读)
            let target = null;
            if (el._el) {
                const page_id = (el.attributes && el.attributes.id) || '';
                const n = page_id ? (document.getElementById(page_id) || el._el) : el._el;
                target = {
                    page_id,
                    state: {
                        ariaSelected: n.getAttribute('aria-selected'),
                        ariaExpanded: n.getAttribute('aria-expanded'),
                        checked: typeof n.checked === 'boolean' ? n.checked : n.hasAttribute('checked'),
                        className: (n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className) || ''
                    }
                };
            }
            return JSON.stringify({ region: reg, rows, dialogs, target });
        }""",
        target_id,
    )
    if not raw:
        return None
    data = json.loads(raw)
    if capture_frame:
        try:
            w = _world(wid)
            reg = data["region"]
            frame_path = SCREENSHOT_DIR / f"frame_before_{wid}_{int(time.time()*1000)}.png"
            w["page"].screenshot(
                path=str(frame_path),
                clip={"x": max(0, reg["x0"]), "y": max(0, reg["y0"]), "width": max(10, reg["x1"] - reg["x0"]), "height": max(10, reg["y1"] - reg["y0"])}
            )
            data["frame_path"] = frame_path
            try:
                data["scroll_y"] = _evaluate(wid, "() => window.scrollY") or 0
            except Exception:
                data["scroll_y"] = 0
            # L2:同拍一张区域计算样式快照(目标优先,最多 STYLE_SNAPSHOT_MAX 个)
            try:
                _sids = [target_id] + [r[0] for r in (data.get("rows") or [])]
                data["styles_before"] = _region_styles(wid, _sids)
            except Exception:
                data["styles_before"] = {}
        except Exception:
            pass
    return data


def _click_region_after(wid, region, page_id=None):
    """点击后用冻结区域取当前构件(不依赖目标是否仍存在)。
    附带全页可见 dialog/menu 集合(远距弹窗兜底)与目标自身状态(状态切换兜底)。
    """
    raw = _evaluate(
        wid,
        """(arg) => {
            const reg = arg.region, page_id = arg.page_id;
            const rows = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                const x = e.bounds.x, y = e.bounds.y;
                if (x + e.bounds.w > reg.x0 && x < reg.x1 && y + e.bounds.h > reg.y0 && y < reg.y1) {
                    rows.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            const dialogs = [];
            for (const e of agentWorld._runtime.world.elements.values()) {
                if (e.semantic === 'dialog' || e.semantic === 'alertdialog' || e.semantic === 'menu') {
                    if (e.inViewport) dialogs.push([e.id, e.semantic, (e.name || '').slice(0, 60)]);
                }
            }
            // 目标自身状态(用页面原生 id 寻 DOM,SPA 重建后依然可读)
            let target_state = null;
            if (page_id) {
                const n = document.getElementById(page_id);
                if (n) {
                    target_state = {
                        ariaSelected: n.getAttribute('aria-selected'),
                        ariaExpanded: n.getAttribute('aria-expanded'),
                        checked: typeof n.checked === 'boolean' ? n.checked : n.hasAttribute('checked'),
                        className: (n.className && n.className.baseVal !== undefined ? n.className.baseVal : n.className) || ''
                    };
                }
            }
            return JSON.stringify({ rows, dialogs, target_state });
        }""",
        {"region": region, "page_id": page_id},
    )
    if not raw:
        return [], [], None
    data = json.loads(raw)
    return data.get("rows", []), data.get("dialogs", []), data.get("target_state")


def _wait_click_effect(wid, snap_before, url_before, max_wait_ms=2500, disappear_ok=False, fill_verified=False):
    """点击后轮询目标区域:有变化即停(不等满),最多 max_wait_ms。
    同时采集全页可见 dialog 集合(远距弹窗/关弹窗兜底)与目标自身状态(状态切换兜底)。
    返回 effect 报告;捕获失败时返回 None(调用方省略字段)。
    """
    if not snap_before:
        return None
    region = snap_before["region"]
    before_rows = snap_before.get("rows", [])
    before_dialogs = snap_before.get("dialogs", [])
    before_target = snap_before.get("target") or {}
    page_id = before_target.get("page_id") or None
    before_target_state = before_target.get("state")
    w = _world(wid)
    deadline = time.time() + max_wait_ms / 1000
    last_rows = None
    last_dialogs = None
    last_target_state = None
    last_seen = 0
    # 证据窗计时埋点(用于评估轮询式证据窗的收益/浪费)
    t_start = time.time()
    poll_count = 0
    first_change_at = None
    stop_reason = "timeout"
    while time.time() < deadline:
        time.sleep(0.2)
        poll_count += 1
        # 导航开始后旧文档的执行上下文会立即销毁；先读取 Playwright 的 URL
        # 事实，避免继续 evaluate 旧 DOM 而等待新页面加载，导致动作反馈被
        # 外部店铺的慢加载拖住。URL 变化本身就是导航类强证据。
        try:
            current_url = w["page"].url
        except Exception:
            current_url = ""
        if current_url and current_url != url_before:
            return {
                "verdict": "effected",
                "confidence": "high",
                "why": "URL 变化(导航/提交类)",
                "observed": [],
                "region_changed": {"new": 0, "gone": 0},
                "evidence": {
                    "polls": poll_count,
                    "total_ms": int((time.time() - t_start) * 1000),
                    "first_change_ms": int((time.time() - t_start) * 1000),
                    "stop": "url-change",
                },
            }
        try:
            rows, dialogs, target_state = _click_region_after(wid, region, page_id)
        except Exception:
            rows, dialogs, target_state = [], [], None
        if (rows, dialogs, target_state) != (last_rows, last_dialogs, last_target_state):
            last_rows, last_dialogs, last_target_state = rows, dialogs, target_state
            last_seen = time.time()
            if first_change_at is None:
                first_change_at = time.time() - t_start
            # 聪明早停:已看到"决定性证据"(弹窗出现/URL变/状态翻转/关键构件/值进框)就直接返回,
            # 不必再等 0.4s 稳定——弹窗都弹出来了,等稳定是白等(对持续变化页收益最大)
            if rows:
                early = _build_click_effect(before_rows, rows, w["page"].url != url_before,
                                            before_dialogs, dialogs,
                                            before_target_state, target_state,
                                            disappear_ok, fill_verified)
                if early["verdict"] == "effected":
                    early["evidence"] = {
                        "polls": poll_count,
                        "total_ms": int((time.time() - t_start) * 1000),
                        "first_change_ms": int((first_change_at or 0) * 1000),
                        "stop": "early-effect",
                    }
                    return early
        # 区域稳定(0.4s 无变化)且距首次观察足够(让重渲染完成)即停
        if rows and (time.time() - last_seen > 0.4) and (time.time() - last_seen < 5):
            stop_reason = "stable"
            break
    total_ms = int((time.time() - t_start) * 1000)
    url_changed = w["page"].url != url_before
    if last_rows is None:
        try:
            last_rows, last_dialogs, last_target_state = _click_region_after(wid, region, page_id)
        except Exception:
            last_rows, last_dialogs, last_target_state = [], [], None
    effect = _build_click_effect(before_rows, last_rows or [], url_changed,
                                 before_dialogs, last_dialogs or [],
                                 before_target_state, last_target_state,
                                 disappear_ok, fill_verified)
    
    # 视觉双轨兜底: 若 DOM 结构无变化(no-change), 取前后局部帧计算 RMS 像素差异, 捕捉纯 CSS 动效/浮层/颜色切换
    if effect.get("verdict") == "no-change" and snap_before.get("frame_path"):
        try:
            # L2 样式层:先比计算样式(结构化、可解释、免截图)。命中则直接生效,不走像素。
            # 只比双端都在的元素;消失/新增归 DOM 侧管,这里跳过。
            _styles_hit = False
            _sb = snap_before.get("styles_before")
            if _sb:
                try:
                    # 波动基线:转菊花这类持续动画每帧都变,必须先排除,
                    # 否则任何含动画邻居的区域都会误报。after 连采两次,之间在变即噪声。
                    _sa1 = _region_styles(wid, list(_sb.keys()))
                    try:
                        time.sleep(0.15)
                    except Exception:
                        pass
                    _sa2 = _region_styles(wid, list(_sb.keys()))
                    _volatile = set()
                    for _sid in _sa1:
                        _m1, _m2 = _sa1.get(_sid) or {}, _sa2.get(_sid) or {}
                        for _p in _m1:
                            if _m1.get(_p) != _m2.get(_p):
                                _volatile.add((_sid, _p))
                    _diffs = []
                    for _sid, _bm in _sb.items():
                        _am = _sa2.get(_sid)
                        if not _am:
                            continue
                        for _p, _bv in _bm.items():
                            if (_sid, _p) in _volatile:
                                continue
                            if _am.get(_p) != _bv:
                                _diffs.append({"id": _sid, "prop": _p,
                                               "before": str(_bv)[:120],
                                               "after": str(_am.get(_p))[:120]})
                            if len(_diffs) >= 8:
                                break
                        if len(_diffs) >= 8:
                            break
                    if _diffs:
                        _first = _diffs[0]
                        effect["verdict"] = "visual-effected"
                        effect["confidence"] = "high"
                        effect["why"] = (f"区域元素计算样式变化({len(_diffs)}处,"
                                         f"如{_first['id']}.{_first['prop']}:"
                                         f"{_first['before']}→{_first['after']})")
                        effect["style_changes"] = _diffs
                        effect["visual_path"] = "style-diff"
                        _styles_hit = True
                except Exception:
                    pass
            # P0-2 scroll-shift 护栏:点击导致页面滚动时,固定坐标区域前后帧必然错位,
            # 此时 RMS 再大也不能判生效(实测滚动 216px 产生 RMS 28.9,淹没真信号)。
            try:
                y_now = _evaluate(wid, "() => window.scrollY") or 0
            except Exception:
                y_now = 0
            y_before = snap_before.get("scroll_y", y_now)
            if _styles_hit:
                pass  # 已命中样式层,跳过像素兜底
            elif abs((y_now or 0) - (y_before or 0)) > 2:
                effect["visual_skipped"] = "scroll-shift"
                effect["why"] = (effect.get("why") or "") + \
                    "(动作前后页面发生滚动,区域前后帧错位,视觉比对作废)"
            else:
                b_path = snap_before["frame_path"]
                a_path = SCREENSHOT_DIR / f"frame_after_{wid}_{int(time.time()*1000)}.png"
                reg = snap_before["region"]
                w["page"].screenshot(
                    path=str(a_path),
                    clip={"x": max(0, reg["x0"]), "y": max(0, reg["y0"]), "width": max(10, reg["x1"] - reg["x0"]), "height": max(10, reg["y1"] - reg["y0"])}
                )
                i1 = Image.open(b_path).convert("RGB")
                i2 = Image.open(a_path).convert("RGB")
                diff = ImageChops.difference(i1, i2)
                stat = ImageStat.Stat(diff)
                diff_rms = math.sqrt(sum(stat.sum2) / (i1.size[0] * i1.size[1] * 3))
                # P0-2:原始分永远记录(阈值校准与审计用),判定走 VISUAL_RMS_THRESHOLD。
                effect["visual_diff_raw"] = round(diff_rms, 2)
                effect["visual_path"] = "pixel"
                if diff_rms > VISUAL_RMS_THRESHOLD:
                    effect["verdict"] = "visual-effected"
                    effect["confidence"] = "high"
                    effect["why"] = f"检测到目标区域发生显著视觉状态或浮层变化 (RMS={round(diff_rms, 2)})"
                    effect["visual_diff_score"] = round(diff_rms, 2)
        except Exception:
            pass

    effect["evidence"] = {
        "polls": poll_count,
        "total_ms": total_ms,
        "first_change_ms": int((first_change_at or 0) * 1000),
        "stop": stop_reason,
    }
    return effect


def _occlusion_probe(wid, target_id=None, x=None, y=None):
    """遮挡归因探测(Phase 3):elementFromPoint 单点检查。

    目标模式(target_id):检查元素中心点是否被上层元素遮挡。
    坐标模式(x/y,click_at 用):报告坐标处顶层元素,遮罩类(role=dialog/menu 或
    backdrop/overlay/modal class)标记 covered。
    返回 None 或 {covered, covered_by:{tag,role,id,cls}, at:[x,y], target_tag}。
    本函数只检测与归因,不改变任何动作行为。
    """
    try:
        data = _evaluate(
            wid,
            """(arg) => {
                let el = null, cx = null, cy = null;
                if (arg.id !== null && arg.id !== undefined && arg.id !== '') {
                    el = agentWorld._runtime.world.elements.get(arg.id);
                    if (!el || !el._el) return null;
                    el._el.scrollIntoView({ block: 'center', inline: 'center' });
                    const r = el._el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return { covered: false, hidden: true };
                    cx = Math.round(r.x + r.width / 2); cy = Math.round(r.y + r.height / 2);
                } else {
                    cx = Math.round(arg.x); cy = Math.round(arg.y);
                }
                const top = document.elementFromPoint(cx, cy);
                if (!top) return { covered: false };
                const tag = top.tagName.toLowerCase();
                const cls = (top.className && typeof top.className === 'string')
                    ? top.className.split(/\\s+/).slice(0, 3).join(' ') : '';
                const role = top.getAttribute('role') || '';
                let covered;
                if (arg.id !== null && arg.id !== undefined && arg.id !== '') {
                    covered = Boolean(el && top !== el._el && !el._el.contains(top));
                } else {
                    covered = ['dialog', 'alertdialog', 'menu'].includes(role)
                        || /backdrop|overlay|modal/i.test(cls);
                }
                let candidates = [];
                if (covered && top) {
                    try {
                        const btns = top.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]');
                        for (let b of Array.from(btns).slice(0, 3)) {
                            const bText = (b.innerText || b.getAttribute('aria-label') || b.getAttribute('title') || b.value || '').trim();
                            const bId = b.getAttribute('id') || '';
                            candidates.push({
                                tag: b.tagName.toLowerCase(),
                                id: bId,
                                text: bText.slice(0, 40),
                                reason: '遮挡层内部可交互入口(可尝试点击以关闭或完成验证)'
                            });
                        }
                    } catch (e) {}
                }
                return {
                    covered: covered,
                    covered_by: { tag, role, id: top.id || '', cls },
                    at: [cx, cy],
                    target_tag: el && el._el ? el._el.tagName.toLowerCase() : null,
                    candidates: candidates,
                };
            }""",
            {"id": target_id, "x": x, "y": y},
        )
        if not data or data.get("hidden"):
            return None
        return data
    except Exception:
        return None


def _occlusion_attach(ret, probe):
    """把遮挡归因挂到动作返回:结构化 occlusion 字段 + 兼容旧 obscured_note。"""
    if not probe or not probe.get("covered"):
        return
    by = probe.get("covered_by") or {}
    label = f"<{by.get('tag') or '?'}"
    if by.get("role"):
        label += f" role={by['role']}"
    if by.get("id"):
        label += f" id={by['id']}"
    if by.get("cls"):
        label += f" class={by['cls']}"
    label += ">"
    at = probe.get("at") or []
    if by.get("role") in ("dialog", "alertdialog", "menu"):
        action = "先关闭/操作该弹窗,再重试动作"
    else:
        action = "先处理该遮挡元素,再重试动作"
    ret["occlusion"] = {
        "covered": True,
        "covered_by": by,
        "at": at,
        "action": action,
    }
    if probe.get("candidates"):
        ret["occlusion"]["candidates"] = probe["candidates"]
    ret["obscured_note"] = f"目标被 {label} 遮挡于 {at}:{action}"
