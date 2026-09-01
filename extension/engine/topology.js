window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 构建拓扑关系
   */
  function buildTopology(elements) {
    const adjacency = new Map(); // id → {top, bottom, left, right}
    const navigationPath = [];
    
    // 按y坐标排序构建纵向浏览路径
    const sorted = [...elements].sort((a, b) => a.bounds.y - b.bounds.y);
    
    let prevRow = null;
    sorted.forEach(el => {
      const row = Math.floor(el.bounds.y / 100); // 100px为一行
      if (row !== prevRow) {
        navigationPath.push({ row, elements: [el.id] });
        prevRow = row;
      } else {
        navigationPath[navigationPath.length - 1].elements.push(el.id);
      }
    });
    
    // 计算每个元素的邻近关系
    elements.forEach(el => {
      const neighbors = { top: [], bottom: [], left: [], right: [] };
      
      elements.forEach(other => {
        if (el.id === other.id) return;
        const dist = elementDistance(el, other);
        if (dist > 3) return; // 超过3格不算邻居
        
        // 判断方向
        const dx = other.bounds.x - el.bounds.x;
        const dy = other.bounds.y - el.bounds.y;
        
        if (Math.abs(dy) > Math.abs(dx)) {
          if (dy < 0) neighbors.top.push({ id: other.id, dist });
          else neighbors.bottom.push({ id: other.id, dist });
        } else {
          if (dx < 0) neighbors.left.push({ id: other.id, dist });
          else neighbors.right.push({ id: other.id, dist });
        }
      });
      
      // 每个方向只保留最近的
      Object.keys(neighbors).forEach(dir => {
        neighbors[dir].sort((a, b) => a.dist - b.dist);
        neighbors[dir] = neighbors[dir].slice(0, 3).map(n => n.id);
      });
      
      adjacency.set(el.id, neighbors);
    });
    
    return { adjacency, navigationPath };
  }

  function elementDistance(a, b) {
    const GRID_SIZE = 40;
    const ax = a.bounds.x + a.bounds.w / 2;
    const ay = a.bounds.y + a.bounds.h / 2;
    const bx = b.bounds.x + b.bounds.w / 2;
    const by = b.bounds.y + b.bounds.h / 2;
    return Math.sqrt(((ax - bx) / GRID_SIZE) ** 2 + ((ay - by) / GRID_SIZE) ** 2);
  }

  global.AgentRuntime.topology = { buildTopology, elementDistance };
})(window);
