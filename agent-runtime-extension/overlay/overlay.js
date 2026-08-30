/**
 * overlay.js - Visual Overlay
 * 可视化覆盖层：网格线、元素边框、区域标注、密度热图
 */

(function(global) {
  'use strict';

  const CONFIG = {
    GRID_SIZE: 40,
    OVERLAY_Z_INDEX: 9999,
    COLORS: {
      grid: 'rgba(100, 180, 255, 0.3)',
      gridBold: 'rgba(100, 180, 255, 0.5)',
      element: {
        interactive: 'rgba(76, 175, 80, 0.6)',
        content: 'rgba(33, 150, 243, 0.4)',
        cta: 'rgba(255, 152, 0, 0.6)',
        decorative: 'rgba(158, 158, 158, 0.3)'
      },
      region: 'rgba(233, 30, 99, 0.3)',
      regionBorder: 'rgba(233, 30, 99, 0.8)',
      density: {
        0: 'rgba(76, 175, 80, 0.1)',
        1: 'rgba(255, 235, 59, 0.3)',
        2: 'rgba(255, 152, 0, 0.4)',
        3: 'rgba(244, 67, 54, 0.5)',
        '3+': 'rgba(183, 28, 28, 0.6)'
      }
    }
  };

  let currentMode = 'off';
  let overlayContainer = null;
  let tooltip = null;

  /**
   * 初始化覆盖层容器
   */
  function initOverlay() {
    if (overlayContainer) return;
    
    overlayContainer = document.createElement('div');
    overlayContainer.id = 'agent-runtime-overlay';
    overlayContainer.style.cssText = `
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: ${CONFIG.OVERLAY_Z_INDEX};
      overflow: hidden;
    `;
    
    document.body.appendChild(overlayContainer);
    
    // 创建 tooltip
    tooltip = document.createElement('div');
    tooltip.id = 'agent-runtime-tooltip';
    tooltip.style.cssText = `
      position: fixed;
      padding: 8px 12px;
      background: rgba(0, 0, 0, 0.85);
      color: white;
      font-size: 12px;
      font-family: monospace;
      border-radius: 4px;
      pointer-events: none;
      z-index: ${CONFIG.OVERLAY_Z_INDEX + 1};
      display: none;
      max-width: 300px;
      white-space: pre-wrap;
    `;
    document.body.appendChild(tooltip);
  }

  /**
   * 清除覆盖层内容
   */
  function clearOverlay() {
    if (!overlayContainer) return;
    overlayContainer.innerHTML = '';
  }

  /**
   * 创建 SVG 命名空间
   */
  function createSVG(tag) {
    return document.createElementNS('http://www.w3.org/2000/svg', tag);
  }

  /**
   * 绘制网格线
   */
  function drawGrid(width, height) {
    if (!overlayContainer) return;
    
    const svg = createSVG('svg');
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.style.position = 'absolute';
    svg.style.top = '0';
    svg.style.left = '0';
    
    const gs = CONFIG.GRID_SIZE;
    
    // 垂直线
    for (let x = 0; x <= width; x += gs) {
      const line = createSVG('line');
      line.setAttribute('x1', x);
      line.setAttribute('y1', 0);
      line.setAttribute('x2', x);
      line.setAttribute('y2', height);
      
      const isBold = (x / gs) % 5 === 0;
      line.setAttribute('stroke', isBold ? CONFIG.COLORS.gridBold : CONFIG.COLORS.grid);
      line.setAttribute('stroke-width', isBold ? '2' : '1');
      
      svg.appendChild(line);
    }
    
    // 水平线
    for (let y = 0; y <= height; y += gs) {
      const line = createSVG('line');
      line.setAttribute('x1', 0);
      line.setAttribute('y1', y);
      line.setAttribute('x2', width);
      line.setAttribute('y2', y);
      
      const isBold = (y / gs) % 5 === 0;
      line.setAttribute('stroke', isBold ? CONFIG.COLORS.gridBold : CONFIG.COLORS.grid);
      line.setAttribute('stroke-width', isBold ? '2' : '1');
      
      svg.appendChild(line);
    }
    
    overlayContainer.appendChild(svg);
  }

  /**
   * 绘制元素边框
   */
  function drawElements(elements) {
    if (!overlayContainer) return;
    
    const svg = createSVG('svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.position = 'absolute';
    svg.style.top = '0';
    svg.style.left = '0';
    
    for (const el of elements) {
      const { x, y, w, h } = el.bounds;
      
      // 确定颜色
      let color = CONFIG.COLORS.element.content;
      if (el.interactive) {
        color = el.semantic === 'cta' ? CONFIG.COLORS.element.cta : CONFIG.COLORS.element.interactive;
      }
      
      // 创建矩形
      const rect = createSVG('rect');
      rect.setAttribute('x', x);
      rect.setAttribute('y', y);
      rect.setAttribute('width', w);
      rect.setAttribute('height', h);
      rect.setAttribute('fill', 'none');
      rect.setAttribute('stroke', color);
      rect.setAttribute('stroke-width', el.interactive ? '2' : '1');
      rect.setAttribute('rx', '2');
      rect.dataset.elementId = el.id;
      rect.dataset.semantic = el.semantic;
      rect.dataset.text = el.text || '';
      
      // 添加交互事件
      rect.style.cursor = 'pointer';
      rect.style.pointerEvents = 'all';
      rect.addEventListener('mouseenter', showTooltip);
      rect.addEventListener('mouseleave', hideTooltip);
      
      svg.appendChild(rect);
      
      // 如果有语义标签，添加标签
      if (el.semantic && w > 30 && h > 15) {
        const label = createSVG('text');
        label.setAttribute('x', x + 4);
        label.setAttribute('y', y + 12);
        label.setAttribute('fill', 'white');
        label.setAttribute('font-size', '9');
        label.setAttribute('font-family', 'monospace');
        label.setAttribute('pointer-events', 'none');
        label.textContent = el.semantic;
        svg.appendChild(label);
      }
    }
    
    overlayContainer.appendChild(svg);
  }

  /**
   * 绘制空白区域
   */
  function drawRegions(regionsData) {
    if (!overlayContainer || !regionsData.regions) return;
    
    const svg = createSVG('svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.position = 'absolute';
    svg.style.top = '0';
    svg.style.left = '0';
    
    for (const region of regionsData.regions) {
      const { x, y, width, height } = region.pixel;
      
      // 填充
      const rect = createSVG('rect');
      rect.setAttribute('x', x);
      rect.setAttribute('y', y);
      rect.setAttribute('width', width);
      rect.setAttribute('height', height);
      rect.setAttribute('fill', CONFIG.COLORS.region);
      rect.dataset.regionId = region.id;
      rect.dataset.score = region.usage.score;
      rect.dataset.types = region.usage.recommendedTypes.join(', ');
      
      rect.addEventListener('mouseenter', showRegionTooltip);
      rect.addEventListener('mouseleave', hideTooltip);
      
      svg.appendChild(rect);
      
      // 边框
      const border = createSVG('rect');
      border.setAttribute('x', x);
      border.setAttribute('y', y);
      border.setAttribute('width', width);
      border.setAttribute('height', height);
      border.setAttribute('fill', 'none');
      border.setAttribute('stroke', CONFIG.COLORS.regionBorder);
      border.setAttribute('stroke-width', '2');
      border.setAttribute('stroke-dasharray', '5,5');
      svg.appendChild(border);
      
      // 标签
      if (width > 50 && height > 30) {
        const label = createSVG('text');
        label.setAttribute('x', x + width / 2);
        label.setAttribute('y', y + height / 2);
        label.setAttribute('fill', 'white');
        label.setAttribute('font-size', '10');
        label.setAttribute('font-family', 'monospace');
        label.setAttribute('text-anchor', 'middle');
        label.setAttribute('dominant-baseline', 'middle');
        label.setAttribute('pointer-events', 'none');
        label.textContent = `${region.usage.recommendedTypes[0] || 'empty'} (${region.usage.score.toFixed(2)})`;
        svg.appendChild(label);
      }
    }
    
    overlayContainer.appendChild(svg);
  }

  /**
   * 绘制密度热图
   */
  function drawDensityMap(densityData) {
    if (!overlayContainer || !densityData.map) return;
    
    const svg = createSVG('svg');
    svg.setAttribute('width', '100%');
    svg.setAttribute('height', '100%');
    svg.style.position = 'absolute';
    svg.style.top = '0';
    svg.style.left = '0';
    
    const gs = CONFIG.GRID_SIZE;
    
    for (let y = 0; y < densityData.map.length; y++) {
      for (let x = 0; x < densityData.map[y].length; x++) {
        const density = densityData.map[y][x];
        
        if (density > 0) {
          let color;
          if (density >= 3) {
            color = CONFIG.COLORS.density['3+'];
          } else {
            color = CONFIG.COLORS.density[density];
          }
          
          const rect = createSVG('rect');
          rect.setAttribute('x', x * gs);
          rect.setAttribute('y', y * gs);
          rect.setAttribute('width', gs);
          rect.setAttribute('height', gs);
          rect.setAttribute('fill', color);
          rect.setAttribute('pointer-events', 'none');
          
          svg.appendChild(rect);
        }
      }
    }
    
    overlayContainer.appendChild(svg);
  }

  /**
   * 显示元素 tooltip
   */
  function showTooltip(e) {
    if (!tooltip) return;
    
    const rect = e.target;
    const info = {
      id: rect.dataset.elementId,
      semantic: rect.dataset.semantic,
      text: rect.dataset.text
    };
    
    tooltip.innerHTML = `<strong>${info.id}</strong>
 semantic: ${info.semantic}
 text: ${info.text.slice(0, 50)}${info.text.length > 50 ? '...' : ''}`;
    
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 10) + 'px';
    tooltip.style.top = (e.clientY + 10) + 'px';
  }

  /**
   * 显示区域 tooltip
   */
  function showRegionTooltip(e) {
    if (!tooltip) return;
    
    const rect = e.target;
    const info = {
      id: rect.dataset.regionId,
      score: rect.dataset.score,
      types: rect.dataset.types
    };
    
    tooltip.innerHTML = `<strong>Region: ${info.id}</strong>
 Score: ${info.score}
 Types: ${info.types}`;
    
    tooltip.style.display = 'block';
    tooltip.style.left = (e.clientX + 10) + 'px';
    tooltip.style.top = (e.clientY + 10) + 'px';
  }

  /**
   * 隐藏 tooltip
   */
  function hideTooltip() {
    if (!tooltip) return;
    tooltip.style.display = 'none';
  }

  /**
   * 切换覆盖层模式
   */
  function toggleOverlay(mode, worldState) {
    initOverlay();
    clearOverlay();
    currentMode = mode;
    
    if (mode === 'off') {
      if (overlayContainer) {
        overlayContainer.style.display = 'none';
      }
      return;
    }
    
    if (overlayContainer) {
      overlayContainer.style.display = 'block';
    }
    
    const width = window.innerWidth;
    const height = window.innerHeight;
    
    switch (mode) {
      case 'grid':
        drawGrid(width, height);
        break;
        
      case 'elements':
        drawGrid(width, height);
        if (worldState && worldState.elements) {
          drawElements(worldState.elements);
        }
        break;
        
      case 'regions':
        if (worldState && worldState.regions) {
          drawRegions(worldState.regions);
        }
        break;
        
      case 'density':
        if (worldState && worldState.occupancyGrid) {
          const densityData = global.AgentRuntime.regions.generateDensityMap(worldState.occupancyGrid);
          drawDensityMap(densityData);
        }
        break;
        
      case 'all':
        drawGrid(width, height);
        if (worldState && worldState.elements) {
          drawElements(worldState.elements);
        }
        if (worldState && worldState.regions) {
          drawRegions(worldState.regions);
        }
        break;
        
      default:
        break;
    }
  }

  /**
   * 获取当前模式
   */
  function getOverlayMode() {
    return currentMode;
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.overlay = {
    toggleOverlay,
    getOverlayMode,
    initOverlay,
    clearOverlay,
    CONFIG
  };

})(window);
