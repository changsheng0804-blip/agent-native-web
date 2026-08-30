/**
 * Agent Runtime Adapter - Main Entry
 * 将网页翻译成 Agent 可理解的世界
 */

(function(global) {
  'use strict';

  const CONFIG = {
    GRID_SIZE: 40,
    DEBOUNCE_DELAY: 200, // DOM 变化防抖延迟
    RESIZE_DEBOUNCE: 150 // 窗口 resize 防抖延迟
  };

  let worldState = null;
  let queryAPI = null;
  let mutationObserver = null;
  let resizeTimeout = null;
  let isInitialized = false;

  /**
   * 初始化 Agent World
   */
  function initialize() {
    if (isInitialized) return;
    
    console.log('[Agent Runtime] Initializing...');
    const startTime = performance.now();
    
    try {
      // 1. 扫描 DOM
      const scanResult = global.AgentRuntime.scanner.scanDOM();
      console.log(`[Agent Runtime] Scanned ${scanResult.count} elements in ${scanResult.scanTime.toFixed(2)}ms`);
      
      // 2. 构建占用网格
      const occupancyGrid = global.AgentRuntime.occupancy.buildOccupancyGrid(
        scanResult.elements,
        scanResult.viewport.width,
        scanResult.viewport.height
      );
      console.log(`[Agent Runtime] Built occupancy grid: ${occupancyGrid.width}x${occupancyGrid.height} in ${occupancyGrid.buildTime.toFixed(2)}ms`);
      
      // 3. 构建语义树
      const semanticTree = global.AgentRuntime.semantic.buildSemanticTree(
        scanResult.elements,
        scanResult.viewport.height
      );
      console.log(`[Agent Runtime] Built semantic tree with ${Object.keys(semanticTree.bySemantic).length} semantic types in ${semanticTree.buildTime.toFixed(2)}ms`);
      
      // 4. 构建拓扑关系
      const topology = global.AgentRuntime.topology.buildTopology(scanResult.elements);
      console.log(`[Agent Runtime] Built topology with ${topology.navigationPath.length} navigation rows in ${topology.buildTime.toFixed(2)}ms`);
      
      // 5. 检测空白区域
      const regions = global.AgentRuntime.regions.detectRegions(occupancyGrid, scanResult.elements);
      console.log(`[Agent Runtime] Detected ${regions.regionCount} empty regions in ${regions.detectTime.toFixed(2)}ms`);
      
      // 组装世界状态
      worldState = {
        elements: semanticTree.elements,
        occupancyGrid,
        semanticTree,
        topology,
        regions,
        meta: {
          url: window.location.href,
          title: document.title,
          initializedAt: Date.now(),
          totalScanTime: performance.now() - startTime
        }
      };
      
      // 创建查询 API
      queryAPI = global.AgentRuntime.query.createQueryAPI(worldState);
      
      // 挂载到 window.agentWorld
      global.agentWorld = {
        version: '1.0.0',
        meta: worldState.meta,
        elements: worldState.elements,
        occupancyGrid: worldState.occupancyGrid,
        semanticTree: worldState.semanticTree,
        topology: worldState.topology,
        regions: worldState.regions,
        query: queryAPI,
        toggleOverlay: (mode) => global.AgentRuntime.overlay.toggleOverlay(mode, worldState),
        refresh: initialize,
        getState: () => worldState
      };
      
      console.log('[Agent Runtime] World initialized successfully');
      console.log(`[Agent Runtime] Total time: ${worldState.meta.totalScanTime.toFixed(2)}ms`);
      
      // 设置 MutationObserver 监听 DOM 变化
      setupMutationObserver();
      
      // 设置窗口 resize 监听
      setupResizeHandler();
      
      isInitialized = true;
      
    } catch (error) {
      console.error('[Agent Runtime] Initialization failed:', error);
    }
  }

  /**
   * 设置 MutationObserver 监听 DOM 变化
   */
  function setupMutationObserver() {
    if (mutationObserver) {
      mutationObserver.disconnect();
    }
    
    let debounceTimer = null;
    
    mutationObserver = new MutationObserver((mutations) => {
      // 防抖
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        console.log('[Agent Runtime] DOM changed, refreshing...');
        initialize();
      }, CONFIG.DEBOUNCE_DELAY);
    });
    
    mutationObserver.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['style', 'class', 'id']
    });
  }

  /**
   * 设置窗口 resize 监听
   */
  function setupResizeHandler() {
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimeout);
      resizeTimeout = setTimeout(() => {
        console.log('[Agent Runtime] Window resized, refreshing...');
        initialize();
      }, CONFIG.RESIZE_DEBOUNCE);
    });
  }

  /**
   * 清理资源
   */
  function cleanup() {
    if (mutationObserver) {
      mutationObserver.disconnect();
      mutationObserver = null;
    }
    
    window.removeEventListener('resize', setupResizeHandler);
    
    if (global.agentWorld) {
      global.agentWorld = null;
    }
    
    isInitialized = false;
    worldState = null;
    queryAPI = null;
  }

  // 页面加载完成后初始化
  if (document.readyState === 'complete') {
    setTimeout(initialize, 100);
  } else {
    window.addEventListener('load', () => {
      setTimeout(initialize, 100);
    });
  }

  // 页面卸载时清理
  window.addEventListener('beforeunload', cleanup);

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.main = {
    initialize,
    cleanup,
    CONFIG
  };

})(window);
