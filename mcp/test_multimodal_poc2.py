# -*- coding: utf-8 -*-
"""
验证 PR3 (Canvas 视觉下钻裁剪) 和 PR4 (视觉变化双重证据 Diff)
"""
import base64
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageStat
from playwright.sync_api import sync_playwright

# 1. 验证 PR4: 像素级视觉变化差异计算 (Visual Change Verification)
def compute_visual_diff(img_before_path, img_after_path):
    img1 = Image.open(img_before_path).convert("RGB")
    img2 = Image.open(img_after_path).convert("RGB")
    
    # 差异图像
    diff = ImageChops.difference(img1, img2)
    stat = ImageStat.Stat(diff)
    # 计算 RMS 均方根差异
    diff_rms = math.sqrt(sum(stat.sum2) / (img1.size[0] * img1.size[1] * 3))
    
    changed = diff_rms > 1.5  # 阈值判定
    return {
        "visual_changed": changed,
        "diff_score": round(diff_rms, 2),
        "verdict": "visual-change-detected" if changed else "no-visual-change"
    }

# 2. 验证 PR3: Canvas 局部高清裁剪与多模态适配
def extract_canvas_crop(page, canvas_box, output_path):
    page.screenshot(path=str(output_path), clip={
        "x": canvas_box["x"],
        "y": canvas_box["y"],
        "width": canvas_box["w"],
        "height": canvas_box["h"]
    })
    return output_path

def test_pr3_and_pr4():
    print("=== 开始验证 PR3 (Canvas/复杂构件局部裁剪) & PR4 (视觉变化 Diff 验证) ===")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1000, "height": 700})
        
        # 构造包含纯 CSS 颜色过渡/无 DOM 增删，以及 Canvas 的页面
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                #box { width: 120px; height: 60px; background: #3498db; color: white; line-height: 60px; text-align: center; cursor: pointer; }
                #box.active { background: #e74c3c; } /* 纯视觉颜色变化，DOM 结构不变 */
            </style>
        </head>
        <body>
            <div id="box" onclick="this.classList.toggle('active')">点击变色</div>
            <canvas id="myCanvas" width="200" height="100" style="border:1px solid #000; margin-top:20px;"></canvas>
            <script>
                const ctx = document.getElementById('myCanvas').getContext('2d');
                ctx.fillStyle = '#2ecc71';
                ctx.fillRect(20, 20, 160, 60);
                ctx.fillStyle = '#000';
                ctx.font = '16px Arial';
                ctx.fillText('Canvas 内部图表', 35, 55);
            </script>
        </body>
        </html>
        """
        page.set_content(html_content)
        
        # PR3 验证: Canvas 裁剪
        canvas_box = page.locator("#myCanvas").bounding_box()
        crop_path = Path("mcp/screenshots/canvas_crop.png")
        extract_canvas_crop(page, {"x": canvas_box["x"], "y": canvas_box["y"], "w": canvas_box["width"], "h": canvas_box["height"]}, crop_path)
        print(f"PR3 Canvas 局部裁剪成功: 保存至 {crop_path}")
        
        # PR4 验证: 点击前截帧 -> 点击变色 -> 点击后截帧 -> 视觉 Diff 计算
        before_frame = Path("mcp/screenshots/frame_before.png")
        after_frame = Path("mcp/screenshots/frame_after.png")
        
        page.screenshot(path=str(before_frame), clip={"x": 0, "y": 0, "width": 300, "height": 100})
        # 触发点击（纯 CSS 状态改变）
        page.click("#box")
        page.screenshot(path=str(after_frame), clip={"x": 0, "y": 0, "width": 300, "height": 100})
        
        diff_res = compute_visual_diff(before_frame, after_frame)
        print(f"PR4 视觉 Diff 判定结果: {diff_res}")
        assert diff_res["visual_changed"] is True, "应当检测到纯视觉颜色变化"
        
        browser.close()

if __name__ == "__main__":
    test_pr3_and_pr4()
