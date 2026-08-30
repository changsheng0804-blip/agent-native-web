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
      this.changelog = {
        seq: 0,
        events: [],
        maxEvents: 2000
      };
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
        
        // 处理属性变化
        if (m.type === 'attributes' && m.target.nodeType === Node.ELEMENT_NODE) {
          const el = global.AgentRuntime.scanner.scanElement(m.target);
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
            this.world.elements.delete(id);
            changedIds.add(id);
            removedIds.add(id);
            if (node.querySelectorAll) {
              node.querySelectorAll('*').forEach(child => {
                const cid = global.AgentRuntime.scanner.getStableId(child);
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
        this.logEvent('add', id, { name: el ? el.name : undefined });
      });
      removedIds.forEach(id => this.logEvent('remove', id));
      updatedIds.forEach(id => {
        const el = this.world.elements.get(id);
        this.logEvent('update', id, { name: el ? el.name : undefined });
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
