window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let observer = null;
  let debounceTimer = null;
  const DEBOUNCE_MS = 150;

  /**
   * 启动 DOM 变化观察
   */
  function startDOMObserver(onChange) {
    if (observer) observer.disconnect();
    
    observer = new MutationObserver((mutations) => {
      // 过滤有意义的变更
      const relevant = mutations.filter(m => {
        if (m.type === 'childList') return true;
        if (m.type === 'attributes') {
          const attr = m.attributeName;
          return ['style', 'class', 'id', 'role', 'aria-label', 'hidden', 'disabled'].includes(attr);
        }
        return false;
      });
      
      if (relevant.length === 0) return;
      
      // 防抖
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        onChange(relevant);
      }, DEBOUNCE_MS);
    });
    
    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class', 'id', 'role', 'aria-label', 'hidden', 'disabled']
    });
    
    // 监听滚动（更新可见性）
    let scrollTimer = null;
    window.addEventListener('scroll', () => {
      clearTimeout(scrollTimer);
      scrollTimer = setTimeout(() => {
        onChange([{ type: 'scroll' }]);
      }, 100);
    }, { passive: true });
    
    // 监听 resize
    let resizeTimer = null;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        onChange([{ type: 'resize' }]);
      }, 200);
    }, { passive: true });
    
    return observer;
  }

  function stopDOMObserver() {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  global.AgentRuntime.observer = { startDOMObserver, stopDOMObserver };
})(window);
