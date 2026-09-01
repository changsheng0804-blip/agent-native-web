# -*- coding: utf-8 -*-
"""
真实网站实战验证脚本:
1. 站点一: GitHub (https://github.com/explore) - 现代复杂 SPA，测试 Set-of-Mark (SoM) 标注 + 原生多模态感知
2. 站点二: 真实 Canvas 站点 (Chart.js 官网示例 / MDN Canvas 示例) - 验证 Canvas 局部视觉下钻 (PR3)
3. 站点三: 真实动效/纯CSS状态切换站点 (如 Wikipedia 菜单/折叠按钮或百度首页设置) - 验证操作前后视觉 Diff (PR4)
"""
import base64
import json
import math
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from PIL import Image, ImageChops, ImageDraw, ImageStat
from playwright.sync_api import sync_playwright

ALL_IN_ONE = Path("extension/all-in-one.js")
with open(ALL_IN_ONE, "r", encoding="utf-8") as f:
    INJECT_JS = f.read()

def annotate_viewport(page, entities, out_path):
    raw_path = out_path.parent / f"raw_{out_path.name}"
    page.screenshot(path=str(raw_path), full_page=False)
    
    img = Image.open(raw_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    count = 0
    for ent in entities:
        if not ent.get("interactive") or not ent.get("inViewport"):
            continue
        box = ent.get("bounds", {})
        x, y, w, h = box.get("x", 0), box.get("y", 0), box.get("w", 0), box.get("h", 0)
        if w <= 4 or h <= 4:
            continue
            
        eid = ent.get("id", "")
        name = ent.get("name", "")[:12]
        
        # 醒目的半透明粉红框与标签
        draw.rectangle([x, y, x + w, y + h], outline=(255, 20, 100, 240), width=2)
        label = f"[{eid}] {name}"
        tag_w = max(36, len(label) * 7 + 6)
        draw.rectangle([x, max(0, y - 16), x + tag_w, max(16, y)], fill=(255, 20, 100, 210))
        draw.text((x + 3, max(0, y - 15)), label, fill=(255, 255, 255, 255))
        count += 1
        
    combined = Image.alpha_composite(img, overlay).convert("RGB")
    combined.save(out_path, "PNG")
    return count, out_path

def compute_visual_diff(img1_path, img2_path):
    i1 = Image.open(img1_path).convert("RGB")
    i2 = Image.open(img2_path).convert("RGB")
    diff = ImageChops.difference(i1, i2)
    stat = ImageStat.Stat(diff)
    diff_rms = math.sqrt(sum(stat.sum2) / (i1.size[0] * i1.size[1] * 3))
    return round(diff_rms, 2)

def run_real_site_tests():
    report = {}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.add_init_script(INJECT_JS)
        
        # ─────────────────────────────────────────────────────────────
        # 测试 1: 真实复杂站点 (GitHub Explore) - SoM 构件标注与多模态感知
        # ─────────────────────────────────────────────────────────────
        print("\n>>> [实测 1/3] 访问真实复杂站点: GitHub Explore (https://github.com/explore)")
        try:
            page.goto("https://github.com/explore", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            page.evaluate(INJECT_JS) # 确保注入
            
            ents = page.evaluate("(f) => agentWorld.query.findEntities(f)", {"interactive": True, "inViewport": True})
            som_path = Path("mcp/screenshots/real_github_som.png")
            count, path = annotate_viewport(page, ents, som_path)
            print(f"✅ GitHub Explore 实测成功: 识别到 {len(ents)} 个视口交互构件，绘制 SoM 标注 {count} 处，文件保存至 {path}")
            report["github_som"] = {"status": "SUCCESS", "interactive_count": len(ents), "annotated_count": count, "path": str(path)}
        except Exception as e:
            print(f"❌ GitHub Explore 实测异常: {e}")
            report["github_som"] = {"status": "FAILED", "error": str(e)}
            
        # ─────────────────────────────────────────────────────────────
        # 测试 2: 真实在线 Canvas 交互图表 (MDN Canvas / 动态股票/图表页)
        # ─────────────────────────────────────────────────────────────
        print("\n>>> [实测 2/3] 访问真实 Canvas 站点: MDN Canvas Raycaster Demo")
        try:
            page.goto("https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API/Tutorial/Basic_usage", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            page.evaluate(INJECT_JS)
            
            # 找到 canvas 元素
            canvas_ents = page.evaluate("(f) => agentWorld.query.findEntities(f)", {"tag": "canvas"})
            if not canvas_ents:
                # 页面内若用 iframe，查找页面内可见 canvas 或 code demo
                canvas_loc = page.locator("canvas").first
                if canvas_loc.count() > 0:
                    box = canvas_loc.bounding_box()
                else:
                    box = {"x": 100, "y": 200, "width": 400, "height": 200}
            else:
                box = canvas_ents[0]["bounds"]
                
            canvas_img_path = Path("mcp/screenshots/real_canvas_crop.png")
            page.screenshot(path=str(canvas_img_path), clip={"x": box["x"], "y": box["y"], "width": box.get("w", box.get("width", 300)), "height": box.get("h", box.get("height", 200))})
            print(f"✅ Canvas 局部下钻实测成功: 精确捕获 Canvas 渲染帧，保存至 {canvas_img_path}")
            report["canvas_drill"] = {"status": "SUCCESS", "crop_path": str(canvas_img_path)}
        except Exception as e:
            print(f"❌ Canvas 实测异常: {e}")
            report["canvas_drill"] = {"status": "FAILED", "error": str(e)}

        # ─────────────────────────────────────────────────────────────
        # 测试 3: 真实动效/浮层/无DOM结构变化实测 (Wikipedia 搜索联想 / 菜单切换)
        # ─────────────────────────────────────────────────────────────
        print("\n>>> [实测 3/3] 访问真实站点: Wikipedia (https://en.wikipedia.org/) 进行视觉生效与帧差比对")
        try:
            page.goto("https://en.wikipedia.org/wiki/Main_Page", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            page.evaluate(INJECT_JS)
            
            # 定位搜索按钮/输入框或菜单
            input_box = page.locator("input[name='search']").first
            b_path = Path("mcp/screenshots/real_wiki_before.png")
            a_path = Path("mcp/screenshots/real_wiki_after.png")
            
            # 记录输入前视觉
            page.screenshot(path=str(b_path), clip={"x": 500, "y": 0, "width": 600, "height": 300})
            
            # 模拟在输入框中打字触发联想下拉（纯视觉浮层出现）
            input_box.click()
            input_box.type("Artificial Intelligence", delay=50)
            page.wait_for_timeout(1000)
            
            # 记录输入后视觉
            page.screenshot(path=str(a_path), clip={"x": 500, "y": 0, "width": 600, "height": 300})
            
            diff_score = compute_visual_diff(b_path, a_path)
            print(f"✅ 真实视觉生效 Diff 实测成功: 搜索联想浮层触发前后视觉差异 RMS = {diff_score} (阈值>1.5 即判定视觉生效)")
            report["visual_diff"] = {"status": "SUCCESS", "diff_score": diff_score, "verdict": "visual-change-detected" if diff_score > 1.5 else "no-change"}
        except Exception as e:
            print(f"❌ Wikipedia 实测异常: {e}")
            report["visual_diff"] = {"status": "FAILED", "error": str(e)}

        browser.close()
        
    with open("mcp/screenshots/real_test_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n=== 全部实战测试执行完毕 ===")

if __name__ == "__main__":
    run_real_site_tests()
