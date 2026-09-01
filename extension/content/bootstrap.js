(function() {
  'use strict';
  
  // 等待 DOM 就绪
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
  
  function bootstrap() {
    try {
      const runtime = new window.AgentRuntime.AgentRuntime();
      runtime.init();
      window.AgentRuntime.mountAgentWorld(runtime);
      
      console.log('[Agent Runtime] ✅ World ready — try window.agentWorld.query.describe()');
    } catch(e) {
      console.error('[Agent Runtime] ❌ Bootstrap failed:', e);
    }
  }
})();
