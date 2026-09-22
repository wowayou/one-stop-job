#!/usr/bin/env python3
"""生成应用图标源图（1024×1024 PNG）。

设计主题「预筛漏斗」：批量岗位从漏斗口进入 → 收窄筛选 → 一条被选中的岗位带对勾落出。
- 底：品牌青绿渐变圆角砖（与 --c-brand #147d74 / --c-sidebar-bg #17201b 同源）
- 漏斗：米白描边（off-white），顶宽底窄，编码「多进少出」的收窄
- 选中项：珊瑚色圆点 + 白色对勾（与品牌强调色 #c3542d 同源），漏斗出口唯一落出的那条

只画一个主体形状 + 一个强调点，保证 32px 仍可辨识。
用法：python3 design_icon.py  → 输出 icon_source.png，再由 generate_icons.py 切各尺寸。
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SS = 4  # 超采样倍数，先大后缩得到抗锯齿边缘
BASE = 1024
S = BASE * SS

OUT = Path(__file__).parent / "icon_source.png"

# —— 调色板（全部与 styles.css 品牌令牌同源）——
TEAL_TOP = (26, 138, 126)      # 顶部亮青
TEAL_BOTTOM = (13, 74, 68)     # 底部深青
FUNNEL = (244, 247, 243)       # 漏斗米白
CORAL = (216, 108, 74)         # 选中项珊瑚
CORAL_DARK = (176, 78, 48)
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def gradient_tile(size):
    """竖向渐变，用圆角遮罩裁角。"""
    grad = Image.new("RGB", (size, size))
    px = grad.load()
    for y in range(size):
        c = lerp(TEAL_TOP, TEAL_BOTTOM, y / (size - 1))
        for x in range(size):
            px[x, y] = c
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tile.paste(grad, (0, 0), rounded_mask(size, radius=int(size * 0.22)))
    return tile


def thick_line(draw, pts, width, fill):
    """带圆角端点/接头的粗线：line + 每个顶点补圆。"""
    draw.line(pts, fill=fill, width=width, joint="curve")
    r = width // 2
    for x, y in pts:
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def draw_icon():
    img = gradient_tile(S)
    d = ImageDraw.Draw(img)

    cx = S / 2
    stroke = int(S * 0.05)

    # —— 漏斗（梯形收窄 + 短竖颈）——
    top_y = S * 0.24
    neck_y = S * 0.55
    half_top = S * 0.235
    half_neck = S * 0.052
    funnel = [
        (cx - half_top, top_y),
        (cx + half_top, top_y),
        (cx + half_neck, neck_y),
        (cx + half_neck, neck_y + S * 0.06),
        (cx - half_neck, neck_y + S * 0.06),
        (cx - half_neck, neck_y),
    ]
    thick_line(d, funnel + [funnel[0]], stroke, FUNNEL)

    # —— 顶部三条「待筛岗位」，宽度递减，暗示多条进入 ——
    bar_h = int(S * 0.032)
    for i, w in enumerate((0.30, 0.22, 0.14)):
        y = top_y - S * 0.12 + i * (bar_h + S * 0.028)
        half = S * w / 2
        d.rounded_rectangle(
            [cx - half, y, cx + half, y + bar_h],
            radius=bar_h // 2,
            fill=lerp(FUNNEL, TEAL_TOP, 0.15 + i * 0.12),
        )

    # —— 选中项：珊瑚圆点 + 白对勾（漏斗口正下方）——
    dot_cy = S * 0.76
    dot_r = S * 0.135
    d.ellipse(
        [cx - dot_r, dot_cy - dot_r, cx + dot_r, dot_cy + dot_r],
        fill=CORAL,
        outline=CORAL_DARK,
        width=max(2, SS),
    )
    check = [
        (cx - dot_r * 0.42, dot_cy + dot_r * 0.02),
        (cx - dot_r * 0.08, dot_cy + dot_r * 0.36),
        (cx + dot_r * 0.5, dot_cy - dot_r * 0.36),
    ]
    thick_line(d, check, int(S * 0.028), WHITE)

    img = img.resize((BASE, BASE), Image.Resampling.LANCZOS)
    img.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    draw_icon()
