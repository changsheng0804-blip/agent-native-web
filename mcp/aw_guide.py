# -*- coding: utf-8 -*-
"""任务导览簇——guide 词条、候选扩展、聚焦视图(world_map+三信道组合导览)。

自 mcp/server.py 拆出(Step 4 特性簇),行为不变;依赖方向:aw_core ← aw_runtime ← 本簇。
"""
import re, time
try:
    from aw_core import (
    _guide_terms,
    _page_node_identity,
    )
except ImportError:
    from mcp.aw_core import (
    _guide_terms,
    _page_node_identity,
    )
try:
    from aw_runtime import (
    _evaluate,
    _evaluate_query_retry,
    _ok,
    _page_signal_snapshot,
    _result_payload,
    _route_hint,
    _task_public,
    _task_update,
    _world,
    )
except ImportError:
    from mcp.aw_runtime import (
    _evaluate,
    _evaluate_query_retry,
    _ok,
    _page_signal_snapshot,
    _result_payload,
    _route_hint,
    _task_public,
    _task_update,
    _world,
    )
try:
    from aw_query import (
    _t_world_change_digest,
    )
except ImportError:
    from mcp.aw_query import (
    _t_world_change_digest,
    )


def _expand_candidates(wid, max_results=8):
    """识别可能需要先展开的可交互入口，仅提供提示，不自动点击。"""
    try:
        entities = _evaluate_query_retry(
            wid, "(f) => agentWorld.query.findEntities(f)",
            {"interactive": True, "maxResults": 500}, attempts=2
        ) or []
    except Exception:
        return []
    patterns = re.compile(r"(选择商品|包含\s*\d+\s*件商品|展开|更多|加载|分类|商品|产品|show|load|more|product|item)", re.I)
    scored = []
    for e in entities:
        text = str(e.get("text") or "")
        name = str(e.get("name") or "")
        hay = f"{name} {text}"
        if not patterns.search(hay):
            continue
        score = 0
        if re.search(r"(选择商品|包含\s*\d+\s*件商品|展开|加载|show|load)", hay, re.I):
            score += 3
        if re.search(r"(商品|产品|product|item)", hay, re.I):
            score += 2
        scored.append({
            "id": e.get("id"), "name": e.get("name"), "text": text[:180],
            "semantic": e.get("semantic"), "fingerprint": e.get("fingerprint"),
            "bounds": e.get("bounds"), "interactive": bool(e.get("interactive")),
            "expand_score": score,
        })
    scored.sort(key=lambda x: x.get("expand_score", 0), reverse=True)
    return scored[:max(1, int(max_results))]


def _focused_view(wid, terms, max_candidates=6, frontier_depth=1, expand="none"):
    """构建任务条件化的局部视野(战争迷雾)。

    这里只读取当前已经暴露的 DOM/语义/表单关系,不点击、不提交、不导航。
    结构上把结果拆成 focus(当前焦点)、frontier(可达候选)、blockers(阻断)
    和 collapsed_noise(折叠噪声),不把未知的 JavaScript 去向伪装成确定路径。
    """
    started = time.perf_counter()
    # MVP 只实现当前页面的一跳结构透视,保留字段便于后续扩展但不虚报二跳结果。
    frontier_depth = 1
    expand = str(expand or "none").strip().lower()
    if expand not in {"none", "nearby", "region", "all"}:
        expand = "none"
    try:
        max_candidates = max(1, min(int(max_candidates), 12))
    except Exception:
        max_candidates = 6

    try:
        payload = _evaluate(
            wid,
            r"""(arg) => {
            const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '');
            const terms = (arg.terms || []).map(norm).filter(Boolean);
            const regionRoles = new Set([
                'navigation', 'banner', 'main', 'contentinfo', 'complementary',
                'form', 'dialog', 'tablist', 'menu', 'aside', 'section',
                'article', 'search', 'region'
            ]);
            const actionIntent = terms.some(t => /(退款|标记|权限|开启|开通|发布|提交|保存|删除|移除|授权|refund|mark|permission|enable|publish|submit|save|delete|authorize)/i.test(t));
            const riskRe = /(删除|移除|付款|支付|购买|发布|提交|保存|确认|授权|登录|验证|delete|remove|pay|purchase|publish|submit|save|confirm|authorize|login|verify)/i;
            const all = [...(agentWorld._runtime?.world?.elements?.values?.() || [])];
            const byNode = new Map(all.filter(e => e && e._el).map(e => [e._el, e]));

            const isVisible = (node) => {
                if (!node) return '';
                try {
                    const r = node.getBoundingClientRect();
                    const s = getComputedStyle(node);
                    return !!r.width && !!r.height && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
                } catch (_) { return ''; }
            };
            const visibleText = (node) => isVisible(node) ? String(node.textContent || '').trim().slice(0, 180) : '';
            const regionOf = (node) => {
                let cur = node;
                let fallback = null;
                const overlayRoles = new Set(['dialog', 'alertdialog', 'menu']);
                for (let i = 0; i < 12 && cur; i++, cur = cur.parentElement) {
                    const known = byNode.get(cur);
                    if (known && (regionRoles.has(known.semantic) || regionRoles.has(known.tag))) {
                        const candidate = {
                            id: known.id,
                            name: known.name || known.text || known.semantic,
                            semantic: known.semantic || known.tag,
                            bounds: known.bounds || null
                        };
                        if (overlayRoles.has(candidate.semantic)) return candidate;
                        if (!fallback) fallback = candidate;
                    }
                }
                if (fallback) return fallback;
                return { id: 'region:unknown', name: '未命名区域', semantic: 'unknown', bounds: null };
            };
            const rows = [];
            for (const e of all) {
                if (!e || !e._el || !e.interactive) continue;
                const n = e._el;
                const tag = String(e.tag || n.tagName || '').toLowerCase();
                const attrs = e.attributes || {};
                const href = n.getAttribute('href') || attrs.href || '';
                const controls = n.getAttribute('aria-controls') || '';
                const form = n.closest ? n.closest('form') : null;
                const formAction = form ? (form.getAttribute('action') || form.action || '') : '';
                const type = String(n.getAttribute('type') || '').toLowerCase();
                const inputType = type || (tag === 'select' ? 'select-one' : '');
                const currentValue = ['input', 'textarea', 'select'].includes(tag) ? String(n.value || '') : '';
                const placeholder = n.getAttribute('placeholder') || '';
                const disabled = !!(n.disabled || n.hasAttribute('disabled') || n.getAttribute('aria-disabled') === 'true');
                const requiredMissing = !!(n.required && !String(n.value || '').trim());
                const invalid = n.getAttribute('aria-invalid') === 'true';
                const visible = isVisible(n);
                const region = regionOf(n);
                const ariaRole = String(n.getAttribute('role') || '').toLowerCase();
                const insideRow = !!(n.closest && n.closest('tr'));
                let actionRole = 'unknown-action';
                if (ariaRole === 'menuitem') actionRole = 'menu-item';
                else if (['input', 'textarea', 'select'].includes(tag)) actionRole = 'form-input';
                else if (tag === 'button' && ['dialog', 'alertdialog'].includes(region.semantic)
                    && /(确认|确定|保存|提交|删除|退款|发布|授权|confirm|save|submit|delete|refund|publish|authorize)/i.test([e.name, e.text].join(' '))) actionRole = 'confirm-action';
                else if (tag === 'button' && insideRow) actionRole = 'row-action';
                else if (tag === 'button' && ariaRole === 'tab') actionRole = 'tab';
                else if (tag === 'a' && insideRow) actionRole = 'object-link';
                else if (tag === 'a' && href) actionRole = 'navigation-link';
                else if (tag === 'button') actionRole = 'action-button';
                const ownHay = norm([
                    e.name, e.text, e.semantic, href, controls, formAction
                ].join(' '));
                const regionHay = norm([region.name, region.semantic].join(' '));
                const elementMatched = terms.filter(t => ownHay.includes(t));
                const regionMatched = terms.filter(t => regionHay.includes(t));
                const matched = [...new Set([...elementMatched, ...regionMatched])];
                const searchField = tag === 'input'
                    && ['search', 'text'].includes(inputType)
                    && terms.some(t => ['search', 'filter'].includes(t));
                const relation = href
                    ? 'direct-link-confirmed'
                    : (formAction && (type === 'submit' || tag === 'button' || tag === 'input')
                        ? 'form-target-confirmed'
                        : (controls
                            ? 'controlled-state-inferred'
                            : ((tag === 'button' && n.getAttribute('role') === 'tab') || tag === 'summary' || tag === 'details'
                                ? 'local-state-inferred'
                                : 'destination-unknown')));
                const confidence = relation.endsWith('confirmed')
                    ? 'confirmed'
                    : (relation.endsWith('inferred') ? 'inferred' : 'unknown');
                const pathScore = href ? 5 : (formAction ? 4 : (controls ? 3 : (relation === 'local-state-inferred' ? 2 : 0)));
                const taskScore = elementMatched.reduce((sum, t) => sum + Math.min(10, t.length) + 3, 0)
                    + regionMatched.reduce((sum, t) => sum + Math.min(6, t.length), 0)
                    + (searchField ? 8 : 0);
                const risk = riskRe.test([e.name, e.text, href, controls, formAction].join(' '));
                const actionScore = actionIntent && ['button', 'input', 'select', 'summary'].includes(tag) ? 4 : 0;
                const score = taskScore + pathScore + actionScore + (e.inViewport ? 1 : 0) + (risk ? 2 : 0);
                rows.push({
                    id: e.id,
                    name: e.name,
                    text: e.text,
                    semantic: e.semantic,
                    tag,
                    inputType,
                    value: currentValue,
                    placeholder,
                    searchField,
                    interactive: true,
                    inViewport: !!e.inViewport,
                    visible,
                    bounds: e.bounds || null,
                    fingerprint: e.fingerprint,
                    href: href || null,
                    formAction: formAction || null,
                    ariaControls: controls || null,
                    actionRole,
                    relation,
                    confidence,
                    elementMatched,
                    regionMatched,
                    matchedTerms: matched,
                    pathScore,
                    taskScore,
                    score,
                    risk,
                    disabled,
                    requiredMissing,
                    invalid,
                    region
                });
            }

            const alerts = [...document.querySelectorAll('[role="alert"], [aria-live="assertive"], [aria-invalid="true"]')]
                .map(n => ({
                    id: n.id || null,
                    text: visibleText(n),
                    role: n.getAttribute('role') || 'alert',
                    source: 'page',
                    untrusted: true
                }))
                .filter(x => x.text)
                .slice(0, 12);
            return {
                rows,
                alerts,
                totals: {
                    elements: all.length,
                    interactive: rows.length,
                    visibleInteractive: rows.filter(x => x.inViewport).length
                }
            };
            }""",
            {"terms": list(terms or [])},
        ) or {}
    except Exception as exc:
        # 渐进扫描/导航尚未就绪时不让 focused 导览把整条任务链打崩;
        # 返回空焦点并明确 unknown,由调用方按需重试或扩大视野。
        payload = {
            "rows": [],
            "alerts": [],
            "totals": {},
            "error": f"focused-view-unavailable:{type(exc).__name__}",
        }

    rows = payload.get("rows") or []
    alerts = payload.get("alerts") or []
    rows.sort(key=lambda r: (-int(r.get("score", 0)), -int(r.get("pathScore", 0)), str(r.get("id", ""))))

    def _region_key(row):
        region = row.get("region") or {}
        return region.get("id") or f"{region.get('semantic', 'unknown')}:{region.get('name', '')}"

    def _public(row, kind):
        public = {
            "id": row.get("id"),
            "name": row.get("name"),
            "text": row.get("text"),
            "semantic": row.get("semantic"),
            "tag": row.get("tag"),
            "interactive": bool(row.get("interactive")),
            "in_viewport": bool(row.get("inViewport")),
            "visible": bool(row.get("visible", True)),
            "bounds": row.get("bounds"),
            "fingerprint": row.get("fingerprint"),
            "href": row.get("href"),
            "form_action": row.get("formAction"),
            "aria_controls": row.get("ariaControls"),
            "action_role": row.get("actionRole", "unknown-action"),
            "relation": row.get("relation"),
            "confidence": row.get("confidence", "unknown"),
            "element_terms": row.get("elementMatched") or [],
            "region_terms": row.get("regionMatched") or [],
            "matched_terms": row.get("matchedTerms") or [],
            "region": row.get("region") or {},
            "disabled": bool(row.get("disabled")),
            "required_missing": bool(row.get("requiredMissing")),
            "invalid": bool(row.get("invalid")),
            "risk": bool(row.get("risk")),
            "evidence": "live-structure",
            "view_kind": kind,
        }
        # 仅向表单控件暴露控制类型和值，避免把 null 元数据复制到每个链接/按钮。
        if row.get("actionRole") == "form-input":
            public.update({
                "input_type": row.get("inputType"),
                "value": row.get("value"),
                "placeholder": row.get("placeholder"),
                "search_field": bool(row.get("searchField")),
            })
        return public

    # 当前焦点优先保留元素本身命中或具备明确路径的入口;
    # 仅因区域标题命中、但没有动作/路径关系的装饰按钮不进入焦点。
    focus_pool = [
        r for r in rows
        if r.get("visible", True) and (r.get("elementMatched") or (r.get("pathScore", 0) > 0 and r.get("matchedTerms")))
    ]
    if not focus_pool:
        focus_pool = [r for r in rows if r.get("visible", True) and r.get("pathScore", 0) > 0]
    # 页面出现错误/警告时，附近的重试、重新加载和恢复入口必须穿透噪声过滤，
    # 即使按钮文字与任务目标没有直接词面匹配。
    recovery_pool = [
        r for r in rows
        if r.get("visible", True)
        and re.search(r"(重试|再试|重新加载|刷新|retry|reload)", " ".join([str(r.get("name") or ""), str(r.get("text") or "")]), re.I)
    ] if alerts else []
    if recovery_pool:
        recovery_ids = {r.get("id") for r in recovery_pool}
        focus_pool = recovery_pool + [r for r in focus_pool if r.get("id") not in recovery_ids]
    # 活动菜单/抽屉/确认弹窗是当前视野的强边界:即使按钮文本没有直接命中任务词,
    # 也必须把其中的可见操作保留,否则 Agent 看见弹窗却找不到“确认/保存”按钮。
    active_overlay = [
        r for r in rows
        if r.get("visible", True)
        and (r.get("region") or {}).get("semantic") in {"dialog", "menu"}
    ]
    if active_overlay:
        existing = {r.get("id") for r in active_overlay}
        focus_pool = active_overlay + [r for r in focus_pool if r.get("id") not in existing]
    focus = focus_pool[:max_candidates]
    focus_ids = {r.get("id") for r in focus}
    focus_regions = {_region_key(r) for r in focus}

    blockers = []
    blocker_ids = set()
    for row in rows:
        reasons = []
        if not row.get("visible", True):
            continue
        if row.get("disabled"):
            reasons.append("element-disabled")
        if row.get("requiredMissing"):
            reasons.append("required-field-missing")
        if row.get("invalid"):
            reasons.append("aria-invalid")
        if reasons:
            item = _public(row, "blocker")
            item["reasons"] = reasons
            blockers.append(item)
            blocker_ids.add(row.get("id"))
    for alert in alerts:
        item = dict(alert)
        item["view_kind"] = "runtime-signal"
        blockers.append(item)

    def _frontier_allowed(row):
        if expand == "all":
            return True
        if row.get("elementMatched"):
            return True
        if _region_key(row) in focus_regions:
            # 侧栏/全局导航中一个入口命中任务词时,不要把同一整栏的所有
            # 直链复制到 frontier;主体表格/弹窗区域才允许保留附近路径。
            if (row.get("region") or {}).get("semantic") in {
                "navigation", "complementary", "banner", "contentinfo"
            }:
                return False
            return row.get("pathScore", 0) > 0
        if expand in {"region", "all"} and row.get("pathScore", 0) > 0:
            return True
        # 直接链接/表单是页面明确暴露的候选边,但仍受数量上限约束。
        if row.get("pathScore", 0) >= 4 and row.get("region", {}).get("semantic") in {
            "navigation", "menu", "tablist", "main", "unknown"
        }:
            # 默认窄视野不把整条全局导航当作候选噪声;只有附近/区域展开,
            # 或当前没有任何焦点时,才把这些直接链接作为探索边界。
            return expand in {"nearby", "region", "all"} or not focus
        return False

    frontier_pool = [r for r in rows if r.get("visible", True) and r.get("id") not in focus_ids and _frontier_allowed(r)]
    if expand == "none":
        frontier_limit = max_candidates
    elif expand == "nearby":
        frontier_limit = max_candidates * 2
    elif expand == "region":
        frontier_limit = max_candidates * 4
    else:
        frontier_limit = len(rows)
    frontier = frontier_pool[:frontier_limit]
    frontier_ids = {r.get("id") for r in frontier}

    selected_ids = focus_ids | frontier_ids | blocker_ids
    collapsed_rows = [r for r in rows if r.get("visible", True) and r.get("id") not in selected_ids]
    collapsed_by_region = {}
    for row in collapsed_rows:
        region = row.get("region") or {}
        key = _region_key(row)
        bucket = collapsed_by_region.setdefault(key, {
            "id": region.get("id"),
            "name": region.get("name"),
            "semantic": region.get("semantic"),
            "count": 0,
        })
        bucket["count"] += 1

    runtime_signals = []
    for alert in alerts:
        runtime_signals.append(dict(alert))
    for row in rows:
        if row.get("visible", True) and row.get("risk") and row.get("id") not in blocker_ids:
            item = _public(row, "runtime-signal")
            item["signal"] = "risk-sensitive-action"
            runtime_signals.append(item)
    if payload.get("error"):
        runtime_signals.append({
            "signal": "focused-view-unavailable",
            "source": "runtime",
            "message": "当前页面结构尚未就绪,请重试或扩大观察范围",
            "error": payload.get("error"),
            "view_kind": "runtime-signal",
        })

    focus_public = [_public(row, "focus") for row in focus]
    frontier_public = [_public(row, "frontier") for row in frontier]
    blocker_public = blockers[:max_candidates * 2 + 12]
    signal_public = runtime_signals[:max_candidates * 2 + 12]

    def _short_item(item):
        short = {
            "id": item.get("id"),
            "text": item.get("text"),
            "name": item.get("name"),
            "semantic": item.get("semantic"),
            "action_role": item.get("action_role", "unknown-action"),
            "relation": item.get("relation"),
            "confidence": item.get("confidence"),
            "disabled": bool(item.get("disabled")),
            "required_missing": bool(item.get("required_missing")),
            "href": item.get("href"),
            "aria_controls": item.get("aria_controls"),
            "matched_terms": item.get("matched_terms") or [],
            "region": {
                key: (item.get("region") or {}).get(key)
                for key in ("id", "name", "semantic")
                if (item.get("region") or {}).get(key) is not None
            },
        }
        if item.get("action_role") == "form-input":
            short.update({
                "input_type": item.get("input_type"),
                "value": item.get("value"),
                "placeholder": item.get("placeholder"),
                "search_field": bool(item.get("search_field")),
            })
        return short

    def _pick_score(item):
        role = item.get("action_role") or "unknown-action"
        role_score = {
            "form-input": 7,
            "confirm-action": 6,
            "menu-item": 6,
            "row-action": 5,
            "action-button": 4,
            "tab": 2,
            "object-link": 1,
            "navigation-link": 0,
        }.get(role, 0)
        if role == "form-input":
            if item.get("search_field") or item.get("input_type") == "search":
                role_score += 5
            if item.get("semantic") == "listbox" and str(item.get("value") or "") in {"全部", "全部状态", "all"}:
                role_score -= 4
        if re.search(r"(重试|再试|重新加载|刷新|retry|reload)", " ".join([str(item.get("name") or ""), str(item.get("text") or "")]), re.I):
            role_score += 10
        if (item.get("region") or {}).get("semantic") in {"menu", "dialog", "alertdialog"}:
            # 活动弹层是当前操作边界，优先于弹层外的搜索框、分页器等候选。
            role_score += 10
        if item.get("disabled"):
            role_score -= 8
        if item.get("required_missing"):
            role_score += 4
        return role_score

    actionable = [item for item in focus_public + frontier_public if not item.get("disabled")]
    required = [item for item in blocker_public if item.get("required_missing") and not item.get("disabled")]
    recommendation_pool = required + actionable
    recommendation_pool.sort(key=lambda item: (-_pick_score(item), -len(item.get("matched_terms") or []), str(item.get("id") or "")))
    recommended_next = _short_item(recommendation_pool[0]) if recommendation_pool else None
    alternative_items = []
    if recommended_next:
        for item in recommendation_pool[1:]:
            short = _short_item(item)
            if short.get("id") != recommended_next.get("id"):
                alternative_items.append(short)
            if len(alternative_items) >= 3:
                break
    if recommended_next:
        role_label = {
            "form-input": "先填写前置条件",
            "row-action": "优先使用行级操作入口",
            "menu-item": "优先使用当前菜单动作",
            "confirm-action": "检查风险后再确认",
            "object-link": "打开对象详情",
            "navigation-link": "进入导航路径",
        }.get(recommended_next.get("action_role"), "检查候选入口")
        next_action = f"{role_label}: {recommended_next.get('text') or recommended_next.get('name') or recommended_next.get('id')}"
    else:
        next_action = "当前没有可靠的可执行候选;先扩大视野或读取阻断信号"

    return {
        "focus": focus_public,
        "frontier": frontier_public,
        "blockers": blocker_public,
        "runtime_signals": signal_public,
        "recommended_next": recommended_next,
        "alternatives": alternative_items,
        "next_action": next_action,
        "collapsed_noise": {
            "count": len(collapsed_rows),
            "regions": sorted(collapsed_by_region.values(), key=lambda x: (-x["count"], str(x.get("name") or ""))),
            "expandable": bool(collapsed_rows),
            "expand_options": ["nearby", "region", "all"] if collapsed_rows else [],
        },
        "view_meta": {
            "mode": "focused",
            "depth": frontier_depth,
            "expand": expand,
            "generated_ms": round((time.perf_counter() - started) * 1000, 2),
            "source": "live-structure",
            "totals": payload.get("totals") or {},
            "degraded": bool(payload.get("error")),
        },
    }


def _compact_focused_view(view):
    """把 focused 视野编码成适合模型上下文的短包,保留行动所需字段。"""
    def compact_item(item):
        region = item.get("region") or {}
        return {
            key: item.get(key)
            for key in (
                "id", "name", "text", "semantic", "tag", "interactive", "in_viewport",
                "input_type", "value", "placeholder", "search_field",
                "href", "form_action", "aria_controls", "action_role", "relation", "confidence",
                "matched_terms", "disabled", "required_missing", "invalid", "risk", "view_kind",
            )
            if key in item
        } | ({"region": {key: region.get(key) for key in ("id", "name", "semantic") if region.get(key) is not None}} if region else {})

    def compact_signal(item):
        if not isinstance(item, dict):
            return item
        return {
            key: item.get(key)
            for key in (
                "id", "name", "text", "role", "source", "signal", "status",
                "url", "detail", "message", "kind", "untrusted", "view_kind",
            )
            if key in item
        }

    collapsed = view.get("collapsed_noise") or {}
    meta = view.get("view_meta") or {}
    compact = dict(view)
    compact["focus"] = [compact_item(item) for item in view.get("focus", [])]
    compact["frontier"] = [compact_item(item) for item in view.get("frontier", [])]
    compact["blockers"] = [compact_signal(item) for item in view.get("blockers", [])]
    compact["runtime_signals"] = [compact_signal(item) for item in view.get("runtime_signals", [])]
    compact["collapsed_noise"] = {
        "count": collapsed.get("count", 0),
        "regions": [
            {key: item.get(key) for key in ("id", "name", "semantic", "count") if key in item}
            for item in collapsed.get("regions", [])
        ],
        "expandable": bool(collapsed.get("expandable")),
        "expand_options": collapsed.get("expand_options", []),
    }
    compact["view_meta"] = {
        key: meta.get(key)
        for key in ("mode", "depth", "expand", "generated_ms", "source", "degraded", "totals")
        if key in meta
    }
    compact["view_meta"]["detail"] = "compact"
    return compact


def _t_world_guide(args):
    """把三个页面信道和当前实时结构组合成一份短的任务导览。"""
    wid = args["world_id"]
    task = str(args["task"]).strip()
    if not task:
        raise ValueError("task 不能为空,请用一句话描述当前任务")
    max_candidates = max(1, min(int(args.get("max_candidates", 6)), 12))
    view_mode = str(args.get("view_mode", "full") or "full").strip().lower()
    if view_mode not in {"full", "focused"}:
        raise ValueError("view_mode 只能是 full 或 focused")
    # MVP 只执行一跳的无副作用结构分析。
    frontier_depth = 1
    expand = str(args.get("expand", "none") or "none").strip().lower()
    if expand not in {"none", "nearby", "region", "all"}:
        raise ValueError("expand 只能是 none、nearby、region 或 all")
    view_detail = str(args.get("view_detail", "full") or "full").strip().lower()
    if view_detail not in {"full", "compact"}:
        raise ValueError("view_detail 只能是 full 或 compact")
    change_since = int(args.get("change_since", 0))
    evidence_since = int(args.get("evidence_since", 0))
    try:
        _evaluate(wid, "() => { agentWorld._runtime.refreshStatus(); return true; }")
    except Exception:
        pass

    state = _page_signal_snapshot(wid)
    change_digest = _result_payload(_t_world_change_digest({
        "world_id": wid,
        "since": change_since,
    }))
    w = _world(wid)
    recent_evidence_raw = [
        x for x in w.get("evidence_log", [])
        if x.get("evidence_seq", 0) > evidence_since
    ][-5:]
    # 导览只带最近证据的短摘要;需要动作前后完整状态时再读 world_evidence。
    recent_evidence = []
    for item in recent_evidence_raw:
        after = item.get("after") or {}
        transition = item.get("transition") or {}
        recent_evidence.append({
            "evidence_seq": item.get("evidence_seq"),
            "action": item.get("action"),
            "target": item.get("target"),
            "verdict": item.get("verdict"),
            "confidence": item.get("confidence"),
            "why": item.get("why"),
            "runtime_signals": [
                dict(signal) for signal in (item.get("runtime_signals") or [])[:8]
                if isinstance(signal, dict)
            ],
            "after": {
                "url": after.get("url"),
                "title": after.get("title"),
                "state": after.get("state"),
            },
            "transition": {
                "url_changed": transition.get("url_changed"),
                "new_overlays": transition.get("new_overlays", [])[:8],
                "gone_overlays": transition.get("gone_overlays", [])[:8],
                "changes_seq_changed": transition.get("changes_seq_changed"),
            },
        })
    terms = _guide_terms(task)
    raw_candidates = _evaluate(
        wid,
        """(arg) => {
            const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, '');
            const terms = (arg.terms || []).map(norm).filter(Boolean);
            const map = agentWorld.query.map(8);
            const rows = [];
            const seen = new Set();
            const push = (e, rg) => {
                if (!e || seen.has(e.id)) return;
                const href = e.attributes && e.attributes.href;
                const hay = norm([
                    rg.name, rg.semantic, e.name, e.text, e.semantic, href
                ].join(' '));
                const matched = [];
                let score = 0;
                    for (const term of terms) {
                        if (term && hay.includes(term)) {
                            matched.push(term);
                            score += Math.min(10, term.length) + (href ? 2 : 0);
                            // 任务明确寻找筛选/搜索时,优先实际控件和搜索区域,
                            // 避免页面标题或列表内容淹没任务入口。
                            if (term === 'filter' || term === 'search') {
                                if (rg.semantic === 'search') score += 12;
                                if (['input', 'searchbox', 'textbox', 'select'].includes(e.semantic)) score += 10;
                                if (e.semantic === 'navigation' && /filter/i.test(String(e.text || ''))) score += 5;
                            }
                        }
                    }
                if (!score) return;
                seen.add(e.id);
                rows.push({
                    id: e.id,
                    name: e.name,
                    text: e.text,
                    semantic: e.semantic,
                    interactive: !!e.interactive,
                    inViewport: !!e.inViewport,
                    bounds: e.bounds,
                    fingerprint: e.fingerprint,
                    href: href || null,
                    region: { semantic: rg.semantic, name: rg.name, bounds: rg.bounds },
                    matched,
                    match_score: score
                });
            };
            for (const block of (map.regions || [])) {
                const rg = block.region || {};
                for (const entry of (block.entries || [])) {
                    const e = agentWorld.query.getEntity(entry.id);
                    push(e, rg);
                }
            }
            // 地图只列每区的少量入口;任务目标可能在未列出的入口中,这里仅作页面内语义兜底。
            for (const brief of agentWorld.query.findEntities({ interactive: true, maxResults: 1000 })) {
                if (seen.has(brief.id)) continue;
                const e = agentWorld.query.getEntity(brief.id) || brief;
                push(e, { semantic: e.region || 'unknown', name: 'live-entity', bounds: null });
            }
            rows.sort((a, b) => {
                // 同分时:命中任务词数多者优先(避免 Notifications 这类无关元素
                // 与 star 命中者并列 4 分时靠原始顺序抢到 first → next_action 误导)
                return b.match_score - a.match_score
                    || (b.matched || []).length - (a.matched || []).length
                    || Number(b.interactive) - Number(a.interactive);
            });
            return rows.slice(0, arg.max);
        }""",
        {"terms": terms, "max": max_candidates},
    ) or []

    candidates = []
    for item in raw_candidates:
        candidate = {
            "id": item.get("id"),
            "name": item.get("name"),
            "text": item.get("text"),
            "semantic": item.get("semantic"),
            "interactive": item.get("interactive"),
            "in_viewport": item.get("inViewport"),
            "bounds": item.get("bounds"),
            "fingerprint": item.get("fingerprint"),
            "matched_terms": item.get("matched", []),
            "match_score": item.get("match_score", 0),
            "region": item.get("region"),
            "evidence": "live-structure",
        }
        if item.get("href"):
            candidate["href"] = item["href"]
            candidate["relation"] = "direct-link-confirmed"
        else:
            candidate["relation"] = "target-found-destination-unconfirmed"
        candidates.append(candidate)

    regions = []
    seen_regions = set()
    for candidate in candidates:
        rg = candidate.get("region") or {}
        key = (rg.get("semantic"), rg.get("name"))
        if key in seen_regions:
            continue
        seen_regions.add(key)
        regions.append({
            "semantic": rg.get("semantic"),
            "name": rg.get("name"),
            "bounds": rg.get("bounds"),
            "reason": "包含与当前任务匹配的实时入口",
        })

    direct_routes = [
        {
            "from": state.get("url"),
            "to": c.get("href"),
            "via": c.get("id"),
            "status": "confirmed",
        }
        for c in candidates if c.get("href")
    ]
    route_hint = _route_hint(wid, state, candidates)
    expand_candidates = _expand_candidates(wid)
    if candidates:
        top = candidates[0]
        # next_action 保底:只指向"可交互且命中任务词"的候选;
        # 同分时无关元素曾排第一(实测 Notifications 抢走 Star 任务的 first),误导 harness 点错。
        if top.get("interactive") and top.get("matched_terms"):
            next_action = f"优先检查候选 {top.get('id')} 的详图,再决定是否执行动作"
        else:
            next_action = ("候选区分度不足:最高分候选未命中任务词,请先确认目标元素"
                           "(可用 world_entities 按文本过滤)再执行动作")
    else:
        next_action = "当前页面没有找到直接匹配入口;不要猜测,先扩大到导航/菜单区域或提供更具体目标词"

    focused_view = None
    if view_mode == "focused":
        focused_view = _focused_view(
            wid,
            terms,
            max_candidates=max_candidates,
            frontier_depth=frontier_depth,
            expand=expand,
        )
        # 活动弹窗/菜单属于运行时信号,即使与任务词不匹配也必须保留。
        for signal_kind in ("dialogs", "menus"):
            for item in state.get(signal_kind, []) or []:
                focused_view["runtime_signals"].append({
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "text": item.get("text"),
                    "signal": signal_kind,
                    "source": "page-state",
                    "untrusted": True,
                    "view_kind": "runtime-signal",
                })
        # 页面没有错误文字时，动作证据中的浏览器网络/控制台错误仍必须
        # 穿透战争迷雾；这些文本来自页面运行时，标记为不可信数据，不能当指令。
        for evidence in recent_evidence:
            for signal in evidence.get("runtime_signals") or []:
                signal = dict(signal)
                signal.setdefault("view_kind", "runtime-signal")
                focused_view["runtime_signals"].append(signal)
        focused_view["runtime_signals"] = focused_view["runtime_signals"][:max_candidates * 2 + 12]
        if view_detail == "compact":
            focused_view = _compact_focused_view(focused_view)

    try:
        node_id, _ = _page_node_identity(state)
        _task_update(wid, page={"url": state.get("url"), "node": node_id,
                                "epoch": int(w.get("epoch", 0))},
                     route={"mode": "mcp", "source": route_hint.get("source"),
                            "validated": bool(route_hint.get("validated")),
                            "stale": bool(route_hint.get("stale"))})
    except Exception:
        pass

    result = {
        "world_id": wid,
        "channel": "task-guide",
        "task": task,
        "terms": terms,
        "state": {
            "url": state.get("url"),
            "title": state.get("title"),
            "status": state.get("state"),
            "dialogs": state.get("dialogs", []),
            "menus": state.get("menus", []),
        },
        "change_digest": change_digest,
        "recent_evidence": recent_evidence,
        "relevant_regions": regions[:6],
        "candidates": candidates,
        "routes": direct_routes[:max_candidates],
        "route_hint": route_hint,
        "expand_candidates": expand_candidates,
        "next_action": next_action,
        "unknown": [
            "点击后的新页面结构尚未确认",
            "没有公开链接的按钮去向需要执行后用证据确认",
        ],
        "next_cursors": {
            "change_since": change_digest.get("to", change_since),
            "evidence_since": int(w.get("evidence_seq", evidence_since)),
        },
        "task_state": _task_public(wid),
    }
    if focused_view is not None:
        result.update(focused_view)
        # focused 模式不再重复携带 full 导览的候选/路由大块;否则“战争迷雾”
        # 在服务端生成了窄视野,却在传输层又把整张旧地图带回去。
        for field in ("relevant_regions", "candidates", "routes", "route_hint", "expand_candidates"):
            result.pop(field, None)
        if view_detail == "compact":
            digest = result.get("change_digest") or {}
            result["change_digest"] = {
                key: digest.get(key)
                for key in ("from", "to", "changed", "events_seen", "counts", "importance_counts", "key")
                if key in digest
            }
            result.pop("task_state", None)
            result.pop("next_cursors", None)
        result["view_mode"] = view_mode
        result["frontier_depth"] = frontier_depth
        result["expand"] = expand
        result["view_detail"] = view_detail
        result["unknown"] = list(result.get("unknown") or []) + [
            "没有明确 href、表单目标或 aria-controls 的 JavaScript 按钮去向仍需执行后确认",
            "候选边界只表示当前页面可观察的可能路径,不代表服务器结果已确定",
        ]
    return _ok(result)
