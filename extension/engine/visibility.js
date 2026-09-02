window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 计算单个元素的可见性
   */
  function computeVisibility(el) {
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    
    const display = style.display;
    const visibility = style.visibility;
    const opacity = parseFloat(style.opacity || 1);
    const zIndex = parseInt(style.zIndex || 0);
    
    const isVisible = display !== 'none' && visibility !== 'hidden' && opacity > 0 && rect.width > 0 && rect.height > 0;
    const inViewport = rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth;
    
    // 视口覆盖率
    const viewportArea = window.innerWidth * window.innerHeight;
    const elArea = rect.width * rect.height;
    const visibleArea = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0)) *
                        Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
    const viewportCoverage = viewportArea > 0 ? visibleArea / viewportArea : 0;
    
    return {
      visible: isVisible,
      inViewport,
      opacity,
      zIndex,
      viewportCoverage: Math.round(viewportCoverage * 10000) / 10000,
      scrollPosition: {
        fromTop: Math.round(rect.top + window.scrollY),
        fromBottom: Math.round(document.body.scrollHeight - rect.bottom - window.scrollY)
      }
    };
  }

  /**
   * 批量更新所有元素的可见性
   */
  function updateAllVisibility(elements) {
    elements.forEach(el => {
      if (el._el) {
        const vis = computeVisibility(el._el);
        el.visible = vis.visible;
        el.inViewport = vis.inViewport;
        el.viewportCoverage = vis.viewportCoverage;
        el.opacity = vis.opacity;
        el.zIndex = vis.zIndex;
      }
    });
  }

  /**
   * 解析 computed color 字符串 → {r,g,b,a}
   * 兼容 rgb() / rgba() 两种格式
   */
  function parseColor(str) {
    if (!str) return null;
    const m = str.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return {
      r: Math.round(parseFloat(m[1])),
      g: Math.round(parseFloat(m[2])),
      b: Math.round(parseFloat(m[3])),
      a: m[4] !== undefined ? parseFloat(m[4]) : 1
    };
  }

  /**
   * 向上追溯"有效纯色背景"：
   * - 从元素自身开始，找到第一个非透明 backgroundColor
   * - 若途中遇到 background-image（渐变/图片背景），返回 null（无法静态判定，不误伤白字+图背景的合法场景）
   */
  function effectiveBackgroundColor(el) {
    let node = el;
    while (node && node.nodeType === 1) {
      const st = getComputedStyle(node);
      if (st.backgroundImage && st.backgroundImage !== 'none') return null;
      const bg = st.backgroundColor;
      if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
        const c = parseColor(bg);
        if (c && c.a > 0) return c;
      }
      // 向上回溯;Shadow DOM 边界:shadow 内元素 parentElement 链到 shadowRoot 为止,
      // 需跳到 host 元素继续(host 的背景同样决定 shadow 内元素的"有效背景")
      const p = node.parentElement;
      if (p) {
        node = p;
      } else if (node.getRootNode && node.getRootNode() instanceof ShadowRoot) {
        node = node.getRootNode().host || null;
      } else {
        node = null;
      }
    }
    return null;
  }

  /**
   * 伪隐藏检测(IPI 攻防矩阵 VEC_4~VEC_8 的过滤缺口)
   * 在 display/visibility/opacity 三种"结构性隐藏"之外,补五种"伪隐藏":
   *   VEC_4 color:white(文字与有效背景同色)
   *   VEC_5 移出视口(position:absolute/fixed 且完全脱离视口上方/左侧)
   *   VEC_6 font-size:0
   *   VEC_7 text-indent 大幅负缩进
   *   VEC_8 aria-hidden="true"(含祖先链,遵循 ARIA 最近祖先覆盖语义)
   * 返回 true 表示"用户不可见,不应进入原生网页世界"。
   */
  function isPseudoHidden(el, style, rect) {
    if (!el || !el.closest || !style || !rect) return false;

    // VEC_8: aria-hidden="true"(自身或最近带 aria-hidden 的祖先)
    const ah = el.closest('[aria-hidden]');
    if (ah && (ah.getAttribute('aria-hidden') || '').trim().toLowerCase() === 'true') return true;

    // VEC_6: font-size:0 文字零号不可见
    if (parseFloat(style.fontSize) === 0) return true;

    // VEC_7: text-indent 大幅负缩进(文本被移出元素可视范围,经典 image-replacement 隐藏)
    if (parseFloat(style.textIndent) <= -100) return true;

    // VEC_5: 绝对/固定定位且完全脱离视口上方/左侧(不占文档流,滚动也不可达)
    // 只查负方向:向下/向右的大偏移可能是正常页尾/横向内容,避免误伤
    const pos = style.position;
    if ((pos === 'absolute' || pos === 'fixed') && (rect.right < -50 || rect.bottom < -50)) return true;

    // VEC_4: 文字与有效背景同色(或文字全透明)
    // 性能:仅元素含文本才做颜色比对(无文本元素不泄露文本,跳过祖先链 getComputedStyle 遍历)
    const color = parseColor(style.color);
    if (color && color.a === 0) return true; // 全透明文字
    if (color && color.a >= 0.99 && (el.textContent || '').trim().length > 0) {
      const bg = effectiveBackgroundColor(el);
      if (bg && bg.a >= 0.99 && bg.r === color.r && bg.g === color.g && bg.b === color.b) return true;
    }
    return false;
  }

  global.AgentRuntime.visibility = { computeVisibility, updateAllVisibility, isPseudoHidden };
})(window);
