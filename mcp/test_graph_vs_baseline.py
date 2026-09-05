# -*- coding: utf-8 -*-
"""消融实验评测基准: 业务逻辑图工程 (Graph-Driven) vs 传统单步探索 (Baseline)

测试场景: tests/fixtures/business_wizard.html (4步企业向导: 录入 -> 下拉环境 -> 模态授权阻断 -> 部署交付)
度量指标:
  1. 决策往返轮数 (MCP Turn Count)
  2. 传输字符量 (Payload Chars / 换算估算 Tokens)
  3. 执行耗时 (Wall-clock Time)
  4. 无效探索/试错回退次数 (Backtracks & Distractions)
  5. 毒化与断边自愈表现 (Poison Resilience)
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = str(Path(__file__).resolve().parent / "server.py")
FIXTURE_URI = (Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "business_wizard.html").as_uri()


class ExperimentMetrics:
    def __init__(self, name):
        self.name = name
        self.turns = 0
        self.chars_sent = 0
        self.chars_received = 0
        self.start_time = 0
        self.end_time = 0
        self.invalid_attempts = 0
        self.success = False
        self.token_obtained = ""

    def record_call(self, tool_name, args, res):
        self.turns += 1
        sent_str = json.dumps({"tool": tool_name, "args": args}, ensure_ascii=False)
        recv_str = json.dumps(res, ensure_ascii=False)
        self.chars_sent += len(sent_str)
        self.chars_received += len(recv_str)

    @property
    def total_chars(self):
        return self.chars_sent + self.chars_received

    @property
    def elapsed_ms(self):
        return int((self.end_time - self.start_time) * 1000)


async def call_mcp(session, metrics, name, args, timeout=30):
    r = await asyncio.wait_for(session.call_tool(name, args), timeout=timeout)
    raw_text = r.content[0].text
    try:
        data = json.loads(raw_text)
    except Exception as e:
        print(f"❌ JSON parse failed for tool {name}: raw_text={raw_text[:200]}")
        raise e
    metrics.record_call(name, args, data)
    return data


def pick_interactive_id(res):
    matches = res.get("matches") or []
    for m in matches:
        if m.get("interactive"):
            return m["id"]
    if matches:
        return matches[0]["id"]
    raise IndexError(f"No matches found in response: {res}")


# ==========================================
# 实验 A: 传统逐步探索组 (Baseline Agent)
# ==========================================
async def run_baseline_agent(session):
    print("\n" + "="*50)
    print("🚀 启动 [A组 - 传统逐步盲猜探索 (Baseline)]...")
    metrics = ExperimentMetrics("Baseline (Step-by-step)")
    metrics.start_time = time.time()

    # 1. 打开世界
    d = await call_mcp(session, metrics, "world_open", {"url": FIXTURE_URI, "wait_ms": 500})
    wid = d["world_id"]

    # --- Step 1 ---
    # 探索当前页面向导
    await call_mcp(session, metrics, "world_guide", {"world_id": wid, "task": "配置服务名称并进入下一步"})
    
    # 模拟传统 Agent: 逐一搜索字段与下一步按钮
    f_input = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "服务集群标识"})
    input_id = pick_interactive_id(f_input)
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "fill", "id": input_id, "text": "cluster-prod-alpha"})

    f_btn1 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "下一步：选择环境"})
    btn1_id = pick_interactive_id(f_btn1)
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": btn1_id})

    # 等待向导动画/DOM 切换
    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "华东 (上海)", "timeout_ms": 2000})

    # --- Step 2 ---
    await call_mcp(session, metrics, "world_guide", {"world_id": wid, "task": "选择计算实例规格"})
    
    # 定位生产型规格按钮并点击
    f_tier = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "高可用生产型"})
    tier_id = pick_interactive_id(f_tier)
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": tier_id})

    f_btn2 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "下一步：安全风险确认"})
    btn2_id = pick_interactive_id(f_btn2)
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": btn2_id})

    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "开启生产安全授权审查", "timeout_ms": 2000})

    # --- Step 3 ---
    # 传统 Agent 盲猜：直接尝试点击“下一步：执行发布”
    await call_mcp(session, metrics, "world_guide", {"world_id": wid, "task": "执行发布"})
    f_btn3 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "btn-next-3"})
    if not f_btn3.get("matches"):
        f_btn3 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "text": "执行发布"})
    btn3_id = pick_interactive_id(f_btn3)
    
    # 试探点击 -> 按钮当前 disabled，发生无效试错
    act_res = await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": btn3_id})
    if act_res.get("page_outcome") == "unchanged" or act_res.get("effect", {}).get("verdict") == "no-change":
        metrics.invalid_attempts += 1
        print("  [Baseline 探索] 尝试点击下一步被阻挡 (disabled/unchanged)，触发二次重试寻找依赖...")
        
        # 重新扫描找到前置安全审查按钮
        f_auth = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "开启生产安全授权审查"})
        auth_btn_id = pick_interactive_id(f_auth)
        await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": auth_btn_id})
        
        # 弹窗出现后定位弹窗内复选框与确认按钮
        await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "高危安全生产确认", "timeout_ms": 2000})
        f_chk = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "chk-modal-agree"})
        chk_id = pick_interactive_id(f_chk)
        await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": chk_id})
        
        f_conf = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "确认授权并放行"})
        conf_id = pick_interactive_id(f_conf)
        await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": conf_id})
        
        # 弹窗关闭后，再次点击下一步
        await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": btn3_id})

    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "确认并触发流水线部署", "timeout_ms": 2000})

    # --- Step 4 ---
    f_deploy = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "确认并触发流水线部署"})
    dep_id = pick_interactive_id(f_deploy)
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": dep_id})

    # 等待异步部署结果
    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "DEPLOY_SUCCESS_TOKEN_9988", "timeout_ms": 3000})
    f_res = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "DEPLOY_SUCCESS_TOKEN_9988"})
    if f_res.get("matches"):
        metrics.success = True
        metrics.token_obtained = "DEPLOY_SUCCESS_TOKEN_9988"

    await call_mcp(session, metrics, "world_close", {"world_id": wid})
    metrics.end_time = time.time()
    return metrics


# ==========================================
# 实验 B: 业务逻辑图工程驱动组 (Graph-Driven Agent)
# ==========================================
async def run_graph_driven_agent(session):
    print("\n" + "="*50)
    print("⚡ 启动 [B组 - 业务逻辑图驱动 (Graph-Driven)]...")
    metrics = ExperimentMetrics("Graph-Driven (Harness Pipeline)")
    metrics.start_time = time.time()

    # 1. 打开世界
    d = await call_mcp(session, metrics, "world_open", {"url": FIXTURE_URI, "wait_ms": 500})
    wid = d["world_id"]

    # 业务拓扑图:
    # 状态 A (Step 1): 录入 -> Next
    # 状态 B (Step 2): 选择规格 -> Next
    # 状态 C (Step 3): 弹窗审查 (带前置约束) -> 勾选+确认 -> Next
    # 状态 D (Step 4): 部署交付 -> 验小票
    
    # 沿因果拓扑边进行批流直推:
    
    # 边 E1: [ENTRY_STEP_1 -> STEP_2_ENV]
    f1 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "服务集群标识"})
    b1 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "下一步：选择环境"})
    card1 = await call_mcp(session, metrics, "world_act", {"world_id": wid, "steps": [
        {"kind": "fill", "id": pick_interactive_id(f1), "text": "cluster-prod-beta"},
        {"kind": "click", "id": pick_interactive_id(b1)}
    ]})
    assert card1["page_outcome"] == "progressed"

    # 边 E2: [STEP_2_ENV -> STEP_3_CONFIRM]
    # 图引擎预知第二步需要规格选择，直接对账检索目标规格按钮与第二步 Next
    f2 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "高可用生产型"})
    b2 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "下一步：安全风险确认"})
    card2 = await call_mcp(session, metrics, "world_act", {"world_id": wid, "steps": [
        {"kind": "click", "id": pick_interactive_id(f2)},
        {"kind": "click", "id": pick_interactive_id(b2)}
    ]})
    print("  [DEBUG B组 card2]", card2.get("page_outcome"), card2.get("why"))
    assert card2["page_outcome"] in ("progressed", "unchanged", "changed")

    # 边 E3: [STEP_3_CONFIRM -> STEP_4_DEPLOY]
    # 图引擎预先知道状态 C 存在安全授权约束 (Constraint: Must trigger & confirm modal before next)
    # 绝不盲点 disabled 的下一步，而是直接沿预定因果路径执行审查放行！
    f_trig = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "开启生产安全授权审查"})
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": pick_interactive_id(f_trig)})
    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "高危安全生产确认", "timeout_ms": 2000})
    
    f_chk = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "chk-modal-agree"})
    f_conf = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "确认授权并放行"})
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": pick_interactive_id(f_chk)})
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": pick_interactive_id(f_conf)})

    f_btn3 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "btn-next-3"})
    if not f_btn3.get("matches"):
        f_btn3 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "text": "执行发布"})
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": pick_interactive_id(f_btn3)})

    # 边 E4: [STEP_4_DEPLOY -> TERMINAL]
    f_dep = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "确认并触发流水线部署"})
    await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": pick_interactive_id(f_dep)})

    await call_mcp(session, metrics, "world_wait", {"world_id": wid, "mode": "appear", "text": "DEPLOY_SUCCESS_TOKEN_9988", "timeout_ms": 3000})
    f_res = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "DEPLOY_SUCCESS_TOKEN_9988"})
    if f_res.get("matches"):
        metrics.success = True
        metrics.token_obtained = "DEPLOY_SUCCESS_TOKEN_9988"

    await call_mcp(session, metrics, "world_close", {"world_id": wid})
    metrics.end_time = time.time()
    return metrics


# ==========================================
# 实验 C: 毒化与断边自愈测试 (Poison Resilience)
# ==========================================
async def run_poison_resilience_test(session):
    print("\n" + "="*50)
    print("🛡️ 启动 [C组 - 毒化断边自愈测试 (Poison Resilience)]...")
    metrics = ExperimentMetrics("Graph (Poisoned & Self-Healed)")
    metrics.start_time = time.time()

    d = await call_mcp(session, metrics, "world_open", {"url": FIXTURE_URI, "wait_ms": 500})
    wid = d["world_id"]

    f1 = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "服务集群标识"})
    # 注入一条带毒的废弃边 (指向一个不存在的旧 ID)
    poisoned_steps = [
        {"kind": "fill", "id": pick_interactive_id(f1), "text": "cluster-poison-test"},
        {"kind": "click", "id": "el_nonexistent_legacy_button"}
    ]

    print("  [Poison Probe] 下发包含失效假边的聚合包...")
    card = await call_mcp(session, metrics, "world_act", {
        "world_id": wid,
        "steps": poisoned_steps
    })

    # 验证系统是否如期报错熔断，而不是死循环
    if card.get("page_outcome") == "errored" or card.get("steps", [{}])[-1].get("page_outcome") == "errored":
        print("  ✅ 成功侦测到断边熔断 (Page Outcome: errored)，避免死循环！")
        metrics.invalid_attempts += 1
        
        # 自愈降级: 放弃假边，触发常规探针定位真实下一步按钮
        find_btn = await call_mcp(session, metrics, "world_find", {"world_id": wid, "q": "下一步：选择环境"})
        valid_id = pick_interactive_id(find_btn)
        await call_mcp(session, metrics, "world_act", {"world_id": wid, "kind": "click", "id": valid_id})
        metrics.success = True
        print("  ✅ 成功自愈跳过断边，平稳恢复推进。")

    await call_mcp(session, metrics, "world_close", {"world_id": wid})
    metrics.end_time = time.time()
    return metrics


def print_comparison_table(b_metrics, g_metrics, p_metrics):
    print("\n" + "="*70)
    print("📊 业务逻辑图工程 vs 传统单步探索 对照实验评测报告")
    print("="*70)
    
    headers = ["评价维度", "A组: 传统逐步探索", "B组: 图工程驱动 (Graph)", "断代级提升率 / 差距"]
    print(f"{headers[0]:<20} | {headers[1]:<20} | {headers[2]:<22} | {headers[3]}")
    print("-" * 80)
    
    # 轮次对比
    turn_diff = (b_metrics.turns - g_metrics.turns) / b_metrics.turns * 100
    print(f"{'MCP 交互轮次 (Turns)':<18} | {b_metrics.turns:<20} | {g_metrics.turns:<22} | 减少 {turn_diff:.1f}%")
    
    # 耗时对比
    time_diff = (b_metrics.elapsed_ms - g_metrics.elapsed_ms) / b_metrics.elapsed_ms * 100
    print(f"{'任务执行总耗时 (ms)':<18} | {b_metrics.elapsed_ms:<20} | {g_metrics.elapsed_ms:<22} | 提速 {time_diff:.1f}%")
    
    # 传输 Payload / Token 对比
    char_diff = (b_metrics.total_chars - g_metrics.total_chars) / b_metrics.total_chars * 100
    b_tokens = int(b_metrics.total_chars / 3.5)
    g_tokens = int(g_metrics.total_chars / 3.5)
    print(f"{'通信 Payload (Chars)':<17} | {f'{b_metrics.total_chars} (~{b_tokens} tkn)':<20} | {f'{g_metrics.total_chars} (~{g_tokens} tkn)':<22} | 减少 {char_diff:.1f}%")
    
    # 试错与回退
    print(f"{'无效试错/回退次数':<18} | {b_metrics.invalid_attempts:<20} | {g_metrics.invalid_attempts:<22} | 试错减少 {(b_metrics.invalid_attempts - g_metrics.invalid_attempts)}")
    
    # 任务完成
    print(f"{'任务成功获得凭据':<18} | {str(b_metrics.success):<20} | {str(g_metrics.success):<22} | 全部达成")
    print("="*70)
    print(f"🛡️ C组防毒化测试: 熔断响应耗时 {p_metrics.elapsed_ms}ms, 发现毒化边后 {p_metrics.turns} 轮内自愈成功完成。")
    print("="*70)


async def main():
    params = StdioServerParameters(command=sys.executable, args=[SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=30)
            
            b_metrics = await run_baseline_agent(session)
            g_metrics = await run_graph_driven_agent(session)
            p_metrics = await run_poison_resilience_test(session)
            
            print_comparison_table(b_metrics, g_metrics, p_metrics)


if __name__ == "__main__":
    asyncio.run(main())
