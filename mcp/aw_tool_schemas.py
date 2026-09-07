# -*- coding: utf-8 -*-
"""MCP 工具 schema 定义(纯数据)——自 mcp/server.py 拆出,行为不变。

修改工具清单只改本文件;server.list_tools 负责 LITE 过滤、规范词置前与 [内部/调试] 标记。
"""
import mcp.types as types


def build_tool_definitions():
    tools = [
        types.Tool(
            name="world_open",
            description="打开一个网页并建立原生网页世界(注入 agent-runtime)。返回世界 ID 和页面摘要。可并行打开多个世界互不干扰。headful=true 时弹出可见窗口(人工介入点:登录/验证码/真人确认);profile=名称 时使用持久化登录态(同一名称复用);cdp_url 可连接已有 Chrome 调试端口(如 http://localhost:9222),复用日常已登录浏览器(注意:不会关闭用户浏览器)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要打开的网址(使用 cdp_url 时可填当前页地址,与当前页相同则跳过导航;填空则直接注入当前页)"},
                    "ready_policy": {"type": "string", "enum": ["action", "terrain", "stable"], "description": "返回时机:action=脚本注入且当前视口可识别即返回;terrain=等待任务相关区域;stable=等待完整稳定(兼容旧行为)", "default": "action"},
                    "wait_ms": {"type": "number", "description": "导航后的额外等待毫秒;action 默认不额外等待,动态页可显式填写", "default": 0},
                    "stabilize_ms": {"type": "number", "description": "terrain/stable 策略等待渐进扫描的最大毫秒", "default": 10000},
                    "headful": {"type": "boolean", "description": "是否弹出可见窗口(登录/验证码/人工确认场景用)", "default": False},
                    "profile": {"type": "string", "description": "持久化登录态名称(如 login-taobao),同一名称复用 cookie/会话;留空则不持久化"},
                    "cdp_url": {"type": "string", "description": "连接已有 Chrome 的 CDP 调试地址(如 http://localhost:9222),复用日常已登录浏览器;与 profile/headless 互斥"},
                    "task_id": {"type": "string", "description": "可选任务编号;同一任务的会话、动作和结果使用同一编号,同一网页世界内的动作会继承它"},
                    "reuse_policy": {"type": "string", "enum": ["auto", "never"], "description": "会话复用策略:auto=同一任务优先复用,never=强制新建", "default": "auto"},
                    "idle_ttl_ms": {"type": "integer", "description": "空闲会话保留毫秒数,默认600000", "default": 600000},
                    "task_goal": {"type": "string", "description": "可选任务目标,用于轨迹和候选图说明,不作为网页指令执行"},
                    "workflow_id": {"type": "string", "description": "可选任务类型编号,用于把多次独立运行归入同一任务族"},
                    "site_version": {"type": "string", "description": "可选网站版本或发布标记,未知时不要猜测"},
                    "role": {"type": "string", "description": "可选账号角色,例如管理员或普通用户"},
                    "permission_scope": {"type": "string", "description": "可选用户授权范围,只保存范围名称不保存凭据"},
                    "graph_valid_until": {"type": "integer", "description": "可选候选图有效截止时间,Unix 时间戳;到期后图标记为 expired"},
                    "business_state_rules": {"type": "array", "description": "可选显式业务状态规则;未知或多规则命中时不会猜测", "items": {"type": "object"}},
                    "operation_contracts": {"type": "array", "description": "可选业务操作契约,包含前置状态、逻辑输入输出、所需角色、授权范围和适用网站版本", "items": {"type": "object"}},
                    "site_adapter": {"type": "object", "description": "可选站点业务适配器;集中声明状态规则、操作契约、任务类型和网站版本", "additionalProperties": True},
                    "site_adapter_file": {"type": "string", "description": "可选站点适配器 JSON 文件名;只能读取 mcp/site_adapters 受控目录,不能与 site_adapter 同时使用"},
                    "enforce_contracts": {"type": "boolean", "description": "是否在 world_act 执行前强制检查业务操作契约;开启后,带 operation 的动作不满足前置条件时会被拦截", "default": False},
                },
                "required": ["url"],
            },
        ),
        types.Tool(
            name="world_entities",
            description="构件清单(图纸构件表):按角色/标签/文本/名字/稳定指纹/空间范围/可交互/视口过滤查询元素,返回编号、名字、摘要文本、实际 href(没有则为空)、坐标、指纹。指纹=跨会话稳定的第二 ID(同站多次进出可快速认路)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer", "description": "世界 ID(world_open 返回)"},
                    "role": {"type": "string", "description": "语义角色,如 button/link/input/combobox/heading/navigation"},
                    "tag": {"type": "string", "description": "HTML 标签,如 a/button/input/div"},
                    "text": {"type": "string", "description": "文本包含(子串匹配)"},
                    "name": {"type": "string", "description": "名字包含(如 round-trip 匹配 combobox.round-trip)"},
                    "fingerprint": {"type": "string", "description": "稳定指纹精确匹配(同站多次进出的认路记忆,从上次 world_entity 详图里取)"},
                    "bounds": {
                        "type": "object",
                        "description": "空间矩形过滤 {x,y,w,h}——与 world_map 返回的 region.bounds 一致。区域钻取:地图拿到某区 bounds 后,只查该区内的构件(中心点落在矩形内)",
                        "properties": {
                            "x": {"type": "number"}, "y": {"type": "number"},
                            "w": {"type": "number"}, "h": {"type": "number"},
                        },
                    },
                    "interactive": {"type": "boolean", "description": "是否可交互"},
                    "in_viewport": {"type": "boolean", "description": "是否在当前视口内"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 100},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_entity",
            description="单个构件详情:编号、名字、稳定指纹、坐标、语义、文本、可交互、邻居(上下左右)、所在区域。指纹是跨会话稳定的第二 ID,用于同站多次进出时快速认路。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号(如 el_89)或可解析的名字"},
                },
                "required": ["world_id", "id"],
            },
        ),
        types.Tool(
            name="world_layers",
            description="图层视图:结构(标签分布)/语义(角色分布)/空间(网格、视口)/交互/名字统计。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_map",
            description="页面结构导览(地图):按语义地标容器(导航/侧栏/主体/页脚/表单/弹窗/标签栏等)分区,每区给出范围、构件数、可交互入口(带强 ID 和指纹)。适合控制台类复杂页面——agent 看一次地图就知道'哪里有什么、点哪个编号过去',不必翻全部清单。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "max_entries": {"type": "integer", "description": "每区最多列出几个可交互入口", "default": 6},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_resolve",
            description="弱 ID 解析:把名字(如 combobox.round-trip)/强 ID/页面原生 id 解析为稳定编号。页面变化后名字失效时重新解析即可。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "query": {"type": "string", "description": "名字或编号"},
                },
                "required": ["world_id", "query"],
            },
        ),
        types.Tool(
            name="world_changes",
            description="[何时用]调试用——需要逐条核对原始事件序列时用;常规操作请用 world_change_digest(更省)。[何时不用]只想知道有没有变化时不要用(噪声大)。变更流:读取自 since 序号以来的页面变化事件(add/remove/update/visibility),增量续读不重不漏。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的 to 值(游标)", "default": 0},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_state",
            description="[何时用]操作后结果存疑时——看弹窗/表单/登录态/URL 在哪;或新开页面后做一次全局确认。[何时不用]不需要全局状态、只在查单个元素时不要用(用 world_entity)。页面状态信道:只读取当前最新的整体页面状态,包括网址、标题、稳定状态、弹窗/菜单、当前网页世界的页签摘要和变化序号;不返回完整页面结构。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_change_digest",
            description="[何时用]操作后想了解'页面变了什么'的摘要(推荐日常用)——返回数量/重要构件/游标,不返回原始事件。[何时不用]需要完整事件序列时用 world_changes。变化摘要信道:读取自 since 序号以来的压缩变化摘要,只返回数量、重要构件和变化游标,不返回原始事件列表。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的变化序号", "default": 0},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_evidence",
            description="[何时用]调试/验证场景——追溯某次操作的证据链时用;[何时不用]常规流程不要主动调,操作返回值自带 verdict 证据即可。操作证据信道:读取动作前后页面状态、网址、弹窗/菜单变化和结果判断;不保存填入的具体文本内容。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的证据序号", "default": 0},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 20},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_business_state",
            description="业务状态信道:用调用方声明的显式规则,把当前页面运行时状态投影为业务状态;没有规则或规则冲突时返回 unknown/ambiguous,不会猜测。",
            inputSchema={
                "type": "object",
                "properties": {"world_id": {"type": "integer"}},
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_operation_check",
            description="业务操作前置检查:判断当前业务状态是否满足某个操作契约;检查失败只返回原因,不执行动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "operation": {"type": "string", "description": "业务操作名,例如提交资料"},
                },
                "required": ["world_id", "operation"],
            },
        ),
        types.Tool(
            name="world_task_plan",
            description="任务路径规划:从当前运行时状态出发,沿已观测的任务图寻找目标业务状态;默认只采用 verified(已通过回放与分支检查)边,allow_candidate=true 仅用于探索,不会执行动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "goal_state": {"type": "string", "description": "目标业务状态编号,例如 profile.complete"},
                    "task_ids": {"type": "array", "description": "可选历史任务实例编号;明确提供后会把已归档轨迹加入规划来源", "items": {"type": "string"}},
                    "max_steps": {"type": "integer", "description": "最多规划多少步,默认 8", "default": 8},
                    "min_replays": {"type": "integer", "description": "规划时采用的独立回放次数阈值,默认 2"},
                    "allow_candidate": {"type": "boolean", "description": "是否允许把 candidate/replayed(未完全验证)边用于探索性规划,默认 false", "default": False},
                },
                "required": ["world_id", "goal_state"],
            },
        ),
        types.Tool(
            name="world_graph_replay_check",
            description="回放核对:把当前世界中的实际轨迹与指定任务图迁移边逐项比较;只返回通过或失败,不执行页面动作,不比较输入原文。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "edge_id": {"type": "string", "description": "world_graph 或 world_task_plan 返回的迁移边编号"},
                    "trace_step": {"type": "integer", "description": "可选当前世界轨迹步骤编号;不填则核对最新轨迹"},
                    "task_ids": {"type": "array", "description": "可选历史任务实例编号;用于找到指定迁移边", "items": {"type": "string"}},
                    "min_replays": {"type": "integer", "description": "构图时使用的独立回放次数阈值,默认 2"},
                },
                "required": ["world_id", "edge_id"],
            },
        ),
        types.Tool(
            name="world_adapter_compare",
            description="站点适配器兼容性检查:比较两个受控目录中的适配器版本,识别状态规则、操作契约、流程编号和网站版本变化;只读取文件,不执行网页动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "base_file": {"type": "string", "description": "基准适配器 JSON 文件名"},
                    "candidate_file": {"type": "string", "description": "待检查适配器 JSON 文件名"},
                },
                "required": ["base_file", "candidate_file"],
            },
        ),
        types.Tool(
            name="world_trace",
            description="任务轨迹信道:读取当前网页世界中已脱敏的动作前后状态、操作结果和证据引用;不保存填写文本原文。用于构建和审计任务运行时图。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "上次读到的轨迹步骤序号", "default": 0},
                    "limit": {"type": "integer", "description": "最多返回轨迹条数", "default": 50},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_graph",
            description="候选任务运行时图:从已记录轨迹生成状态节点和迁移边。候选图只表示观测到的行为,不代表完整业务规则,也不把出现次数解释成概率。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "expected_outcomes": {"type": "array", "description": "可选预期结果分支,用于生命周期评估", "items": {"type": "string"}},
                    "min_replays": {"type": "integer", "description": "可选独立回放次数阈值,默认 2", "default": 2},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_trace_archive",
            description="读取已归档任务轨迹:仅在明确开启本地轨迹归档后可用;按任务编号读取脱敏 JSONL 记录,可在网页世界关闭后审计。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "world_open 返回的任务实例编号"},
                    "since": {"type": "integer", "description": "上次读到的轨迹步骤序号", "default": 0},
                    "limit": {"type": "integer", "description": "最多返回轨迹条数", "default": 200},
                },
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="world_graph_archive",
            description="从已归档任务轨迹生成候选图:用于网页世界关闭后的审计;不会把候选图自动升级为已验证图。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "world_open 返回的任务实例编号"},
                    "goal": {"type": "string", "description": "可选任务目标说明"},
                    "expected_outcomes": {"type": "array", "description": "可选预期结果分支", "items": {"type": "string"}},
                    "min_replays": {"type": "integer", "description": "可选独立回放次数阈值,默认 2", "default": 2},
                    "valid_until": {"type": "integer", "description": "可选有效截止时间,Unix 时间戳"},
                },
                "required": ["task_id"],
            },
        ),
        types.Tool(
            name="world_graph_assess",
            description="评估候选图生命周期:检查独立回放次数、预期结果分支和有效期,只返回评估结果,不会自动发布图。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "expected_outcomes": {"type": "array", "description": "预期必须覆盖的页面结果,例如 progressed、errored、challenged", "items": {"type": "string"}},
                    "min_replays": {"type": "integer", "description": "每条边至少需要多少条独立轨迹,默认 2", "default": 2},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_graph_bundle",
            description="合并多个已归档任务实例生成候选图并评估,用于跨会话回放;任务编号必须由调用方明确提供。",
            inputSchema={
                "type": "object",
                "properties": {
                    "task_ids": {"type": "array", "description": "要合并的任务实例编号列表", "items": {"type": "string"}},
                    "goal": {"type": "string", "description": "可选任务目标说明"},
                    "expected_outcomes": {"type": "array", "description": "预期必须覆盖的页面结果", "items": {"type": "string"}},
                    "min_replays": {"type": "integer", "description": "每条边至少需要多少条独立轨迹,默认 2", "default": 2},
                    "valid_until": {"type": "integer", "description": "可选有效截止时间,Unix 时间戳"},
                },
                "required": ["task_ids"],
            },
        ),
        types.Tool(
            name="world_guide",
            description="实时任务导览:把页面状态、变化摘要和最近操作证据组合成一份面向当前任务的短导览;默认返回兼容的完整候选,可用 view_mode=focused 获取任务条件化战争迷雾视野(焦点/候选边界/阻断/折叠噪声),不执行任何动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "task": {"type": "string", "description": "当前要完成的任务,尽量用一句话描述"},
                    "change_since": {"type": "integer", "description": "变化摘要上次读取到的序号", "default": 0},
                    "evidence_since": {"type": "integer", "description": "操作证据上次读取到的序号", "default": 0},
                    "max_candidates": {"type": "integer", "description": "最多返回候选入口数", "default": 6},
                    "view_mode": {"type": "string", "enum": ["full", "focused"], "description": "full=保持现有导览; focused=任务条件化视野,只返回焦点/候选边界/阻断/折叠噪声", "default": "full"},
                    "frontier_depth": {"type": "integer", "enum": [1], "description": "可达候选透视深度; MVP 只执行无副作用的一跳结构分析", "default": 1},
                    "expand": {"type": "string", "enum": ["none", "nearby", "region", "all"], "description": "focused 视野扩展范围:none=窄视野, nearby=附近候选, region=相关区域, all=当前页面可交互元素", "default": "none"},
                    "view_detail": {"type": "string", "enum": ["full", "compact"], "description": "focused 返回格式:full=保留空间与结构元数据; compact=去重元数据,保留行动角色、路径关系和阻断", "default": "full"},
                },
                "required": ["world_id", "task"],
            },
        ),
        types.Tool(
            name="world_click",
            description="按编号点击元素(原生 click 事件)。带遮挡检测与自动等待，并返回页面整体反馈(URL、页面状态、弹窗/菜单和变化序号);如果页面已跳转或出现覆盖层,优先按整体事实修正局部效果判断。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(截取目标区域前后帧做像素比对,捕获纯 CSS 动效/浮层变化)。默认 False 以保持快速;需要视觉证据时开启", "default": False},
                },
                "required": ["world_id", "id"],
            },
        ),
        types.Tool(
            name="world_fill",
            description="按编号填入文本(优先 Playwright 原生 fill/press_sequentially;支持打字间隔模拟触发联想;失败自动降级 JS setter+覆盖层切换)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "text": {"type": "string", "description": "要填入的文本"},
                    "type_delay_ms": {"type": "integer", "description": "逐字打字延迟毫秒(>0 时模拟真实键盘输入,触发自动联想下拉)", "default": 0},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(默认 False 保持快速)", "default": False},
                },
                "required": ["world_id", "id", "text"],
            },
        ),
        types.Tool(
            name="world_batch_fill",
            description="批量填入多个表单字段(单次 MCP 往返完成多个输入框填写,减少交互延迟;逐字段容错,失败不影响后续)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "fields": {
                        "type": "array",
                        "description": "表单字段列表: [{ id, text, type_delay_ms? }]",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "构件编号或名字"},
                                "text": {"type": "string", "description": "填入内容"},
                                "type_delay_ms": {"type": "integer", "description": "逐字打字延迟毫秒", "default": 0},
                            },
                            "required": ["id", "text"],
                        },
                    },
                },
                "required": ["world_id", "fields"],
            },
        ),
        types.Tool(
            name="world_press",
            description="按编号聚焦并按按键(如 Enter/Escape/Tab/ArrowDown),用于提交表单或操作下拉建议。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号或名字"},
                    "key": {"type": "string", "description": "按键名,如 Enter、Escape、Tab、ArrowDown"},
                    "visual_evidence": {"type": "boolean", "description": "是否启用视觉 diff 兜底(默认 False 保持快速)", "default": False},
                },
                "required": ["world_id", "id", "key"],
            },
        ),
        types.Tool(
            name="world_wait",
            description="等待条件满足:构件出现/消失/文本变化。轮询内部原生网页世界,操作后验证结果的利器。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "mode": {"type": "string", "enum": ["appear", "disappear"], "description": "appear=等待出现,disappear=等待消失"},
                    "role": {"type": "string", "description": "角色过滤(可选)"},
                    "text": {"type": "string", "description": "文本过滤(可选)"},
                    "name": {"type": "string", "description": "名字过滤(可选)"},
                    "timeout_ms": {"type": "integer", "description": "超时毫秒", "default": 30000},
                },
                "required": ["world_id", "mode"],
            },
        ),
        types.Tool(
            name="world_click_at",
            description="按视口坐标点击(原生网页世界外的元素/iframe 区域兜底,坐标来自截图或视觉)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "x": {"type": "integer", "description": "视口横坐标"},
                    "y": {"type": "integer", "description": "视口纵坐标"},
                },
                "required": ["world_id", "x", "y"],
            },
        ),
        types.Tool(
            name="world_navigate",
            description="在当前世界内导航到新 URL(SPA 跳转/换页,无需关闭重开)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "url": {"type": "string", "description": "要导航的网址"},
                    "wait_ms": {"type": "number", "description": "导航后额外等待毫秒", "default": 2000},
                },
                "required": ["world_id", "url"],
            },
        ),
        types.Tool(
            name="world_eval",
            description="在世界内执行 JS 表达式(调试/特殊查询用,建议只读;返回结果截断保护)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "expression": {"type": "string", "description": "JS 表达式,如 document.title 或 (() => {...})()"},
                },
                "required": ["world_id", "expression"],
            },
        ),
        types.Tool(
            name="world_assume",
            description=("提交决策前提:我假设某元素的状态是某个值,请持续监视,失效时在后续工具返回中自动通知。"
                         "doc_id 支持 DOM id(如 field-origin)或内核构件 id(如 el_6);"
                         "attr 支持 value(输入框)/checked(复选框)/textContent(文本元素)。"),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "name": {"type": "string", "description": "前提名,如 origin"},
                    "doc_id": {"type": "string", "description": "元素 DOM id 或内核 id"},
                    "attr": {"type": "string", "description": "监视属性", "enum": ["value", "checked", "textContent"]},
                    "expect": {"type": "string", "description": "期望值"},
                },
                "required": ["world_id", "name", "doc_id", "attr", "expect"],
            },
        ),
        types.Tool(
            name="world_ack",
            description="修正完成后恢复对该前提的监视(收到前提失效通知后,重做该步并 ack)。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "expect": {"type": "string", "description": "可选:修正后的新期望值"},
                },
                "required": ["world_id", "name"],
            },
        ),
        types.Tool(
            name="world_status",
            description="查看当前所有前提及其监视状态。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_timeline",
            description=("统一时间线读取(游标增量):动作/网络请求/响应/DOM 变更/前提失效合并为一条因果时间轴。"
                         "digest 模式返回统计 + 因果窗口(每个动作引发了什么)+ 静默失败标注;"
                         "raw 模式返回原始事件。since 传上次的 cursor 做增量续读。"),
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "游标,只返回 seq 更大的事件", "default": 0},
                    "mode": {"type": "string", "description": "digest(摘要,默认) 或 raw(原始事件)",
                             "enum": ["digest", "raw"], "default": "digest"},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_screenshot",
            description="截图:整页、指定构件区域或带编号标注图(Set-of-Mark)。支持直接返回图片数据(ImageContent)或文件路径,原生多模态模型友好。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "id": {"type": "string", "description": "构件编号(可选,不填截整页或视口)"},
                    "annotated": {"type": "boolean", "description": "是否绘制带 [el_X] 编号与名称的半透明标注框(Set-of-Mark 模式)", "default": False},
                    "return_base64": {"type": "boolean", "description": "是否直接返回 MCP ImageContent 原生图片数据", "default": True},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_close",
            description="关闭世界,释放浏览器资源。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "keep_session": {"type": "boolean", "description": "是否只标记空闲并保留网页世界,供同一 task_id 后续复用", "default": False},
                    "idle_ttl_ms": {"type": "integer", "description": "保留会话的空闲期限,默认600000", "default": 600000},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_list",
            description="列出所有已打开的世界。",
            inputSchema={"type": "object", "properties": {}},
        ),
        # ── 阶段 B 收口:3 个新工具(默认协议) ──
        types.Tool(
            name="world_find",
            description="默认协议:按条件定位构件(替代 world_entities/world_resolve 的日常用法)。返回 matches[] 与 ambiguous 标记，匹配项带轻量摘要文本、实际 href 和稳定指纹;禁止在本工具内执行任何动作。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "q": {"type": "string", "description": "一句话或弱 ID(名字/强 ID/页面原生 id),走 resolve 解析;未命中时按可见文本/名字子串兜底(大小写不敏感)"},
                    "role": {"type": "string", "description": "语义角色,如 button/link/input/combobox/heading"},
                    "text": {"type": "string", "description": "文本包含(子串匹配)"},
                    "name": {"type": "string", "description": "名字包含(如 round-trip 匹配 combobox.round-trip)"},
                    "fingerprint": {"type": "string", "description": "稳定指纹精确匹配(用于重复进入同一站点时快速找回目标)"},
                    "tag": {"type": "string", "description": "HTML 标签过滤,如 div/a/button"},
                    "interactive": {"type": "boolean", "description": "仅返回可交互构件"},
                    "in_viewport": {"type": "boolean", "description": "仅返回视口内构件"},
                    "max_results": {"type": "integer", "description": "最多返回条数", "default": 20},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡;默认轻量", "default": False},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_act",
            description="默认协议:唯一行动入口。kind=click|fill|press|batch_fill,返回统一后果卡(page_outcome 五态)。steps 数组可在一个往返内执行多个动作(聚合执行,等价 RFC 的 world_run);任一步 errored 即停止。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "kind": {"type": "string", "description": "动作类型: click|fill|press|batch_fill", "enum": ["click", "fill", "press", "batch_fill"]},
                    "id": {"type": "string", "description": "构件编号(如 el_89)或可解析的名字(world_find 返回的 id)"},
                    "text": {"type": "string", "description": "fill 时填写的文本"},
                    "key": {"type": "string", "description": "press 时的按键(Enter/Escape/Tab...)"},
                    "fields": {"type": "array", "description": "batch_fill 的字段列表 [{\"id\":\"el_6\",\"text\":\"...\"}]", "items": {"type": "object"}},
                    "type_delay_ms": {"type": "number", "description": "逐字打字延迟(触发联想下拉用)", "default": 0},
                    "visual_evidence": {"type": "boolean", "description": "是否截前后帧做视觉 diff 兜底", "default": False},
                    "wait_policy": {"type": "string", "enum": ["confirmed", "receipt"], "description": "confirmed=等待最终后果卡(默认),receipt=动作发出后立即返回编号"},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡(frames/forms/world 明细);默认轻量(URL/稳定态/登录态/弹窗)", "default": False},
                    "steps": {"type": "array", "description": "聚合执行:多步动作序列 [{kind,id,text|key|fields,...}, ...],任一步 errored 即停", "items": {"type": "object"}},
                    "operation": {"type": "string", "description": "可选业务操作名,例如填写资料或提交;不填写时使用低层动作名"},
                    "operation_id": {"type": "string", "description": "可选操作编号,用于把相邻步骤绑定到同一业务操作"},
                    "executor": {"type": "string", "description": "可选执行器标记,例如 world_act、webmcp 或授权接口"},
                    "task_id": {"type": "string", "description": "可选任务实例编号,不填写时继承 world_open 的任务编号"},
                    "enforce_contracts": {"type": "boolean", "description": "可选开启本次动作的契约强制检查;不会关闭 world_open 已开启的严格模式;带 operation 时失败则不点击页面", "default": False},
                    "input_bindings": {"type": "array", "description": "可选输入数据绑定,只填写逻辑引用,例如 [{\"from\":\"填写资料.资料\",\"to\":\"提交资料.资料\"}]", "items": {"type": "object"}},
                    "output_bindings": {"type": "array", "description": "可选输出数据绑定,只填写逻辑引用,不填写真实值,例如 [{\"name\":\"资料\",\"ref\":\"profile\"}]", "items": {"type": "object"}},
                },
                "required": ["world_id"],
            },
        ),
        types.Tool(
            name="world_outcome",
            description="默认协议:读最近一张统一后果卡(幂等,弱模型'我刚才到底怎样了'的唯一查询)。since 传入 evidence_seq 时,仅当有新动作才返回新卡,否则返回 none 卡。watch_id 为阶段 C(验尸官)预留。",
            inputSchema={
                "type": "object",
                "properties": {
                    "world_id": {"type": "integer"},
                    "since": {"type": "integer", "description": "仅当存在 evidence_seq 大于 since 的新卡时返回它", "default": 0},
                    "verbose": {"type": "boolean", "description": "true 时返回全量深诊断状态卡;默认轻量", "default": False},
                    "action_id": {"type": "string", "description": "receipt 模式返回的动作编号"},
                    "wait_ms": {"type": "integer", "description": "查询 receipt 时最多等待结果变化的毫秒数,默认0"},
                },
                "required": ["world_id"],
            },
        ),
    ]


    return tools
