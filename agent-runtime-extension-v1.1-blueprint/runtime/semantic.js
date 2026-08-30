/**
 * semantic.js - Semantic Tree
 * 语义推断、注意力权重计算、区域归属
 */

(function(global) {
  'use strict';

  const CONFIG = {
    ABOVE_FOLD_THRESHOLD: 0.7, // 视口高度的 70% 以内视为首屏
    ATTENTION_WEIGHTS: {
      interactive: 1.5,
      cta: 1.3,
      heading: 1.2,
      image: 0.9,
      text: 0.7,
      decorative: 0.3
    }
  };

  /**
   * 语义角色推断
   */
  function inferSemanticRole(element) {
    // 已经在 scanner 中推断过，这里可以进一步细化
    const { role, semantic, tag, interactive } = element;
    
    // CTA 按钮识别
    if (tag === 'button' || role === 'button') {
      const text = (element.text || '').toLowerCase();
      const ctaKeywords = ['submit', 'login', 'sign up', '注册', '登录', '购买', 'buy', 'get', 'start', '开始', '立即', 'now'];
      if (ctaKeywords.some(k => text.includes(k))) {
        return 'cta';
      }
      return 'button';
    }
    
    // 已有语义直接返回
    if (semantic) return semantic;
    if (role) return role;
    
    // 根据标签推断
    const tagSemanticMap = {
      'h1': 'title',
      'h2': 'heading',
      'h3': 'subheading',
      'h4': 'subheading',
      'h5': 'subheading',
      'h6': 'subheading',
      'p': 'paragraph',
      'ul': 'list',
      'ol': 'list',
      'li': 'list-item',
      'img': 'image',
      'video': 'video',
      'audio': 'audio',
      'canvas': 'canvas',
      'svg': 'vector-graphic'
    };
    
    if (tagSemanticMap[tag]) return tagSemanticMap[tag];
    
    return 'content';
  }

  /**
   * 计算注意力权重
   */
  function calculateAttentionWeight(element, viewportHeight) {
    let weight = 0.5; // 基础权重
    
    // 大小权重 - 面积越大越显眼
    const area = element.bounds.w * element.bounds.h;
    const normalizedArea = Math.min(area / 10000, 1); // 标准化到 100x100
    weight += normalizedArea * 0.2;
    
    // 位置权重 - 首屏元素权重更高
    const { y, h } = element.bounds;
    const elementCenterY = y + h / 2;
    if (elementCenterY < viewportHeight * CONFIG.ABOVE_FOLD_THRESHOLD) {
      weight += 0.15;
      // 越靠上权重越高
      weight += (1 - elementCenterY / (viewportHeight * CONFIG.ABOVE_FOLD_THRESHOLD)) * 0.1;
    }
    
    // 交互权重
    if (element.interactive) {
      weight += 0.2;
    }
    
    // 语义权重
    const semantic = inferSemanticRole(element);
    const semanticWeights = {
      'cta': 0.25,
      'button': 0.15,
      'link': 0.1,
      'title': 0.15,
      'heading': 0.1,
      'navigation': 0.1,
      'form': 0.15,
      'image': 0.05,
      'video': 0.1,
      'input': 0.1
    };
    
    if (semanticWeights[semantic]) {
      weight += semanticWeights[semantic];
    }
    
    // 文本长度权重（适中最好，太长或太短权重降低）
    const textLen = (element.text || '').length;
    if (textLen > 10 && textLen < 200) {
      weight += 0.05;
    }
    
    return Math.min(weight, 1);
  }

  /**
   * 检测元素所属区域
   */
  function detectRegion(element, elements, occupancyGrid) {
    const { parent, bounds, id } = element;
    
    // 1. 同一表单内的元素属于同一区域
    if (parent) {
      const parentEl = elements.find(e => e.id === parent);
      if (parentEl) {
        const parentRole = inferSemanticRole(parentEl);
        if (parentRole === 'form') {
          return {
            type: 'form',
            containerId: parent,
            reason: 'same-form'
          };
        }
        if (parentRole === 'modal' || parentRole === 'dialog') {
          return {
            type: 'modal',
            containerId: parent,
            reason: 'in-modal'
          };
        }
      }
    }
    
    // 2. 同一语义容器的元素属于同一区域
    const semantic = inferSemanticRole(element);
    if (['navigation', 'header', 'footer', 'sidebar', 'main', 'article', 'section'].includes(semantic)) {
      return {
        type: semantic,
        containerId: id,
        reason: 'semantic-container'
      };
    }
    
    // 3. 空间分组 - 水平对齐的元素
    const sameRowElements = elements.filter(e => {
      if (e.id === id) return false;
      // Y 轴重叠超过 50%
      const overlapY = Math.min(bounds.y + bounds.h, e.bounds.y + e.bounds.h) - 
                       Math.max(bounds.y, e.bounds.y);
      return overlapY > bounds.h * 0.5;
    });
    
    if (sameRowElements.length > 2) {
      // 找最近的元素，确定行号
      const rowStart = Math.min(...sameRowElements.map(e => Math.min(e.bounds.x, bounds.x)));
      const rowIndex = sameRowElements.filter(e => 
        Math.abs(e.bounds.y - bounds.y) < 20
      ).length;
      
      return {
        type: 'row',
        rowIndex,
        elementsInRow: sameRowElements.length,
        reason: 'horizontal-alignment'
      };
    }
    
    // 4. 深度分区
    const depthRegion = Math.floor(element.depth / 3);
    return {
      type: 'depth-zone',
      depthZone: depthRegion,
      reason: 'dom-depth'
    };
  }

  /**
   * 构建语义树
   */
  function buildSemanticTree(elements, viewportHeight) {
    const startTime = performance.now();
    
    // 为每个元素补充语义信息
    const enrichedElements = elements.map(element => {
      const semantic = inferSemanticRole(element);
      const attentionWeight = calculateAttentionWeight(element, viewportHeight);
      const region = detectRegion(element, elements, null);
      
      return {
        ...element,
        semantic,
        attentionWeight,
        region
      };
    });
    
    // 按注意力权重排序
    const sortedByAttention = [...enrichedElements].sort(
      (a, b) => b.attentionWeight - a.attentionWeight
    );
    
    // 按语义角色分组
    const bySemantic = {};
    for (const el of enrichedElements) {
      const semantic = el.semantic;
      if (!bySemantic[semantic]) {
        bySemantic[semantic] = [];
      }
      bySemantic[semantic].push(el.id);
    }
    
    // 识别主要区域
    const mainRegions = identifyMainRegions(enrichedElements);
    
    const endTime = performance.now();
    
    return {
      elements: enrichedElements,
      sortedByAttention,
      bySemantic,
      mainRegions,
      buildTime: endTime - startTime
    };
  }

  /**
   * 识别页面主要区域
   */
  function identifyMainRegions(elements) {
    const regions = [];
    
    // 查找语义化区域容器
    const semanticContainers = ['navigation', 'header', 'footer', 'sidebar', 'main', 'article', 'section'];
    
    for (const container of semanticContainers) {
      const containerElements = elements.filter(e => e.semantic === container);
      if (containerElements.length > 0) {
        regions.push({
          type: container,
          elements: containerElements.map(e => e.id),
          count: containerElements.length
        });
      }
    }
    
    // 如果没有语义区域，基于位置推断
    if (regions.length === 0) {
      const topElements = elements.filter(e => e.bounds.y < 100);
      const bottomElements = elements.filter(e => e.bounds.y > window.innerHeight - 100);
      
      if (topElements.length > 0) {
        regions.push({
          type: 'header',
          elements: topElements.map(e => e.id),
          count: topElements.length,
          inferred: true
        });
      }
      
      if (bottomElements.length > 0) {
        regions.push({
          type: 'footer',
          elements: bottomElements.map(e => e.id),
          count: bottomElements.length,
          inferred: true
        });
      }
    }
    
    return regions;
  }

  /**
   * 生成布局描述
   */
  function generateLayoutDescription(semanticTree) {
    const { bySemantic, mainRegions, elements } = semanticTree;
    
    const parts = [];
    
    // 整体统计
    const totalElements = elements.length;
    const interactiveCount = elements.filter(e => e.interactive).length;
    const semanticCount = Object.keys(bySemantic).length;
    
    parts.push(`页面包含 ${totalElements} 个元素，其中 ${interactiveCount} 个可交互。`);
    
    // 主要区域
    if (mainRegions.length > 0) {
      const regionNames = mainRegions.map(r => r.type).join('、');
      parts.push(`检测到 ${mainRegions.length} 个主要区域：${regionNames}。`);
    }
    
    // 语义分布
    const topSemantics = Object.entries(bySemantic)
      .sort((a, b) => b[1].length - a[1].length)
      .slice(0, 5);
    
    if (topSemantics.length > 0) {
      const distribution = topSemantics
        .map(([semantic, ids]) => `${semantic}(${ids.length})`)
        .join('、');
      parts.push(`语义分布：${distribution}。`);
    }
    
    // 高注意力元素
    const topAttention = semanticTree.sortedByAttention.slice(0, 3);
    if (topAttention.length > 0) {
      const topTexts = topAttention
        .filter(e => e.text)
        .map(e => e.text.slice(0, 20))
        .join('、');
      if (topTexts) {
        parts.push(`视觉焦点：${topTexts}。`);
      }
    }
    
    return parts.join(' ');
  }

  // 导出到全局
  if (!global.AgentRuntime) {
    global.AgentRuntime = {};
  }
  global.AgentRuntime.semantic = {
    inferSemanticRole,
    calculateAttentionWeight,
    detectRegion,
    buildSemanticTree,
    identifyMainRegions,
    generateLayoutDescription,
    CONFIG
  };

})(window);
