/**
 * occupancy.js - Occupancy Grid
 * 构建 40px 网格的占用图
 */

(function(global) {
  'use strict';

  const CONFIG = {
    GRID_SIZE: 40,
    OCCUPANCY_THRESHOLD: 0.5 // 超过 50% 面积算占据
  };

  /**
   * 构建占用网格
   * @param {Array} elements - 扫描得到的元素数组
   * @param {number} viewportWidth - 视口宽度
   * @param {number} viewportHeight - 视口高度
   * @returns {Object} 占用网格数据
   */
  function buildOccupancyGrid(elements, viewportWidth, viewportHeight) {
    const startTime = performance.now();
    const gs = CONFIG.GRID_SIZE;
    
    // 计算网格尺寸
    const gridWidth = Math.ceil(viewportWidth / gs) + 1;
    const gridHeight = Math.ceil(viewportHeight / gs) + 1;
    
    // 初始化网格（每个格子存储占据它的元素ID列表）
    const grid = [];
    for (let y = 0; y < gridHeight; y++) {
      grid[y] = [];
      for (let x = 0; x < gridWidth; x++) {
        grid[y][x] = {
          occupied: false,
          elementIds: [],
          density: 0
        };
      }
    }
    
    // 逐个元素占据网格
    const occupiedCells = new Map(); // 元素ID -> 占据的格子数
    
    for (const element of elements) {
      const { gx, gy, gw, gh } = element.grid;
      const { bounds } = element;
      let cellsOccupied = 0;
      
      for (let dy = 0; dy < gh; dy++) {
        for (let dx = 0; dx < gw; dx++) {
          const cellX = gx + dx;
          const cellY = gy + dy;
          
          // 检查边界
          if (cellX < 0 || cellX >= gridWidth || cellY < 0 || cellY >= gridHeight) continue;
          
          // 计算该格子被元素占据的比例
          const cellRect = {
            x1: cellX * gs,
            y1: cellY * gs,
            x2: (cellX + 1) * gs,
            y2: (cellY + 1) * gs
          };
          
          const intersectRect = {
            x1: Math.max(cellRect.x1, bounds.x),
            y1: Math.max(cellRect.y1, bounds.y),
            x2: Math.min(cellRect.x2, bounds.x + bounds.w),
            y2: Math.min(cellRect.y2, bounds.y + bounds.h)
          };
          
          const intersectArea = Math.max(0, intersectRect.x2 - intersectRect.x1) * 
                               Math.max(0, intersectRect.y2 - intersectRect.y1);
          const cellArea = gs * gs;
          const ratio = intersectArea / cellArea;
          
          if (ratio >= CONFIG.OCCUPANCY_THRESHOLD) {
            const cell = grid[cellY][cellX];
            if (!cell.elementIds.includes(element.id)) {
              cell.elementIds.push(element.id);
              cell.occupied = true;
              cellsOccupied++;
            }
          }
        }
      }
      
      if (cellsOccupied > 0) {
        occupiedCells.set(element.id, cellsOccupied);
      }
    }
    
    // 计算每个格子的密度（占据的元素数量）
    for (let y = 0; y < gridHeight; y++) {
      for (let x = 0; x < gridWidth; x++) {
        grid[y][x].density = grid[y][x].elementIds.length;
      }
    }
    
    const endTime = performance.now();
    
    return {
      grid,
      width: gridWidth,
      height: gridHeight,
      gridSize: gs,
      occupiedCells,
      buildTime: endTime - startTime
    };
  }

  /**
   * 获取指定网格坐标的格子信息
   */
  function getGridCell(occupancyGrid, gx, gy) {
    if (!occupancyGrid) return null;
    const { grid } = occupancyGrid;
    if (gy < 0 || gy >= grid.length || gx < 0 || gx >= grid[0].length) {
      return null;
    }
    return grid[gy][gx];
  }

  /**
   * 获取格子占据的元素
   */
  function getElementsAtCell(occupancyGrid, gx, gy) {
    const cell = getGridCell(occupancyGrid, gx, gy);
    return cell ? cell.elementIds : [];
  }

  /**
   * 检查指定区域是否为空
   */
  function isRegionEmpty(occupancyGrid, gx, gy, gw, gh) {
    for (let dy = 0; dy < gh; dy++) {
      for (let dx = 0; dx < gw; dx++) {
        const cell = getGridCell(occupancyGrid, gx + dx, gy + dy);
        if (cell && cell.occupied) {
          return false;
        }
      }
    }
    return true;
  }

  /**
   * 找到元素占据的所有格子
   */
  function getElementCells(element, occupancyGrid) {
    const { gx, gy, gw, gh } = element.grid;
    const cells = [];
    
    for (let dy = 0; dy < gh; dy++) {
      for (let dx = 0; dx < gw; dx++) {
        cells.push({ x: gx + dx, y: gy + dy });
      }
    }
    
    return cells;
  }

  /**
   * 导出到全局
   */
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.occupancy = {
    buildOccupancyGrid,
    getGridCell,
    getElementsAtCell,
    isRegionEmpty,
    getElementCells,
    CONFIG
  };

})(window);
