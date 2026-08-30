/**
 * regions.js - Region Detection
 * 检测页面空白区域并评估
 */

(function(global) {
  'use strict';

  const CONFIG = {
    MIN_REGION_SIZE: 2, // 最小区域大小（格子数）
    MERGE_THRESHOLD: 1, // 合并相邻空白的阈值
    GRID_SIZE: 40,
    // 推荐用途阈值
    USAGE_RECOMMENDATIONS: {
      small: { min: 2, max: 4, types: ['gap', 'spacing', 'icon-slot'] },
      medium: { min: 5, max: 12, types: ['widget', 'avatar', 'thumbnail', 'button-group'] },
      large: { min: 13, max: 25, types: ['card', 'sidebar-widget', 'modal-content'] },
      xlarge: { min: 26, max: Infinity, types: ['section', 'panel', 'new-content-area'] }
    }
  };

  /**
   * 扫描连续的空白区域
   */
  function scanEmptyRegions(occupancyGrid) {
    const { grid, width, height } = occupancyGrid;
    const visited = new Set();
    const regions = [];
    
    // 四方向移动
    const directions = [
      [0, 1], [1, 0], [0, -1], [-1, 0]
    ];
    
    // BFS 扫描连续空白区域
    function bfs(startX, startY) {
      const cells = [];
      const queue = [[startX, startY]];
      const key = `${startX},${startY}`;
      
      visited.add(key);
      
      while (queue.length > 0) {
        const [x, y] = queue.shift();
        cells.push({ x, y });
        
        for (const [dx, dy] of directions) {
          const nx = x + dx;
          const ny = y + dy;
          const nkey = `${nx},${ny}`;
          
          if (nx >= 0 && nx < width && ny >= 0 && ny < height &&
              !visited.has(nkey) && !grid[ny][nx].occupied) {
            visited.add(nkey);
            queue.push([nx, ny]);
          }
        }
      }
      
      return cells;
    }
    
    // 遍历所有格子
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const key = `${x},${y}`;
        if (!visited.has(key) && !grid[y][x].occupied) {
          const cells = bfs(x, y);
          if (cells.length >= CONFIG.MIN_REGION_SIZE) {
            regions.push(cells);
          }
        }
      }
    }
    
    return regions;
  }

  /**
   * 将连续格子合并为矩形
   */
  function mergeToRectangles(cells) {
    if (cells.length === 0) return [];
    
    // 找边界
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const cell of cells) {
      minX = Math.min(minX, cell.x);
      minY = Math.min(minY, cell.y);
      maxX = Math.max(maxX, cell.x);
      maxY = Math.max(maxY, cell.y);
    }
    
    // 检查是否形成完整的矩形（可能有内部空洞）
    const width = maxX - minX + 1;
    const height = maxY - minY + 1;
    const cellSet = new Set(cells.map(c => `${c.x},${c.y}`));
    
    // 计算实际空格子数
    let actualEmptyCells = 0;
    for (let y = minY; y <= maxY; y++) {
      for (let x = minX; x <= maxX; x++) {
        if (cellSet.has(`${x},${y}`)) {
          actualEmptyCells++;
        }
      }
    }
    
    const totalCells = width * height;
    const fillRatio = actualEmptyCells / totalCells;
    
    // 如果填充率很低，可能是不规则区域，拆分成多个矩形
    if (fillRatio < 0.7) {
      // 返回紧凑边界
      return [{
        x: minX,
        y: minY,
        width,
        height,
        cells: actualEmptyCells,
        totalCells,
        fillRatio,
        shape: 'irregular'
      }];
    }
    
    return [{
      x: minX,
      y: minY,
      width,
      height,
      cells: actualEmptyCells,
      totalCells,
      fillRatio,
      shape: fillRatio > 0.95 ? 'rectangle' : 'almost-rectangle'
    }];
  }

  /**
   * 评估区域的推荐用途
   */
  function recommendUsage(region, occupiedElements, viewportWidth, viewportHeight) {
    const area = region.width * region.height;
    
    // 像素尺寸
    const pixelWidth = region.width * CONFIG.GRID_SIZE;
    const pixelHeight = region.height * CONFIG.GRID_SIZE;
    
    // 位置评估
    const position = {
      isTop: region.y < 3,
      isBottom: region.y > Math.ceil(viewportHeight / CONFIG.GRID_SIZE) - 5,
      isLeft: region.x < 3,
      isRight: region.x > Math.ceil(viewportWidth / CONFIG.GRID_SIZE) - 5,
      isCenter: region.x > 5 && region.x < Math.ceil(viewportWidth / CONFIG.GRID_SIZE) - 10
    };
    
    // 找出最近的占据元素
    let nearestElement = null;
    let nearestDistance = Infinity;
    
    for (const el of occupiedElements) {
      const elCenterX = el.bounds.x + el.bounds.w / 2;
      const elCenterY = el.bounds.y + el.bounds.h / 2;
      
      const regionCenterX = region.x * CONFIG.GRID_SIZE + pixelWidth / 2;
      const regionCenterY = region.y * CONFIG.GRID_SIZE + pixelHeight / 2;
      
      const dist = Math.sqrt(
        Math.pow(elCenterX - regionCenterX, 2) +
        Math.pow(elCenterY - regionCenterY, 2)
      );
      
      if (dist < nearestDistance) {
        nearestDistance = dist;
        nearestElement = el;
      }
    }
    
    // 根据大小推荐类型
    let recommendedTypes = [];
    for (const [size, config] of Object.entries(CONFIG.USAGE_RECOMMENDATIONS)) {
      if (area >= config.min && area <= config.max) {
        recommendedTypes = config.types;
        break;
      }
    }
    
    // 根据位置调整推荐
    if (position.isTop && recommendedTypes.includes('section')) {
      recommendedTypes.unshift('header-space');
    }
    if (position.isBottom && recommendedTypes.includes('section')) {
      recommendedTypes.push('footer-space');
    }
    if (position.isLeft || position.isRight) {
      recommendedTypes = recommendedTypes.filter(t => !['section', 'panel', 'new-content-area'].includes(t));
      if (!recommendedTypes.includes('sidebar-widget')) {
        recommendedTypes.unshift('sidebar-widget');
      }
    }
    
    return {
      area,
      pixelSize: { width: pixelWidth, height: pixelHeight },
      position,
      nearestElement: nearestElement ? { id: nearestElement.id, distance: nearestDistance } : null,
      recommendedTypes,
      score: calculatePlacementScore(region, position, area)
    };
  }

  /**
   * 计算放置分数（越高越适合放置内容）
   */
  function calculatePlacementScore(region, position, area) {
    let score = 0.5; // 基础分数
    
    // 面积适中分数更高（太小放不了内容，太大浪费）
    if (area >= 5 && area <= 15) {
      score += 0.2;
    } else if (area > 15 && area <= 30) {
      score += 0.1;
    }
    
    // 位置分数
    if (position.isCenter) {
      score += 0.2; // 中心位置好
    }
    
    // 边缘位置分数
    if (position.isTop || position.isBottom) {
      score += 0.1;
    }
    
    // 宽型区域适合 banner
    if (region.width > region.height * 2) {
      score += 0.1;
    }
    
    // 方型区域适合 widget
    if (Math.abs(region.width - region.height) < 3) {
      score += 0.1;
    }
    
    return Math.min(score, 1);
  }

  /**
   * 检测所有空白区域
   */
  function detectRegions(occupancyGrid, elements) {
    const startTime = performance.now();
    
    // 扫描连续空白区域
    const rawRegions = scanEmptyRegions(occupancyGrid);
    
    // 合并为矩形
    const rectangles = [];
    for (const region of rawRegions) {
      rectangles.push(...mergeToRectangles(region));
    }
    
    // 评估每个区域
    const viewportWidth = occupancyGrid.width * CONFIG.GRID_SIZE;
    const viewportHeight = occupancyGrid.height * CONFIG.GRID_SIZE;
    
    const evaluatedRegions = rectangles.map((rect, index) => {
      const usage = recommendUsage(rect, elements, viewportWidth, viewportHeight);
      return {
        id: `region_${index}`,
        grid: rect,
        pixel: {
          x: rect.x * CONFIG.GRID_SIZE,
          y: rect.y * CONFIG.GRID_SIZE,
          width: rect.width * CONFIG.GRID_SIZE,
          height: rect.height * CONFIG.GRID_SIZE
        },
        shape: rect.shape,
        fillRatio: rect.fillRatio,
        usage,
        index
      };
    });
    
    // 按分数排序
    evaluatedRegions.sort((a, b) => b.usage.score - a.usage.score);
    
    const endTime = performance.now();
    
    return {
      regions: evaluatedRegions,
      totalEmptyCells: rawRegions.reduce((sum, r) => sum + r.length, 0),
      regionCount: evaluatedRegions.length,
      detectTime: endTime - startTime
    };
  }

  /**
   * 获取密度热图
   */
  function generateDensityMap(occupancyGrid) {
    const { grid, width, height } = occupancyGrid;
    const densityMap = [];
    
    // 统计各密度等级
    const densityLevels = { 0: 0, 1: 0, 2: 0, 3: 0, '3+': 0 };
    
    for (let y = 0; y < height; y++) {
      const row = [];
      for (let x = 0; x < width; x++) {
        const density = grid[y][x].density;
        row.push(density);
        
        if (density === 0) densityLevels[0]++;
        else if (density === 1) densityLevels[1]++;
        else if (density === 2) densityLevels[2]++;
        else if (density === 3) densityLevels[3]++;
        else densityLevels['3+']++;
      }
      densityMap.push(row);
    }
    
    // 计算健康分数（基于密度均匀性）
    const totalCells = width * height;
    const emptyRatio = densityLevels[0] / totalCells;
    const crowdedRatio = (densityLevels['3+'] + densityLevels[3]) / totalCells;
    
    // 健康分数：空白比例和拥挤比例适中
    let healthScore = 1;
    if (emptyRatio < 0.1) healthScore -= 0.3; // 太挤
    if (emptyRatio > 0.6) healthScore -= 0.2; //太空
    if (crowdedRatio > 0.3) healthScore -= 0.2; // 太挤
    healthScore = Math.max(0, Math.min(1, healthScore));
    
    return {
      map: densityMap,
      densityLevels,
      healthScore,
      statistics: {
        totalCells,
        emptyCells: densityLevels[0],
        occupiedCells: totalCells - densityLevels[0],
        averageDensity: (totalCells - densityLevels[0]) / (totalCells || 1)
      }
    };
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.regions = {
    scanEmptyRegions,
    mergeToRectangles,
    recommendUsage,
    detectRegions,
    generateDensityMap,
    CONFIG
  };

})(window);
