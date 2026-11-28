# -*- coding: utf-8 -*-
"""标定 libass 的 Fontsize 与 Qt setPixelSize 之间的系数。

同一串字，在 4 个字号下分别让 libass 与 QPainter 画，量墨迹包围盒，求比例。
"""
import json, os, subprocess, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication,
                           QImage, QPainter, QPainterPath, QPen)

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
BG = "#202020"
FONTS_DIR = r"C:\Windows\Fonts"
TEXT = "国国国国"
SIZES = [36, 48, 72, 96]


def ass(sizes, path):
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W, "PlayResY: %d" % H,
             "WrapStyle: 0", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
             "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
             "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"]
    for i, _ in enumerate(sizes):
        lines.append("Style: S%d,Microsoft YaHei,%d,&H00FFFFFF,&H00FFFFFF,&H00000000,"
                     "&HFF000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1" % (i, 10))
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
              "MarginV, Effect, Text"]
    for i, s in enumerate(sizes):
        x = 100 + i * 450
        lines.append("Dialogue: 0,0:00:00.00,0:00:05.00,S%d,,0,0,0,,{\\an7\\pos(%d,%d)\\fs%d}%s"
                     % (i, x, 120, s, TEXT))
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")


def fpath(p):
    return "'%s'" % os.path.abspath(p).replace("\\", "/").replace(":", r"\:")


def bbox(img):
    x0 = y0 = 10**9
    x1 = y1 = -1
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - 32) + abs(c.green() - 32) + abs(c.blue() - 32) > 24:
                x0 = min(x0, x); y0 = min(y0, y); x1 = max(x1, x); y1 = max(y1, y)
    return None if x1 < 0 else (x0, y0, x1, y1)


def draw_qt(size, path, fam):
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(BG))
    qp = QPainter(img)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    font = QFont(fam); font.setPixelSize(size)
    qp.setFont(font)
    fm = QFontMetricsF(font)
    p = QPainterPath()
    p.addText(QPointF(100.0, 120.0 + fm.ascent()), font, TEXT)
    qp.setPen(Qt.PenStyle.NoPen)
    qp.fillPath(p, QColor("#FFFFFF"))
    qp.end()
    img.save(path)
    return fm.horizontalAdvance(TEXT), fm.ascent() + fm.descent(), fm.height()


def main():
    QGuiApplication(sys.argv)
    for n in ("msyh.ttc", "msyhbd.ttc"):
        pp = os.path.join(FONTS_DIR, n)
        if os.path.exists(pp):
            QFontDatabase.addApplicationFont(pp)
    fam = "Microsoft YaHei" if "Microsoft YaHei" in QFontDatabase.families() else QFontDatabase.families()[0]
    a = os.path.join(HERE, "calib.ass")
    ass(SIZES, a)
    out = os.path.join(HERE, "calib_ff.png")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x202020:s=%dx%d:d=1" % (W, H), "-vf",
                    "subtitles=filename=%s:fontsdir=%s" % (fpath(a), fpath(FONTS_DIR)),
                    "-frames:v", "1", out], check=True)
    ff = QImage(out)
    rows = []
    # 每个字号占一个 450 宽的区域，逐区域量墨迹
    for i, s in enumerate(SIZES):
        x0 = 100 + i * 450
        sub = ff.copy(x0 - 10, 100, 440, 260)
        bb = bbox(sub)
        qpath = os.path.join(HERE, "calib_qt_%d.png" % s)
        adv, lh, fh = draw_qt(s, qpath, fam)
        qb = bbox(QImage(qpath).copy(0, 100, 500, 300))
        rows.append({
            "size": s,
            "ff_ink_w": None if not bb else bb[2] - bb[0] + 1,
            "ff_ink_h": None if not bb else bb[3] - bb[1] + 1,
            "qt_ink_w": None if not qb else qb[2] - qb[0] + 1,
            "qt_ink_h": None if not qb else qb[3] - qb[1] + 1,
            "qt_advance": round(adv, 1), "qt_line_h": round(lh, 2),
        })
        if bb and qb:
            rows[-1]["w_ratio"] = round((bb[2] - bb[0] + 1) / (qb[2] - qb[0] + 1), 3)
            rows[-1]["h_ratio"] = round((bb[3] - bb[1] + 1) / (qb[3] - qb[1] + 1), 3)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    json.dump(rows, open(os.path.join(HERE, "calib.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


main()
