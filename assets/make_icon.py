# -*- coding: utf-8 -*-
"""生成应用图标 assets/icon.ico（多尺寸）与 assets/icon.png。

设计：深色圆角方块 + 三张错开的照片卡（用主色系） + 右侧播放三角。
只用于打包，不参与运行时逻辑。
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "assets")
S = 1024


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def make(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 1024.0
    # 底：深灰圆角
    rounded(d, (0, 0, size - 1, size - 1), int(210 * s), (32, 36, 44, 255))
    # 三张照片卡（错开堆叠）
    cards = [
        ((150, 210, 620, 700), (59, 130, 246, 255)),
        ((250, 300, 720, 790), (14, 165, 233, 255)),
        ((350, 390, 820, 880), (251, 114, 153, 255)),
    ]
    for (x0, y0, x1, y1), col in cards:
        rounded(d, (int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)), int(48 * s), col)
    # 播放三角（压在卡片右下）
    tri = [(600, 560), (600, 830), (850, 695)]
    d.polygon([(int(x * s), int(y * s)) for x, y in tri], fill=(255, 255, 255, 255))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    big = make(S)
    big.save(os.path.join(OUT, "icon.png"))
    big.save(os.path.join(OUT, "icon.ico"),
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("written", os.path.join(OUT, "icon.ico"))


if __name__ == "__main__":
    main()
