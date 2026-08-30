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

  global.AgentRuntime.visibility = { computeVisibility, updateAllVisibility };
})(window);
