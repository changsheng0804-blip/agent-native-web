window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let currentMode = 'off';
  let overlayContainer = null;

  function createContainer() {
    if (overlayContainer) return overlayContainer;
    overlayContainer = document.createElement('div');
    overlayContainer.id = 'agent-runtime-overlay';
    overlayContainer.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:999999;';
    document.body.appendChild(overlayContainer);
    return overlayContainer;
  }

  function clearOverlay() {
    const container = document.getElementById('agent-runtime-overlay');
    if (container) container.innerHTML = '';
  }

  function toggleOverlay(mode, world) {
    clearOverlay();
    currentMode = mode;
    
    if (mode === 'off') return;
    
    const container = createContainer();
    const elements = [...world.elements.values()];
    
    switch(mode) {
      case 'grid': drawGrid(container); break;
      case 'elements': drawElements(container, elements); break;
      case 'regions': drawRegions(container, world); break;
      case 'all':
        drawGrid(container);
        drawElements(container, elements);
        drawRegions(container, world);
        break;
    }
  }

  function drawGrid(container) {
    const GRID = 40;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;';
    svg.setAttribute('width', window.innerWidth);
    svg.setAttribute('height', document.body.scrollHeight);
    
    for (let x = 0; x <= window.innerWidth; x += GRID) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', x); line.setAttribute('y1', 0);
      line.setAttribute('x2', x); line.setAttribute('y2', document.body.scrollHeight);
      line.setAttribute('stroke', 'rgba(125,211,252,0.1)'); line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    }
    for (let y = 0; y <= document.body.scrollHeight; y += GRID) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', 0); line.setAttribute('y1', y);
      line.setAttribute('x2', window.innerWidth); line.setAttribute('y2', y);
      line.setAttribute('stroke', 'rgba(125,211,252,0.1)'); line.setAttribute('stroke-width', '1');
      svg.appendChild(line);
    }
    container.appendChild(svg);
  }

  function drawElements(container, elements) {
    const colorMap = {
      navigation: '#7dd3fc', button: '#86efac', link: '#86efac',
      input: '#fbbf24', heading: '#c084fc', banner: '#7dd3fc',
      contentinfo: '#f87171', dialog: '#fb923c', card: '#a78bfa',
      content: '#64748b', img: '#f472b6'
    };
    
    elements.forEach(el => {
      if (!el.inViewport) return;
      const div = document.createElement('div');
      const color = colorMap[el.semantic] || '#64748b';
      div.style.cssText = `position:absolute;left:${el.bounds.x}px;top:${el.bounds.y}px;width:${el.bounds.w}px;height:${el.bounds.h}px;border:1px solid ${color};border-radius:2px;pointer-events:none;`;
      
      // 标签
      const label = document.createElement('span');
      label.style.cssText = `position:absolute;top:-14px;left:0;font-size:9px;font-family:monospace;color:${color};background:rgba(0,0,0,0.7);padding:1px 4px;border-radius:2px;white-space:nowrap;`;
      label.textContent = el.semantic;
      div.appendChild(label);
      
      container.appendChild(div);
    });
  }

  function drawRegions(container, world) {
    if (!world.query) return;
    const regions = world.query.findEmptyRegions(10);
    
    regions.forEach(region => {
      const GRID = 40;
      const div = document.createElement('div');
      div.style.cssText = `position:absolute;left:${region.bounds.gx1*GRID}px;top:${region.bounds.gy1*GRID}px;width:${(region.bounds.gx2-region.bounds.gx1+1)*GRID}px;height:${(region.bounds.gy2-region.bounds.gy1+1)*GRID}px;border:2px dashed rgba(251,191,36,0.4);background:rgba(251,191,36,0.05);border-radius:4px;pointer-events:none;`;
      
      const label = document.createElement('span');
      label.style.cssText = 'position:absolute;top:4px;left:4px;font-size:10px;font-family:monospace;color:#fbbf24;opacity:0.7;';
      label.textContent = `${region.cells}格`;
      div.appendChild(label);
      
      container.appendChild(div);
    });
  }

  global.AgentRuntime.overlay = { toggleOverlay, clearOverlay };
})(window);
