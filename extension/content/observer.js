window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let mainObserver = null;
  let shadowObservers = [];      // 各 shadowRoot 的观察器
  let observedShadowRoots = null; // WeakSet:已观察的 shadowRoot(去重)
  const DEBOUNCE_MS = 150;
  // 持续变更兜底:页面懒加载/轮播/广告刷新会不断重置防抖计时器,
  // 若不设上限,onChange 可能永远不触发(原生网页世界饿死)。实战验证:
  // Booking.com 上 world 停滞在 678 个,forceRefresh 却抓到 2426 个。
  const MAX_WAIT_MS = 1000;

  const OBSERVE_OPTS = {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['style', 'class', 'id', 'role', 'aria-label', 'aria-hidden', 'aria-expanded', 'aria-selected', 'hidden', 'open', 'disabled'],
    // 缺陷修复(2026-09-02 弱模型验证):纯文本变化(textContent/value property)此前不被观察,
    // 导致"最终值区域已更新但世界模型永远读旧文本"(A 组误判点击无效的根因)。
    characterData: true,
    characterDataOldValue: false
  };

  function isRelevantMutation(m) {
    if (m.type === 'childList') return true;
    if (m.type === 'characterData') return true;
    if (m.type === 'attributes') {
      return ['style', 'class', 'id', 'role', 'aria-label', 'aria-hidden', 'aria-expanded', 'aria-selected', 'hidden', 'open', 'disabled'].includes(m.attributeName);
    }
    return false;
  }

  /**
   * 启动 DOM 变化观察
   * 设计:累积式防抖(不丢中间批次) + maxWait 兜底(持续变更不饿死)
   * Shadow DOM:主文档观察器看不到 shadow 子树,故对每个 open shadowRoot 单独创建观察器,
   * 共享同一个 onMutations 收集器;新挂载的 shadowRoot(懒加载组件)在变更回调里补观察。
   */
  function startDOMObserver(onChange) {
    if (mainObserver) {
      mainObserver.disconnect();
      mainObserver = null;
    }
    shadowObservers.forEach(ob => ob.disconnect());
    shadowObservers = [];
    observedShadowRoots = new WeakSet();

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

    // 观察一个 root(主文档 document 或任意 shadowRoot),并递归观察其 open shadow roots
    function observeRoot(root, isMain) {
      const ob = new MutationObserver((mutations) => {
        const relevant = mutations.filter(isRelevantMutation);
        onMutations(relevant);
        // 新增节点里可能携带新 shadow root(组件懒挂载):补观察
        for (const m of mutations) {
          if (!m.addedNodes) continue;
          m.addedNodes.forEach(n => {
            if (n.nodeType !== Node.ELEMENT_NODE) return;
            if (n.shadowRoot && !observedShadowRoots.has(n.shadowRoot)) {
              observedShadowRoots.add(n.shadowRoot);
              observeRoot(n.shadowRoot, false);
            }
            if (n.querySelectorAll) {
              n.querySelectorAll('*').forEach(ch => {
                if (ch.shadowRoot && !observedShadowRoots.has(ch.shadowRoot)) {
                  observedShadowRoots.add(ch.shadowRoot);
                  observeRoot(ch.shadowRoot, false);
                }
              });
            }
          });
        }
      });
      if (isMain) {
        ob.observe(root.documentElement || root.body, OBSERVE_OPTS);
        mainObserver = ob;
      } else {
        ob.observe(root, OBSERVE_OPTS);
        shadowObservers.push(ob);
      }
      // 递归观察 root 内已存在的 open shadow roots(初始化时的组件库)
      const walk = (r) => {
        const all = r.querySelectorAll ? r.querySelectorAll('*') : [];
        all.forEach(el => {
          if (el.shadowRoot && !observedShadowRoots.has(el.shadowRoot)) {
            observedShadowRoots.add(el.shadowRoot);
            observeRoot(el.shadowRoot, false);
            walk(el.shadowRoot);
          }
        });
      };
      walk(root);
      return ob;
    }

    observeRoot(document, true);

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

    return mainObserver;
  }

  function stopDOMObserver() {
    if (mainObserver) {
      mainObserver.disconnect();
      mainObserver = null;
    }
    shadowObservers.forEach(ob => ob.disconnect());
    shadowObservers = [];
    observedShadowRoots = null;
  }

  global.AgentRuntime.observer = { startDOMObserver, stopDOMObserver };
})(window);