window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  class AgentRuntime {
    constructor() {
      this.world = {
        version: '1.0.0',
        elements: new Map(),
        occupancy: null,
        topology: null,
        semanticIndex: null,
        meta: null,
        query: null
      };
      this.occupancyGrid = null;
      this.spatialQuery = null;
      this.updateCount = 0;
      // 事件驱动等待器:world_wait 由 MutationObserver 驱动,不再 server 端轮询
      this._waiters = [];
      this.changelog = {
        seq: 0,
        events: [],
        maxEvents: 2000
      };
      // 世界状态卡:显式暴露"现在是什么"(登录/弹窗/页面/表单)
      this.world.status = {
        dialogs: [],
        page: { state: 'stable', scrollY: 0, totalHeight: 0 },
        forms: [],
        changesSeq: 0
      };
    }

    /**
     * 刷新世界状态(增量维护,防抖后调用)
     */
    refreshStatus() {
      const elements = [...this.world.elements.values()];
      const byNode = new Map(elements.map(e => [e._el, e]));
      // 弹窗/对话框:直接 DOM 查询(预渲染隐藏弹窗会被可见性过滤掉,不依赖原生网页世界)
      const dialogs = [];
      const dialogNodes = document.querySelectorAll('[role="dialog"], [role="alertdialog"], [aria-modal="true"]');
      for (const node of dialogNodes) {
        const rect = node.getBoundingClientRect();
        const st = getComputedStyle(node);
        if (rect.width < 3 || rect.height < 3 || st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') continue;
        if (global.AgentRuntime.visibility.isPseudoHidden(node, st, rect)) continue;
        const el = byNode.get(node);
        dialogs.push({
          id: el ? el.id : 'dom:' + node.tagName.toLowerCase(),
          name: el ? el.name : ((node.getAttribute('aria-label') || node.getAttribute('role') || node.tagName).toLowerCase())
        });
      }
      // 表单(有值的输入框,取前 10)
      const forms = [];
      for (const el of elements) {
        if (!el._el) continue;
        const tag = el._el.tagName;
        if ((tag === 'INPUT' || tag === 'TEXTAREA') && el._el.value) {
          forms.push({ id: el.id, name: el.name, value: String(el._el.value).slice(0, 50) });
          if (forms.length >= 10) break;
        }
      }
      this.world.status.dialogs = dialogs;
      this.world.status.forms = forms;
      // 稳定性:元素数连续两次刷新一致才算 stable(渐进渲染/分层加载下避免误报就绪)
      const curCount = elements.length;
      const prevCount = this._statusCount || 0;
      this._statusCount = curCount;
      const ready = document.readyState === 'complete';
      const stable = ready && (curCount === prevCount || Date.now() - (this.world.meta.initializedAt || 0) > 15000);
      this.world.status.page = {
        state: stable ? 'stable' : 'loading',
        scrollY: Math.round(window.scrollY),
        totalHeight: document.body.scrollHeight
      };
      this.world.status.changesSeq = this.changelog.seq;
    }

    /**
     * 记录变更事件（append-only，带游标）
     */
    logEvent(type, id, extra) {
      const cl = this.changelog;
      cl.seq++;
      const evt = { seq: cl.seq, t: Date.now(), type, id };
      if (extra) Object.assign(evt, extra);
      cl.events.push(evt);
      if (cl.events.length > cl.maxEvents) {
        cl.events.splice(0, cl.events.length - cl.maxEvents);
      }
      return evt;
    }

    /**
     * 读取自 sinceSeq 以来的变更（视频帧续读）
     */
    changes(sinceSeq = 0) {
      const cl = this.changelog;
      return {
        from: sinceSeq,
        to: cl.seq,
        events: cl.events.filter(e => e.seq > sinceSeq)
      };
    }

    /**
     * 最近 n 条变更日志
     */
    log(n = 50) {
      return this.changelog.events.slice(-n);
    }

    /**
     * 事件驱动等待(替代 server 端 0.3s 轮询):
     * 注册一个 waiter,MutationObserver 每次 flush(handleMutation)后检查条件,
     * 命中即 resolve(无需轮询);超时 setTimeout 兜底 resolve(false)。
     * filter 透传 findEntities({role,text,name,...});mode=appear/disappear。
     */
    waitFor(filter = {}, mode = 'appear', timeoutMs = 30000) {
      return new Promise((resolve) => {
        let settled = false;
        const finish = (result) => { if (!settled) { settled = true; resolve(result); } };
        const cleanup = () => {
          const i = this._waiters.indexOf(waiter);
          if (i >= 0) this._waiters.splice(i, 1);
          clearTimeout(waiter.timer);
        };
        const waiter = {
          filter, mode,
          timer: null,
          check: () => {
            try {
              const n = this.world.query.findEntities(filter).length;
              const ok = mode === 'appear' ? n > 0 : n === 0;
              if (ok) {
                cleanup();
                finish({ matched: true, mode, count: n });
                return true;
              }
            } catch (e) { /* query 未就绪等场景:继续等 */ }
            return false;
          }
        };
        waiter.timer = setTimeout(() => {
          cleanup();
          finish({ matched: false, mode, timeout_ms: timeoutMs });
        }, timeoutMs);
        this._waiters.push(waiter);
        waiter.check(); // 立即检查一次:条件已满足时立即返回
      });
    }

    /**
     * MutationObserver flush 后检查所有 waiter(事件驱动核心)
     */
    checkWaiters() {
      for (const w of this._waiters.slice()) w.check();
    }

    /**
     * 初始化：全量扫描 + 启动监听
     */
    init() {
      const startTime = performance.now();
      
      // 1. 全量扫描
      const elements = global.AgentRuntime.scanner.scanAll();
      elements.forEach(el => this.world.elements.set(el.id, el));
      
      // 2. 计算可见性
      global.AgentRuntime.visibility.updateAllVisibility(elements);
      
      // 3. 构建空间层
      this.rebuildSpatialLayers();
      
      // 4. 创建查询引擎
      this.spatialQuery = new global.AgentRuntime.SpatialQuery(this.world);
      this.world.query = this.spatialQuery;
      
      // 5. 记录 meta
      this.world.meta = {
        url: window.location.href,
        title: document.title,
        initializedAt: Date.now(),
        initTime: Math.round(performance.now() - startTime),
        elementCount: this.world.elements.size
      };
      
      // 名字去重（初始全量）
      global.AgentRuntime.semantics.dedupeNames(elements);
      
      // 记录初始事件
      this.logEvent('init', null, { count: this.world.elements.size });
      
      // 刷新世界状态卡
      this.refreshStatus();
      
      console.log(`[Agent Runtime] Initialized: ${this.world.elements.size} elements in ${this.world.meta.initTime}ms`);
      
      // 6. 启动 DOM 观察
      global.AgentRuntime.observer.startDOMObserver((mutations) => {
        this.handleMutation(mutations);
      });
    }

    /**
     * 处理 DOM 变化 — 增量更新
     */
    handleMutation(mutations) {
      this.updateCount++;
      const changedIds = new Set();
      const addedIds = new Set();
      const removedIds = new Set();
      const updatedIds = new Set();
      // 删除前捕获元数据:元素删除后 world.elements 查不到,remove 事件需要 name/semantic
      // 才能被 server 层翻译成人话摘要(变更可读化)
      const removedMeta = new Map();
      
      mutations.forEach(m => {
        if (m.type === 'scroll' || m.type === 'resize') {
          // 滚动/resize 只需更新可见性
          return;
        }
        
        // 处理新增节点
        if (m.addedNodes) {
          m.addedNodes.forEach(node => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const el = global.AgentRuntime.scanner.scanElement(node);
            if (el) {
              this.world.elements.set(el.id, el);
              changedIds.add(el.id);
              addedIds.add(el.id);
            }
            // 子节点也扫描
            if (node.querySelectorAll) {
              node.querySelectorAll('*').forEach(child => {
                const cel = global.AgentRuntime.scanner.scanElement(child);
                if (cel) {
                  this.world.elements.set(cel.id, cel);
                  changedIds.add(cel.id);
                  addedIds.add(cel.id);
                }
              });
            }
          });
        }
        
        // 处理纯文本变化:textContent= 赋值在浏览器里表现为 childList
        // (移除旧文本节点+插入新文本节点,addedNodes 是 TEXT_NODE 会被跳过),
        // 且 m.target(父元素)本身不会被重扫——导致 text 快照陈旧。
        // 缺陷修复(2026-09-02):childList 变化含文本节点时,重扫 m.target 更新 text。
        if (m.type === 'childList' && m.target && m.target.nodeType === Node.ELEMENT_NODE) {
          const hasTextNode = (m.addedNodes && [...m.addedNodes].some(n => n.nodeType === Node.TEXT_NODE)) ||
                              (m.removedNodes && [...m.removedNodes].some(n => n.nodeType === Node.TEXT_NODE));
          if (hasTextNode) {
            const hostId = global.AgentRuntime.scanner.getStableId(m.target);
            if (this.world.elements.has(hostId)) {
              const el = global.AgentRuntime.scanner.scanElement(m.target);
              if (el) {
                this.world.elements.set(el.id, el);
                changedIds.add(el.id);
                updatedIds.add(el.id);
              }
            }
          }
        }

        // 处理属性变化
        if (m.type === 'attributes' && m.target.nodeType === Node.ELEMENT_NODE) {
          // 重新评估 target 及其所有后代:祖先 aria-hidden/隐藏样式变化会影响整棵子树,
          // 不遍历的话"先注册子元素、后给父容器加隐藏"的动态时序会泄露(IPI 防御闭环)
          const nodes = [m.target];
          if (m.target.querySelectorAll) {
            nodes.push(...m.target.querySelectorAll('*'));
          }
          for (const n of nodes) {
            const prevId = global.AgentRuntime.scanner.getStableId(n);
            const wasRegistered = this.world.elements.has(prevId);
            const el = global.AgentRuntime.scanner.scanElement(n);
            if (el) {
              this.world.elements.set(el.id, el);
              changedIds.add(el.id);
              updatedIds.add(el.id);
            } else if (wasRegistered) {
              // 元素被隐藏/变装饰(如动态加 aria-hidden/style/class),从世界移除
              // 避免"先注册后伪隐藏"的动态时序泄露(IPI 防御闭环)
              const prev = this.world.elements.get(prevId);
              if (prev) removedMeta.set(prevId, { name: prev.name, semantic: prev.semantic });
              this.world.elements.delete(prevId);
              changedIds.add(prevId);
              removedIds.add(prevId);
            }
          }
        }

        // 处理纯文本变化(characterData):更新父元素的 text 字段并记 update 事件
        // 缺陷修复(2026-09-02):此前 textContent/value 文本更新不产生可观察事件,
        // 世界模型永远读旧文本——"最终值区域已更新但查询仍是旧值"。
        if (m.type === 'characterData' && m.target.parentElement) {
          const host = m.target.parentElement;
          const el = global.AgentRuntime.scanner.scanElement(host);
          if (el) {
            this.world.elements.set(el.id, el);
            changedIds.add(el.id);
            updatedIds.add(el.id);
          }
        }
        
        // 处理删除节点
        if (m.removedNodes) {
          m.removedNodes.forEach(node => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            const id = global.AgentRuntime.scanner.getStableId(node);
            const prev = this.world.elements.get(id);
            if (prev) removedMeta.set(id, { name: prev.name, semantic: prev.semantic });
            this.world.elements.delete(id);
            changedIds.add(id);
            removedIds.add(id);
            if (node.querySelectorAll) {
              node.querySelectorAll('*').forEach(child => {
                const cid = global.AgentRuntime.scanner.getStableId(child);
                const cprev = this.world.elements.get(cid);
                if (cprev) removedMeta.set(cid, { name: cprev.name, semantic: cprev.semantic });
                this.world.elements.delete(cid);
                changedIds.add(cid);
                removedIds.add(cid);
              });
            }
          });
        }
      });
      
      // 记录变更日志（同一批次按 add > remove > update 优先级合并）
      addedIds.forEach(id => {
        const el = this.world.elements.get(id);
        this.logEvent('add', id, { name: el ? el.name : undefined, semantic: el ? el.semantic : undefined });
      });
      removedIds.forEach(id => {
        const meta = removedMeta.get(id);
        this.logEvent('remove', id, { name: meta ? meta.name : undefined, semantic: meta ? meta.semantic : undefined });
      });
      updatedIds.forEach(id => {
        const el = this.world.elements.get(id);
        this.logEvent('update', id, { name: el ? el.name : undefined, semantic: el ? el.semantic : undefined });
      });
      if (mutations.some(m => m.type === 'scroll' || m.type === 'resize')) {
        this.logEvent('visibility', null, { viewportY: Math.round(window.scrollY) });
      }
      
      // 更新可见性
      const allElements = [...this.world.elements.values()];
      global.AgentRuntime.visibility.updateAllVisibility(allElements);
      
      // 增量更新占位网格
      if (changedIds.size > 0 && this.occupancyGrid) {
        const changed = allElements.filter(e => changedIds.has(e.id));
        this.occupancyGrid.incrementalUpdate(changed);
      }
      
      // 如果变化较大，重建拓扑和语义
      if (changedIds.size > 5 || mutations.some(m => m.type === 'childList')) {
        this.rebuildSpatialLayers();
      }
      
      // 名字去重（保证弱 ID 唯一）
      global.AgentRuntime.semantics.dedupeNames(allElements);
      
      // 刷新世界状态卡
      this.refreshStatus();
      
      // 事件驱动等待器:每次 flush 后检查(命中即 resolve,替代轮询)
      this.checkWaiters();
      
      // 更新 meta
      this.world.meta.elementCount = this.world.elements.size;
      this.world.meta.lastUpdate = Date.now();
      this.world.meta.updateCount = this.updateCount;
    }

    /**
     * 重建空间层（拓扑 + 语义）
     */
    rebuildSpatialLayers() {
      const elements = [...this.world.elements.values()];
      
      // 占位网格
      if (!this.occupancyGrid) {
        this.occupancyGrid = new global.AgentRuntime.OccupancyGrid();
      }
      this.occupancyGrid.rebuild(elements);
      this.world.occupancy = this.occupancyGrid;
      
      // 拓扑
      this.world.topology = global.AgentRuntime.topology.buildTopology(elements);
      
      // 语义索引
      this.world.semanticIndex = global.AgentRuntime.semantics.buildSemanticIndex(elements);
      global.AgentRuntime.semantics.calculateAttentionWeights(elements);
    }

    /**
     * 强制全量刷新
     */
    forceRefresh() {
      this.world.elements.clear();
      this.init();
    }
  }

  global.AgentRuntime.AgentRuntime = AgentRuntime;
})(window);
