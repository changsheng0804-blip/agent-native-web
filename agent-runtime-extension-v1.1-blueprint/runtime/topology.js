/**
 * topology.js - Topology
 * 构建邻近关系、包含关系、对齐关系和导航路径
 */

(function(global) {
  'use strict';

  const CONFIG = {
    NEIGHBOR_DISTANCE: 2, // 距离小于 2 格视为邻近
    OVERLAP_THRESHOLD: 0.8 // 超过 80% 面积重叠视为包含
  };

  /**
   * 计算两个元素的距离
   */
  function calculateDistance(el1, el2) {
    const b1 = el1.bounds;
    const b2 = el2.bounds;
    
    // 计算边界
    const dx = Math.max(0, Math.max(b1.x - (b2.x + b2.w), b2.x - (b1.x + b1.w)));
    const dy = Math.max(0, Math.max(b1.y - (b2.y + b2.h), b2.y - (b1.y + b1.h)));
    
    // 如果重叠，距离为 0
    if (dx === 0 && dy === 0) {
      // 计算重叠面积
      const overlapX = Math.min(b1.x + b1.w, b2.x + b2.w) - Math.max(b1.x, b2.x);
      const overlapY = Math.min(b1.y + b1.h, b2.y + b2.h) - Math.max(b1.y, b2.y);
      return 0;
    }
    
    return Math.sqrt(dx * dx + dy * dy);
  }

  /**
   * 计算网格距离
   */
  function calculateGridDistance(el1, el2) {
    const g1 = el1.grid;
    const g2 = el2.grid;
    
    const dx = Math.max(0, Math.max(g1.gx - (g2.gx + g2.gw), g2.gx - (g1.gx + g1.gw)));
    const dy = Math.max(0, Math.max(g1.gy - (g2.gy + g2.gh), g2.gy - (g1.gy + g1.gh)));
    
    return Math.max(dx, dy);
  }

  /**
   * 获取元素的四个方向最近邻居
   */
  function findNeighbors(element, elements, maxDistance = CONFIG.NEIGHBOR_DISTANCE) {
    let top = null, bottom = null, left = null, right = null;
    let topDist = Infinity, bottomDist = Infinity, leftDist = Infinity, rightDist = Infinity;
    
    const centerY = element.bounds.y + element.bounds.h / 2;
    const centerX = element.bounds.x + element.bounds.w / 2;
    
    for (const other of elements) {
      if (other.id === element.id) continue;
      
      const otherCenterY = other.bounds.y + other.bounds.h / 2;
      const otherCenterX = other.bounds.x + other.bounds.w / 2;
      const gridDist = calculateGridDistance(element, other);
      
      if (gridDist > maxDistance) continue;
      
      // 上方
      if (otherCenterY < centerY && other.bounds.y + other.bounds.h <= element.bounds.y) {
        const dist = centerY - (other.bounds.y + other.bounds.h);
        if (dist < topDist) {
          topDist = dist;
          top = other.id;
        }
      }
      
      // 下方
      if (otherCenterY > centerY && other.bounds.y >= element.bounds.y + element.bounds.h) {
        const dist = other.bounds.y - (element.bounds.y + element.bounds.h);
        if (dist < bottomDist) {
          bottomDist = dist;
          bottom = other.id;
        }
      }
      
      // 左方
      if (otherCenterX < centerX && other.bounds.x + other.bounds.w <= element.bounds.x) {
        const dist = centerX - (other.bounds.x + other.bounds.w);
        if (dist < leftDist) {
          leftDist = dist;
          left = other.id;
        }
      }
      
      // 右方
      if (otherCenterX > centerX && other.bounds.x >= element.bounds.x + element.bounds.w) {
        const dist = other.bounds.x - (element.bounds.x + element.bounds.w);
        if (dist < rightDist) {
          rightDist = dist;
          right = other.id;
        }
      }
    }
    
    return { top, bottom, left, right, distances: { top: topDist, bottom: bottomDist, left: leftDist, right: rightDist } };
  }

  /**
   * 检查 A 是否完全在 B 内部
   */
  function isContainedIn(elementA, elementB) {
    const a = elementA.bounds;
    const b = elementB.bounds;
    
    return (
      a.x >= b.x &&
      a.y >= b.y &&
      a.x + a.w <= b.x + b.w &&
      a.y + a.h <= b.y + b.h
    );
  }

  /**
   * 计算 A 被 B 包含的比例
   */
  function calculateContainmentRatio(elementA, elementB) {
    if (!isContainedIn(elementA, elementB)) return 0;
    
    const aArea = elementA.bounds.w * elementA.bounds.h;
    const bArea = elementB.bounds.w * elementB.bounds.h;
    
    return aArea / bArea;
  }

  /**
   * 找出包含关系
   */
  function findContainmentRelations(elements) {
    const relations = [];
    
    for (const el of elements) {
      for (const other of elements) {
        if (el.id === other.id) continue;
        
        const ratio = calculateContainmentRatio(el, other);
        if (ratio >= CONFIG.OVERLAP_THRESHOLD) {
          relations.push({
            child: el.id,
            parent: other.id,
            containmentRatio: ratio
          });
        }
      }
    }
    
    return relations;
  }

  /**
   * 检查两个元素是否水平对齐
   */
  function isHorizontalAligned(el1, el2, tolerance = 10) {
    return Math.abs(el1.bounds.y - el2.bounds.y) <= tolerance ||
           Math.abs((el1.bounds.y + el1.bounds.h) - (el2.bounds.y + el2.bounds.h)) <= tolerance ||
           Math.abs((el1.bounds.y + el1.bounds.h / 2) - (el2.bounds.y + el2.bounds.h / 2)) <= tolerance;
  }

  /**
   * 检查两个元素是否垂直对齐
   */
  function isVerticalAligned(el1, el2, tolerance = 10) {
    return Math.abs(el1.bounds.x - el2.bounds.x) <= tolerance ||
           Math.abs((el1.bounds.x + el1.bounds.w) - (el2.bounds.x + el2.bounds.w)) <= tolerance ||
           Math.abs((el1.bounds.x + el1.bounds.w / 2) - (el2.bounds.x + el2.bounds.w / 2)) <= tolerance;
  }

  /**
   * 找出对齐关系
   */
  function findAlignmentRelations(elements) {
    const horizontalGroups = [];
    const verticalGroups = [];
    
    // 按 Y 坐标分组（水平对齐）
    const yGroups = new Map();
    for (const el of elements) {
      const yKey = Math.round(el.bounds.y / 20) * 20;
      if (!yGroups.has(yKey)) {
        yGroups.set(yKey, []);
      }
      yGroups.get(yKey).push(el.id);
    }
    
    for (const [y, ids] of yGroups) {
      if (ids.length >= 2) {
        horizontalGroups.push({ y, elements: ids });
      }
    }
    
    // 按 X 坐标分组（垂直对齐）
    const xGroups = new Map();
    for (const el of elements) {
      const xKey = Math.round(el.bounds.x / 20) * 20;
      if (!xGroups.has(xKey)) {
        xGroups.set(xKey, []);
      }
      xGroups.get(xKey).push(el.id);
    }
    
    for (const [x, ids] of xGroups) {
      if (ids.length >= 2) {
        verticalGroups.push({ x, elements: ids });
      }
    }
    
    return { horizontalGroups, verticalGroups };
  }

  /**
   * 构建导航路径（从上到下的阅读流）
   */
  function buildNavigationPath(elements) {
    // 按 Y 坐标排序
    const sortedByY = [...elements].sort((a, b) => a.bounds.y - b.bounds.y);
    
    // 分行
    const rows = [];
    let currentRow = [];
    let currentRowY = null;
    
    for (const el of sortedByY) {
      const elY = el.bounds.y;
      
      if (currentRowY === null) {
        currentRowY = elY;
        currentRow.push(el);
      } else if (Math.abs(elY - currentRowY) < 30) {
        // 同一行
        currentRow.push(el);
      } else {
        // 新行
        if (currentRow.length > 0) {
          rows.push(currentRow);
        }
        currentRow = [el];
        currentRowY = elY;
      }
    }
    
    if (currentRow.length > 0) {
      rows.push(currentRow);
    }
    
    // 每行按 X 排序
    const path = rows.map(row => {
      return row.sort((a, b) => a.bounds.x - b.bounds.x).map(el => el.id);
    });
    
    return path;
  }

  /**
   * 在导航路径中找到从一个元素到另一个元素的路径
   */
  function findPathBetween(fromId, toId, navigationPath, elements) {
    const fromRowIdx = navigationPath.findIndex(row => row.includes(fromId));
    const toRowIdx = navigationPath.findIndex(row => row.includes(toId));
    
    if (fromRowIdx === -1 || toRowIdx === -1) {
      return null;
    }
    
    const path = [];
    
    // 添加起始元素
    const fromRow = navigationPath[fromRowIdx];
    const fromIdx = fromRow.indexOf(fromId);
    path.push(...fromRow.slice(fromIdx));
    
    // 添加中间行
    for (let i = fromRowIdx + 1; i < toRowIdx; i++) {
      const row = navigationPath[i];
      // 添加每行的第一个和最后一个元素作为代表
      path.push(row[0]);
      if (row.length > 1) {
        path.push(row[row.length - 1]);
      }
    }
    
    // 添加目标行
    const toRow = navigationPath[toRowIdx];
    const toIdx = toRow.indexOf(toId);
    path.push(...toRow.slice(0, toIdx + 1));
    
    return path;
  }

  /**
   * 主拓扑构建函数
   */
  function buildTopology(elements) {
    const startTime = performance.now();
    
    // 构建邻居关系
    const neighborsMap = new Map();
    for (const el of elements) {
      neighborsMap.set(el.id, findNeighbors(el, elements));
    }
    
    // 构建包含关系
    const containmentRelations = findContainmentRelations(elements);
    
    // 构建对齐关系
    const alignmentRelations = findAlignmentRelations(elements);
    
    // 构建导航路径
    const navigationPath = buildNavigationPath(elements);
    
    // 构建 DOM 树结构
    const domTree = buildDomTree(elements);
    
    const endTime = performance.now();
    
    return {
      neighbors: Object.fromEntries(neighborsMap),
      containment: containmentRelations,
      alignment: alignmentRelations,
      navigationPath,
      domTree,
      buildTime: endTime - startTime
    };
  }

  /**
   * 构建简化的 DOM 树
   */
  function buildDomTree(elements) {
    const elementMap = new Map(elements.map(e => [e.id, { ...e, children: [] }]));
    const roots = [];
    
    for (const el of elements) {
      const elData = elementMap.get(el.id);
      if (el.parent && elementMap.has(el.parent)) {
        elementMap.get(el.parent).children.push(el.id);
      } else {
        roots.push(el.id);
      }
    }
    
    return {
      roots,
      elements: Object.fromEntries(elementMap)
    };
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.topology = {
    calculateDistance,
    calculateGridDistance,
    findNeighbors,
    isContainedIn,
    findContainmentRelations,
    isHorizontalAligned,
    isVerticalAligned,
    findAlignmentRelations,
    buildNavigationPath,
    findPathBetween,
    buildTopology,
    CONFIG
  };

})(window);
