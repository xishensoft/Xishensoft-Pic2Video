# -*- coding: utf-8 -*-
"""BorderStyle=3 的底色框在 libass 里什么时候真的画出来。"""
import os, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 960, 540
BG = (32, 32, 32)
FONTS = r"C:\Windows\Fonts"


def fpath(p):
    return "'%s'" % os.path.abspath(p).replace("\\", "/").replace(":", r"\:")


def build(variants):
    out = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W, "PlayResY: %d" % H,
           "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
           "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
           "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for i, (bs, outc, back) in enumerate(variants):
        out.append("Style: B%d,Microsoft YaHei,40,&H00FFFFFF,&H00FFFFFF,&H00000000,%s,"
                   "0,0,0,0,100,100,0,0,%d,%s,0,7,0,0,0,1" % (i, back, bs, outc))
    out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text"]
    for i, _ in enumerate(variants):
        out.append("Dialogue: 0,0:00:00.00,0:00:05.00,B%d,,0,0,0,,{\\an7\\pos(60,%d)}底色框测试"
                   % (i, 40 + i * 90))
    return "\n".join(out) + "\n"


VARIANTS = [
    (3, "0", "&H00A85A1E"),   # 当前写法：BorderStyle 3 / outline 0 / 不透明蓝
    (3, "2", "&H00A85A1E"),   # 再给点 outline
    (4, "0", "&H00A85A1E"),   # BorderStyle 4
    (1, "8", "&H00A85A1E"),   # 对照：普通描边（不走底色框）
    (3, "0", "&H80A85A1E"),   # 半透明底框
]


def main():
    a = os.path.join(HERE, "box_probe.ass")
    open(a, "w", encoding="utf-8", newline="\n").write(build(VARIANTS))
    out = os.path.join(HERE, "box_probe.png")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x202020:s=%dx%d:d=1" % (W, H), "-vf",
                    "subtitles=filename=%s:fontsdir=%s" % (fpath(a), fpath(FONTS)),
                    "-frames:v", "1", out], check=True)
    # 用 ffmpeg 自己数「蓝色系」像素，避免引入 Pillow
    print("rendered:", out)
    from PySide6.QtGui import QGuiApplication, QImage
    import sys
    QGuiApplication(sys.argv)
    img = QImage(out)
    for i, (bs, outc, back) in enumerate(VARIANTS):
        y0, y1 = 20 + i * 90, 60 + i * 90
        blue = 0
        for y in range(max(0, y0), min(img.height(), y1 + 40)):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                r, g, b = c.red(), c.green(), c.blue()
                # 目标底色 #1E5AA8：蓝明显大于红
                if b > 80 and b > r + 30 and abs(g - 90) < 70:
                    blue += 1
        print("variant %d BorderStyle=%d Outline=%s Back=%s -> blue_px=%d"
              % (i, bs, outc, back, blue))


main()
