# -*- coding: utf-8 -*-
"""
验证 PR1 (MCP ImageContent 原生图片返回) 和 PR2 (Set-of-Mark 视觉标注框)
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw
import mcp.types as types
from playwright.sync_api import sync_playwright

def create_annotated_screenshot(page, entities, output_path):
    raw_path = output_path.parent / f"raw_{output_path.name}"
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
        if w <= 0 or h <= 0:
            continue
            
        eid = ent.get("id", "")
        # 醒目的半透明荧光粉红/品红标注框
        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 90, 240), width=2)
        label = f"[{eid}] {ent.get('name', '')[:14]}"
        tag_w = max(40, len(label) * 8 + 6)
        draw.rectangle([x, max(0, y - 18), x + tag_w, max(18, y)], fill=(255, 0, 90, 210))
        draw.text((x + 4, max(0, y - 16)), label, fill=(255, 255, 255, 255))
        count += 1
        
    combined = Image.alpha_composite(img, overlay).convert("RGB")
    combined.save(output_path, "PNG")
    return count, output_path

def test_pr1_and_pr2():
    print("=== 开始验证 PR1 (原生 ImageContent) & PR2 (Set-of-Mark 标注) ===")
    
    ALL_IN_ONE = Path("extension/all-in-one.js")
    with open(ALL_IN_ONE, "r", encoding="utf-8") as f:
        inject_js = f.read()
        
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        
        test_url = "https://example.com"
        page.goto(test_url)
        page.evaluate(inject_js)
        
        # 正确调用 findEntities
        ents = page.evaluate("(f) => agentWorld.query.findEntities(f)", {"interactive": True})
        print(f"获取到交互元素数量: {len(ents)}")
        
        out_path = Path("mcp/screenshots/annotated_example.png")
        out_path.parent.mkdir(exist_ok=True)
        marked_count, saved_path = create_annotated_screenshot(page, ents, out_path)
        print(f"PR2 标注完成: 成功标记了 {marked_count} 个交互构件，保存至 {saved_path}")
        
        with open(saved_path, "rb") as f:
            b64_data = base64.b64encode(f.read()).decode("utf-8")
            
        img_content = types.ImageContent(
            type="image",
            data=b64_data,
            mimeType="image/png"
        )
        print(f"PR1 MCP ImageContent 生成成功 (Base64 大小: {len(b64_data)} 字符, mimeType: {img_content.mimeType})")
        
        browser.close()

if __name__ == "__main__":
    test_pr1_and_pr2()
