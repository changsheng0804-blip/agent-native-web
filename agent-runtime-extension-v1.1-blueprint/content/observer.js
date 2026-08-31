window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let observer = null;
  const DEBOUNCE_MS = 150;
  // 持续变更兜底:页面懒加载/轮播/广告刷新会不断重置防抖计时器,
  // 若不设上限,onChange 可能永远不触发(世界模型饿死)。实战验证:
  // Booking.com 上 world 停滞在 678 个,forceRefresh 却抓到 2426 个。
  const MAX_WAIT_MS = 1000;

  /**
   * 启动 DOM 变化观察
   * 设计:累积式防抖(不丢中间批次) + maxWait 兜底(持续变更不饿死)
   */
  function startDOMObserver(onChange) {
    if (observer) observer.disconnect();

    let pending = [];       // 累积待处理的 mutation 批次
    let debounceTimer = null;
    let maxWaitTimer = null;

    function flush() {
      debounceTimer = null;
      maxWaitTimer = null;
      if (pending.length === 0) return;
      const batch = pending;
      pending = [];
      onChange(batch);
    }

    function onMutations(relevant) {
      if (relevant.length === 0) return;
      pending.push(...relevant);
      // 正常防抖:变更停止 150ms 后处理
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(flush, DEBOUNCE_MS);
      // maxWait 兜底:若页面持续变更导致防抖一直被重置,
      // 首次变更后最多 MAX_WAIT_MS 一定强制 flush 一次
      if (!maxWaitTimer) {
        maxWaitTimer = setTimeout(() => {
          clearTimeout(debounceTimer);
          flush();
        }, MAX_WAIT_MS);
      }
    }

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
      onMutations(relevant);
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
