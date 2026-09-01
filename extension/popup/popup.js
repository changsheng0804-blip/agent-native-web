/**
 * popup.js - Plugin Popup Logic
 */

(function() {
  'use strict';
  
  let currentMode = 'off';
  
  // DOM 元素
  const statElements = document.getElementById('stat-elements');
  const statInteractive = document.getElementById('stat-interactive');
  const statRegions = document.getElementById('stat-regions');
  const healthFill = document.getElementById('health-fill');
  const searchInput = document.getElementById('search-input');
  const searchResults = document.getElementById('search-results');
  const noResults = document.getElementById('no-results');
  const modeButtons = document.querySelectorAll('.mode-btn');
  const refreshBtn = document.getElementById('refresh-btn');
  const exportBtn = document.getElementById('export-btn');
  
  /**
   * 初始化 popup
   */
  async function init() {
    // 等待页面脚本加载完成
    await waitForAgentWorld();
    
    // 加载统计数据
    loadStats();
    
    // 设置事件监听
    setupEventListeners();
  }
  
  /**
   * 等待 window.agentWorld 就绪
   */
  function waitForAgentWorld() {
    return new Promise((resolve) => {
      const check = () => {
        if (window.agentWorld) {
          resolve();
        } else {
          setTimeout(check, 100);
        }
      };
      check();
    });
  }
  
  /**
   * 加载统计数据
   */
  function loadStats() {
    if (!window.agentWorld) return;
    
    const summary = window.agentWorld.query.getPageSummary();
    
    statElements.textContent = summary.statistics.totalElements;
    statInteractive.textContent = summary.statistics.interactiveElements;
    statRegions.textContent = summary.statistics.emptyRegions;
    
    // 健康分数
    const healthScore = Math.round(summary.density * 100);
    healthFill.style.width = healthScore + '%';
    
    // 根据分数设置颜色
    if (healthScore >= 70) {
      healthFill.style.background = '#4caf50';
    } else if (healthScore >= 40) {
      healthFill.style.background = '#ff9800';
    } else {
      healthFill.style.background = '#f44336';
    }
  }
  
  /**
   * 设置事件监听
   */
  function setupEventListeners() {
    // 模式切换
    modeButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.mode;
        setOverlayMode(mode);
        
        modeButtons.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });
    
    // 搜索
    let searchTimeout;
    searchInput.addEventListener('input', (e) => {
      clearTimeout(searchTimeout);
      searchTimeout = setTimeout(() => {
        performSearch(e.target.value);
      }, 300);
    });
    
    // 刷新
    refreshBtn.addEventListener('click', () => {
      if (window.agentWorld) {
        window.agentWorld.refresh();
        setTimeout(loadStats, 200);
      }
    });
    
    // 导出
    exportBtn.addEventListener('click', exportSnapshot);
  }
  
  /**
   * 设置覆盖层模式
   */
  function setOverlayMode(mode) {
    currentMode = mode;
    if (window.agentWorld) {
      window.agentWorld.toggleOverlay(mode);
    }
  }
  
  /**
   * 执行搜索
   */
  function performSearch(query) {
    if (!window.agentWorld || !query.trim()) {
      searchResults.classList.remove('show');
      return;
    }
    
    const results = window.agentWorld.query.search({
      role: query.trim(),
      limit: 20
    });
    
    // 获取元素详情
    const elements = results.map(id => window.agentWorld.query.getElement(id)).filter(Boolean);
    
    if (elements.length === 0) {
      noResults.textContent = '未找到匹配元素';
      noResults.style.display = 'block';
      searchResults.querySelectorAll('.result-item').forEach(el => el.remove());
    } else {
      noResults.style.display = 'none';
      
      // 清除旧结果
      searchResults.querySelectorAll('.result-item').forEach(el => el.remove());
      
      // 添加新结果
      elements.forEach(el => {
        const item = document.createElement('div');
        item.className = 'result-item';
        item.innerHTML = `
          <div class="semantic">[${el.semantic || el.role || el.tag}] ${el.id}</div>
          <div class="text">${el.text || '(无文本)'}</div>
        `;
        item.addEventListener('click', () => {
          // 高亮元素
          highlightElement(el.id);
        });
        searchResults.appendChild(item);
      });
    }
    
    searchResults.classList.add('show');
  }
  
  /**
   * 高亮元素（通过临时开启 elements 模式并滚动到元素）
   */
  function highlightElement(elementId) {
    const el = window.agentWorld.elements.find(e => e.id === elementId);
    if (el) {
      // 滚动到元素
      const domEl = document.querySelector(`[id="${elementId}"], [data-id="${elementId}"]`);
      if (domEl) {
        domEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      
      // 临时高亮
      window.agentWorld.toggleOverlay('elements');
      setTimeout(() => {
        if (currentMode !== 'off') {
          window.agentWorld.toggleOverlay(currentMode);
        }
      }, 2000);
    }
  }
  
  /**
   * 导出快照
   */
  function exportSnapshot() {
    if (!window.agentWorld) return;
    
    const snapshot = window.agentWorld.query.getSnapshot();
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    
    const a = document.createElement('a');
    a.href = url;
    a.download = `agent-world-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
  
  // 初始化
  init();
})();
