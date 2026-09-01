window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 挂载 window.agentWorld — Agent 的统一入口
   */
  function mountAgentWorld(runtime) {
    const query = runtime.world.query;
    
    global.agentWorld = {
      version: '1.0.0',
      
      // 页面元信息
      meta: runtime.world.meta,
      
      // 查询 API
      query: {
        findByRole: (role) => query.findByRole(role),
        findByTag: (tag) => query.findByTag(tag),
        findInteractive: () => query.findInteractive(),
        findInViewport: () => query.findInViewport(),
        getElement: (id) => query.getElement(id),
        nearby: (id, radius) => query.nearby(id, radius),
        getNeighbors: (id) => query.getNeighbors(id),
        findEmptyRegions: () => query.findEmptyRegions(),
        navigationPath: () => query.navigationPath(),
        describe: () => query.describe(),
        getPageSummary: () => query.getPageSummary(),
        getSnapshot: () => query.getSnapshot(),
        findEntities: (filter) => query.findEntities(filter),
        getEntity: (id) => query.getEntity(id),
        layers: () => query.layers(),
        map: (maxEntries) => query.map(maxEntries),
        resolve: (q) => query.resolve(q),
        getStatus: () => query.getStatus()
      },
      
      // 变更日志（视频流）
      changes: (sinceSeq) => runtime.changes(sinceSeq),
      log: (n) => runtime.log(n),
      
      // 可视化
      toggleOverlay: (mode) => global.AgentRuntime.overlay.toggleOverlay(mode, runtime.world),
      
      // 刷新
      refresh: () => runtime.forceRefresh(),
      
      // 内部状态
      _runtime: runtime
    };
    
    console.log('[Agent Runtime] window.agentWorld ready');
  }

  global.AgentRuntime.mountAgentWorld = mountAgentWorld;
})(window);
