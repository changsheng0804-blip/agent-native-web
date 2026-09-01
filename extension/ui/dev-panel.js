// Dev Panel - 开发面板（可选拓展）
// 提供可视化调试界面

window.AgentRuntime = window.AgentRuntime || {};

(function(global) {
  'use strict';

  let panelWindow = null;

  function openDevPanel() {
    if (panelWindow && !panelWindow.closed) {
      panelWindow.focus();
      return panelWindow;
    }

    panelWindow = window.open('', '_blank', 'width=400,height=600,left=0,top=0');
    
    const doc = panelWindow.document;
    doc.title = 'Agent Runtime Dev Panel';
    
    const styles = `
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'SF Mono', Monaco, monospace; font-size: 12px; background: #1a1a2e; color: #e0e0e0; padding: 12px; }
        h1 { font-size: 14px; color: #7dd3fc; margin-bottom: 12px; border-bottom: 1px solid #333; padding-bottom: 8px; }
        .section { margin-bottom: 16px; }
        .section-title { color: #86efac; font-size: 11px; text-transform: uppercase; margin-bottom: 6px; }
        .stat { display: flex; justify-content: space-between; padding: 4px 0; border-bottom: 1px solid #2a2a3e; }
        .stat-label { color: #a0a0a0; }
        .stat-value { color: #fbbf24; }
        button { background: #3b82f6; color: white; border: none; padding: 8px 16px; border-radius: 4px; cursor: pointer; margin: 4px 2px; font-size: 11px; }
        button:hover { background: #2563eb; }
        button.active { background: #10b981; }
        pre { background: #0f0f1a; padding: 8px; border-radius: 4px; overflow-x: auto; max-height: 200px; font-size: 10px; }
        input { background: #2a2a3e; border: 1px solid #3b82f6; color: #e0e0e0; padding: 6px 10px; border-radius: 4px; width: 100%; margin: 4px 0; }
        .tag { display: inline-block; background: #3b82f6; padding: 2px 6px; border-radius: 3px; margin: 2px; font-size: 10px; }
      </style>
    `;

    doc.write(`
      <!DOCTYPE html>
      <html>
      <head>${styles}</head>
      <body>
        <h1>🕷️ Agent Runtime</h1>
        
        <div class="section">
          <div class="section-title">Page Meta</div>
          <div id="meta"></div>
        </div>
        
        <div class="section">
          <div class="section-title">Stats</div>
          <div id="stats"></div>
        </div>
        
        <div class="section">
          <div class="section-title">Overlay Modes</div>
          <button onclick="toggleMode('grid')">Grid</button>
          <button onclick="toggleMode('elements')">Elements</button>
          <button onclick="toggleMode('regions')">Regions</button>
          <button onclick="toggleMode('all')">All</button>
          <button onclick="toggleMode('off')">Off</button>
        </div>
        
        <div class="section">
          <div class="section-title">Find By Role</div>
          <input type="text" id="roleInput" placeholder="button, link, input..." onkeypress="if(event.key==='Enter')findByRole()">
          <button onclick="findByRole()">Find</button>
          <div id="roleResults"></div>
        </div>
        
        <div class="section">
          <div class="section-title">Describe</div>
          <button onclick="describe()">Generate</button>
          <pre id="describeResult"></pre>
        </div>
        
        <script>
          function update() {
            if (!window.opener?.agentWorld) return;
            const world = window.opener.agentWorld;
            
            // Meta
            document.getElementById('meta').innerHTML = 
              '<div class="stat"><span class="stat-label">URL</span><span class="stat-value">' + 
              (world.meta?.url || '').substring(0, 30) + '...</span></div>' +
              '<div class="stat"><span class="stat-label">Title</span><span class="stat-value">' + 
              (world.meta?.title || '') + '</span></div>';
            
            // Stats
            const summary = world.query.getPageSummary();
            document.getElementById('stats').innerHTML = 
              '<div class="stat"><span class="stat-label">Total</span><span class="stat-value">' + summary.total + '</span></div>' +
              '<div class="stat"><span class="stat-label">Interactive</span><span class="stat-value">' + summary.interactive + '</span></div>' +
              '<div class="stat"><span class="stat-label">In Viewport</span><span class="stat-value">' + summary.inViewport + '</span></div>' +
              '<div class="stat"><span class="stat-label">Empty Regions</span><span class="stat-value">' + summary.emptyRegions + '</span></div>';
          }
          
          function toggleMode(mode) {
            window.opener?.agentWorld?.toggleOverlay(mode);
            document.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
          }
          
          function findByRole() {
            const role = document.getElementById('roleInput').value;
            const ids = window.opener?.agentWorld?.query.findByRole(role) || [];
            document.getElementById('roleResults').innerHTML = 
              ids.length + ' found: ' + 
              ids.slice(0, 10).map(id => '<span class="tag">' + id + '</span>').join('') +
              (ids.length > 10 ? '...' : '');
          }
          
          function describe() {
            const desc = window.opener?.agentWorld?.query.describe() || '';
            document.getElementById('describeResult').textContent = desc;
          }
          
          setInterval(update, 1000);
          update();
        </script>
      </body>
      </html>
    `);
    
    doc.close();
    return panelWindow;
  }

  global.AgentRuntime.devPanel = { openDevPanel };
})(window);
