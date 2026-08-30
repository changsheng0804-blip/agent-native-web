window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  class SpatialQuery {
    constructor(world) {
      this.world = world;
    }

    /** 按语义角色查找 */
    findByRole(role) {
      return (this.world.semanticIndex?.byRole?.[role] || []);
    }

    /** 按标签查找 */
    findByTag(tag) {
      return [...this.world.elements.values()]
        .filter(e => e.tag === tag.toLowerCase())
        .map(e => e.id);
    }

    /** 所有可交互元素 */
    findInteractive() {
      return [...this.world.elements.values()]
        .filter(e => e.interactive)
        .map(e => e.id);
    }

    /** 在视口内的元素 */
    findInViewport() {
      return [...this.world.elements.values()]
        .filter(e => e.inViewport)
        .map(e => e.id);
    }

    /** 获取元素详情 */
    getElement(id) {
      const el = this.world.elements.get(id);
      if (!el) return null;
      // 不暴露 _el 引用
      const { _el, ...safe } = el;
      return safe;
    }

    /** 查找邻近元素 */
    nearby(id, radius = 3) {
      const target = this.world.elements.get(id);
      if (!target) return [];
      return [...this.world.elements.values()]
        .filter(el => {
          if (el.id === id) return false;
          const dist = global.AgentRuntime.topology.elementDistance(target, el);
          return dist < radius;
        })
        .map(el => el.id);
    }

    /** 获取元素的邻居 */
    getNeighbors(id) {
      return this.world.topology?.adjacency?.get(id) || { top: [], bottom: [], left: [], right: [] };
    }

    /** 查找空白区域 */
    findEmptyRegions(minGridCells = 10) {
      const grid = this.world.occupancy;
      if (!grid || !grid.grid) return [];
      
      const visited = new Set();
      const regions = [];
      
      for (let y = 0; y < grid.rows; y++) {
        for (let x = 0; x < grid.cols; x++) {
          const key = `${x},${y}`;
          if (visited.has(key)) continue;
          if (grid.grid[y]?.[x]?.occupied) continue;
          
          // BFS 找连通空白区域
          const region = bfsEmpty(grid, x, y, visited);
          if (region.cells >= minGridCells) {
            regions.push({
              id: `region-${regions.length}`,
              bounds: region.bounds,
              cells: region.cells,
              areaPx: region.cells * grid.cellSize * grid.cellSize,
              center: {
                gx: Math.round((region.bounds.gx1 + region.bounds.gx2) / 2),
                gy: Math.round((region.bounds.gy1 + region.bounds.gy2) / 2)
              }
            });
          }
        }
      }
      
      return regions.sort((a, b) => b.cells - a.cells);
    }

    /** 导航路径 */
    navigationPath() {
      return this.world.topology?.navigationPath || [];
    }

    /** 自然语言描述 */
    describe() {
      const elements = [...this.world.elements.values()];
      return global.AgentRuntime.semantics.generateLayoutDescription(
        elements,
        this.world.semanticIndex
      );
    }

    /** 页面摘要 */
    getPageSummary() {
      const elements = [...this.world.elements.values()];
      const interactive = elements.filter(e => e.interactive);
      const inViewport = elements.filter(e => e.inViewport);
      const emptyRegions = this.findEmptyRegions();
      
      return {
        total: elements.length,
        interactive: interactive.length,
        inViewport: inViewport.length,
        emptyRegions: emptyRegions.length,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll: { y: window.scrollY, totalHeight: document.body.scrollHeight },
        semanticTypes: Object.keys(this.world.semanticIndex?.byRole || {}).length
      };
    }

    /** 完整快照 */
    getSnapshot() {
      const elements = [...this.world.elements.values()].map(e => {
        const { _el, ...safe } = e;
        return safe;
      });
      return {
        version: this.world.version,
        meta: this.world.meta,
        elements,
        summary: this.getPageSummary(),
        emptyRegions: this.findEmptyRegions(),
        navigationPath: this.navigationPath()
      };
    }

    /**
     * 构件清单（统一过滤式查询，CAD 构件表）
     * filter: { role, tag, text, name, interactive, inViewport, maxResults }
     */
    findEntities(filter = {}) {
      const {
        role, tag, text, name, interactive, inViewport, maxResults = 200
      } = filter;
      const result = [];
      for (const el of this.world.elements.values()) {
        if (role && el.semantic !== role) continue;
        if (tag && el.tag !== String(tag).toLowerCase()) continue;
        if (text && !(el.text || '').toLowerCase().includes(String(text).toLowerCase())) continue;
        if (name && !(el.name || '').toLowerCase().includes(String(name).toLowerCase())) continue;
        if (interactive !== undefined && el.interactive !== !!interactive) continue;
        if (inViewport !== undefined && el.inViewport !== !!inViewport) continue;
        result.push({
          id: el.id,
          name: el.name,
          tag: el.tag,
          semantic: el.semantic,
          text: el.text,
          bounds: el.bounds,
          interactive: el.interactive,
          inViewport: el.inViewport
        });
        if (result.length >= maxResults) break;
      }
      return result;
    }

    /**
     * 构件详情（getElement 超集：含邻居/区域，不含 DOM 引用）
     */
    getEntity(id) {
      const el = this.world.elements.get(id);
      if (!el) return null;
      const { _el, ...safe } = el;
      // 实时刷新 input/textarea 的 value(动态输入后快照可能过期)
      if (_el && (_el.tagName === 'INPUT' || _el.tagName === 'TEXTAREA') && safe.attributes) {
        safe.attributes.value = _el.value || '';
      }
      safe.neighbors = this.world.topology?.adjacency?.get(id) || { top: [], bottom: [], left: [], right: [] };
      const viewportH = window.innerHeight;
      const y = el.bounds.y;
      safe.region = y < viewportH * 0.15 ? 'header'
        : (y > document.body.scrollHeight - viewportH * 0.15 ? 'footer' : 'body');
      return safe;
    }

    /**
     * 图层视图：结构/语义/空间/交互/名字
     */
    layers() {
      const elements = [...this.world.elements.values()];
      const byTag = {};
      elements.forEach(e => { byTag[e.tag] = (byTag[e.tag] || 0) + 1; });
      const byRole = this.world.semanticIndex?.byRole || {};
      const interactive = elements.filter(e => e.interactive).length;
      const unnamed = elements.filter(e => !e.name || e.name.endsWith('.unnamed')).length;
      return {
        structure: { total: elements.length, byTag },
        semantic: { byRole, types: Object.keys(byRole).length },
        spatial: {
          viewport: { width: window.innerWidth, height: window.innerHeight },
          scroll: { y: Math.round(window.scrollY), totalHeight: document.body.scrollHeight },
          grid: this.world.occupancy
            ? { cols: this.world.occupancy.cols, rows: this.world.occupancy.rows, cellSize: this.world.occupancy.cellSize }
            : null
        },
        interactive,
        names: { total: elements.length, named: elements.length - unnamed, unnamed }
      };
    }

    /**
     * 弱 ID 解析：强 ID / name / 页面原生 id / name 模糊
     */
    resolve(q) {
      if (!q) return null;
      if (this.world.elements.has(q)) {
        return { id: q, exact: true, kind: 'strong' };
      }
      const byName = [...this.world.elements.values()].filter(e => e.name === q);
      if (byName.length === 1) return { id: byName[0].id, exact: true, kind: 'name' };
      if (byName.length > 1) return { matches: byName.map(e => e.id), kind: 'name' };
      const byAttr = [...this.world.elements.values()].filter(e => e.attributes && e.attributes.id === q);
      if (byAttr.length === 1) return { id: byAttr[0].id, exact: true, kind: 'attr-id' };
      const fuzzy = [...this.world.elements.values()]
        .filter(e => (e.name || '').includes(q))
        .slice(0, 10);
      if (fuzzy.length > 0) return { matches: fuzzy.map(e => e.id), kind: 'name-fuzzy' };
      return null;
    }
  }

  function bfsEmpty(grid, startX, startY, visited) {
    const queue = [{ x: startX, y: startY }];
    visited.add(`${startX},${startY}`);
    let cells = 0;
    let gx1 = startX, gy1 = startY, gx2 = startX, gy2 = startY;
    
    while (queue.length > 0) {
      const { x, y } = queue.shift();
      cells++;
      gx1 = Math.min(gx1, x); gy1 = Math.min(gy1, y);
      gx2 = Math.max(gx2, x); gy2 = Math.max(gy2, y);
      
      const neighbors = [{ x: x-1, y }, { x: x+1, y }, { x, y: y-1 }, { x, y: y+1 }];
      for (const n of neighbors) {
        const key = `${n.x},${n.y}`;
        if (visited.has(key)) continue;
        if (n.x < 0 || n.x >= grid.cols || n.y < 0 || n.y >= grid.rows) continue;
        if (grid.grid[n.y]?.[n.x]?.occupied) continue;
        visited.add(key);
        queue.push(n);
      }
    }
    
    return { cells, bounds: { gx1, gy1, gx2, gy2 } };
  }

  global.AgentRuntime.SpatialQuery = SpatialQuery;
})(window);
