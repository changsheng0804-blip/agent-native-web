window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  /**
   * 构建语义索引
   */
  function buildSemanticIndex(elements) {
    const byRole = {};
    const byRegion = {};
    
    elements.forEach(el => {
      // 按角色分组
      if (!byRole[el.semantic]) byRole[el.semantic] = [];
      byRole[el.semantic].push(el.id);
      
      // 按区域分组（用 y 范围划分：header/body/footer）
      const region = getRegion(el);
      if (!byRegion[region]) byRegion[region] = [];
      byRegion[region].push(el.id);
    });
    
    return { byRole, byRegion };
  }

  function getRegion(el) {
    const viewportH = window.innerHeight;
    const y = el.bounds.y;
    if (y < viewportH * 0.15) return 'header';
    if (y > document.body.scrollHeight - viewportH * 0.15) return 'footer';
    return 'body';
  }

  /**
   * 计算注意力权重
   */
  function calculateAttentionWeights(elements) {
    const maxArea = Math.max(...elements.map(e => e.bounds.w * e.bounds.h), 1);
    
    elements.forEach(el => {
      const sizeScore = (el.bounds.w * el.bounds.h) / maxArea;
      const positionScore = el.inViewport ? 1 : 0.3;
      const interactiveScore = el.interactive ? 1.5 : 1;
      const semanticScore = ['button','cta','link','navigation'].includes(el.semantic) ? 1.3 : 1;
      
      el.attentionWeight = Math.min(1, sizeScore * 0.3 + positionScore * 0.2 + interactiveScore * 0.3 + semanticScore * 0.2);
    });
    
    // 排序
    return [...elements].sort((a, b) => b.attentionWeight - a.attentionWeight);
  }

  /**
   * 生成自然语言布局描述
   */
  function generateLayoutDescription(elements, semanticIndex) {
    const { byRole, byRegion } = semanticIndex;
    const parts = [];
    
    const total = elements.length;
    const interactive = elements.filter(e => e.interactive).length;
    const inViewport = elements.filter(e => e.inViewport).length;
    
    parts.push(`页面包含 ${total} 个元素，${interactive} 个可交互，${inViewport} 个在当前视口内。`);
    
    // 描述语义结构
    const roles = Object.entries(byRole).sort((a,b) => b[1].length - a[1].length);
    if (roles.length > 0) {
      parts.push(`语义类型: ${roles.map(([r, ids]) => `${r}(${ids.length})`).join(', ')}。`);
    }
    
    // 描述空间布局
    const headerEls = byRegion.header || [];
    const footerEls = byRegion.footer || [];
    const bodyEls = byRegion.body || [];
    
    if (headerEls.length > 0) parts.push(`页头区域有 ${headerEls.length} 个元素。`);
    if (bodyEls.length > 0) parts.push(`主体区域有 ${bodyEls.length} 个元素。`);
    if (footerEls.length > 0) parts.push(`页脚区域有 ${footerEls.length} 个元素。`);
    
    // 描述密度
    const viewportWidth = window.innerWidth;
    const avgWidth = elements.reduce((s, e) => s + e.bounds.w, 0) / Math.max(total, 1);
    if (avgWidth > viewportWidth * 0.6) parts.push(`元素多为全宽布局。`);
    else if (avgWidth < viewportWidth * 0.3) parts.push(`元素多为窄列布局。`);
    
    return parts.join(' ');
  }

  /**
   * 名字去重：同名构件追加序号（button.card -> button.card-2）
   * 保证弱 ID 在任意时刻全局唯一，CAD 图纸编号原则
   */
  function dedupeNames(elements) {
    const seen = new Map();
    elements.forEach(el => {
      const n = seen.get(el.name) || 0;
      seen.set(el.name, n + 1);
      if (n > 0) el.name = `${el.name}-${n + 1}`;
    });
  }

  global.AgentRuntime.semantics = { buildSemanticIndex, calculateAttentionWeights, generateLayoutDescription, dedupeNames };
})(window);
