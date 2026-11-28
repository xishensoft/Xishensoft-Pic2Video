# -*- coding: utf-8 -*-
"""BorderStyle 3 / 4 的填充色与内边距规则。"""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 960, 760
FONTS = r"C:\Windows\Fonts"
TEXT = "底色框"
GREEN = "&H0030C020"   # BGR：绿
RED = "&H000000FF"     # BGR：红


def fpath(p):
    return "'%s'" % os.path.abspath(p).replace("\\", "/").replace(":", r"\:")


# (BorderStyle, Outline, OutlineColour, BackColour, 说明)
VARIANTS = [
    (3, 2, RED, GREEN, "BS3 outline2 描边红 底色绿"),
    (3, 0.5, RED, GREEN, "BS3 outline0.5"),
    (4, 0, RED, GREEN, "BS4 outline0 底色绿"),
    (4, 3, RED, GREEN, "BS4 outline3 底色绿"),
    (4, 0, RED, "&H8030C020", "BS4 半透明底框"),
]


def build():
    out = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W, "PlayResY: %d" % H,
           "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
           "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
           "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for i, (bs, oc, occ, bc, _d) in enumerate(VARIANTS):
        out.append("Style: V%d,Microsoft YaHei,40,&H00FFFFFF,&H00FFFFFF,%s,%s,0,0,0,0,100,100,"
                   "0,0,%d,%s,0,7,0,0,0,1" % (i, occ, bc, bs, oc))
    out += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text"]
    for i, _v in enumerate(VARIANTS):
        out.append("Dialogue: 0,0:00:00.00,0:00:05.00,V%d,,0,0,0,,{\\an7\\pos(60,%d)}%s"
                   % (i, 30 + i * 140, TEXT))
    return "\n".join(out) + "\n"


def main():
    a = os.path.join(HERE, "box_probe2.ass")
    open(a, "w", encoding="utf-8", newline="\n").write(build())
    out = os.path.join(HERE, "box_probe2.png")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x202020:s=%dx%d:d=1" % (W, H), "-vf",
                    "subtitles=filename=%s:fontsdir=%s" % (fpath(a), fpath(FONTS)),
                    "-frames:v", "1", out], check=True)
    from PySide6.QtGui import QGuiApplication, QImage
    QGuiApplication(sys.argv)
    img = QImage(out)
    for i, (bs, oc, occ, bc, desc) in enumerate(VARIANTS):
        y0, y1 = 20 + i * 140, 40 + i * 140 + 80
        stats = {"green": 0, "red": 0, "dark": 0}
        gx0 = gy0 = 10**9
        gx1 = gy1 = -1
        for y in range(max(0, y0), min(img.height(), y1)):
            for x in range(img.width()):
                c = img.pixelColor(x, y)
                r, g, b = c.red(), c.green(), c.blue()
                if g > 100 and g > r + 40 and g > b + 40:
                    stats["green"] += 1
                    gx0 = min(gx0, x); gy0 = min(gy0, y); gx1 = max(gx1, x); gy1 = max(gy1, y)
                elif r > 100 and r > g + 40:
                    stats["red"] += 1
                elif r < 30 and g < 30 and b < 30:
                    stats["dark"] += 1
        gbox = None if gx1 < 0 else (gx0, gy0, gx1, gy1)
        print("%-28s BS=%d outline=%-3s green=%-6d red=%-6d dark=%-6d green_bbox=%s"
              % (desc, bs, oc, stats["green"], stats["red"], stats["dark"], gbox))


main()
