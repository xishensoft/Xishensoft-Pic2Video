# -*- coding: utf-8 -*-
"""字幕样式「预览 / 烧录」一致性实测。

用同一份样式字典分别生成 ASS（交给 ffmpeg+libass 真渲）和 QPainter 绘制（预览用的
那套映射），再逐像素比对包围盒与掩膜 IoU，量化两者能对齐到什么程度。

产出（同目录）：probe.ass / ff_<case>.png / qt_<case>.png / compare_<case>.png / metrics.json
"""
import json
import os
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF,
                           QGuiApplication, QImage, QPainter, QPainterPath, QPen)

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1920, 1080
BG = "#202020"
FFMPEG = "ffmpeg"
FONTS_DIR = r"C:\Windows\Fonts"
FONT_NAME = "Microsoft YaHei"

# ---------------------------------------------------------------- 样式字典
# 这是方案里提议的 style 结构：数值以 1080p 为基准，边距用画面宽/高的百分比。
CASES = [
    {
        "id": "bottom",
        "text": "第一行：预览要和成片一致\n第二行：换行与描边都要对齐",
        "style": {
            "font": FONT_NAME, "size": 72, "bold": False, "italic": False,
            "primary": "#FFD24D", "outline_color": "#402000", "outline": 3.0,
            "shadow_color": "#000000", "shadow": 0.0,
            "back_color": "#000000", "back_opacity": 0,
            "align": 2, "margin_l": 6.0, "margin_r": 6.0, "margin_v": 6.0,
        },
    },
    {
        "id": "mid_right",
        "text": "右侧居中",
        "style": {
            "font": FONT_NAME, "size": 64, "bold": False, "italic": False,
            "primary": "#FFFFFF", "outline_color": "#101010", "outline": 2.0,
            "shadow_color": "#000000", "shadow": 0.0,
            "back_color": "#000000", "back_opacity": 0,
            "align": 6, "margin_l": 4.0, "margin_r": 8.0, "margin_v": 6.0,
        },
    },
    {
        "id": "topleft_box",
        "text": "左上角字幕块",
        "style": {
            "font": FONT_NAME, "size": 56, "bold": True, "italic": False,
            "primary": "#FFFFFF", "outline_color": "#000000", "outline": 0.0,
            "shadow_color": "#000000", "shadow": 0.0,
            "back_color": "#1E5AA8", "back_opacity": 100,
            "align": 7, "margin_l": 4.0, "margin_r": 4.0, "margin_v": 8.0,
        },
    },
]


def ass_colour(hex_rgb, alpha=0):
    """#RRGGBB -> ASS 的 &HAABBGGRR（A=00 不透明）。"""
    h = hex_rgb.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "&H%02X%02X%02X%02X" % (alpha, b, g, r)


def anchor_px(style, w=W, h=H):
    """九宫格 + 百分比边距 -> 锚点像素（与 ASS \an 的锚点语义一致）。"""
    align = int(style.get("align", 2))
    col = (align - 1) % 3           # 0 左 / 1 中 / 2 右
    # ASS \an 的 keypad 编号：1-3 贴底、4-6 居中、7-9 贴顶，和 CSS 的行序相反
    row = 2 - (align - 1) // 3      # 0 上 / 1 中 / 2 下
    ml = float(style.get("margin_l", 0.0))
    mr = float(style.get("margin_r", 0.0))
    mv = float(style.get("margin_v", 0.0))
    x = w * ml / 100.0 if col == 0 else (w / 2.0 if col == 1 else w * (1 - mr / 100.0))
    y = h * mv / 100.0 if row == 0 else (h / 2.0 if row == 1 else h * (1 - mv / 100.0))
    return align, round(x), round(y)


def line_factor(family):
    """字体自身 (ascent+descent) 相对 em 的倍数。

    libass 把 Style 的 Fontsize 当成「ascent+descent 的高度」来缩放字形，
    而 Qt 的 setPixelSize 给的是 em 框；两者差这个倍数（msyh 约 1.32）。
    """
    font = QFont(family)
    font.setPixelSize(1000)
    fm = QFontMetricsF(font)
    return (fm.ascent() + fm.descent()) / 1000.0


def ass_fontsize(px_size, factor):
    """把「em 像素字号」换算成 libass 要的 Fontsize。"""
    return max(1, int(round(float(px_size) * factor)))


def ass_style_line(name, style, w=W, h=H, factor=1.0):
    """style -> 一行 ASS Style（字段顺序按 ASS_STYLE_FORMAT）。"""
    boxed = float(style.get("back_opacity", 0)) > 0
    # 实测：libass 的 BS=3 用 OutlineColour 画框（不是 BackColour），BS=4 才是
    # 「底色框 + 描边」；所以底色框一律走 BS=4。
    border_style = 4 if boxed else 1
    back_alpha = 255 - round(255 * float(style.get("back_opacity", 0)) / 100.0)
    _, mx, my = anchor_px(style, w, h)
    fields = [
        name,
        style["font"],
        str(ass_fontsize(style["size"], factor)),
        ass_colour(style["primary"]),
        ass_colour(style["primary"]),
        ass_colour(style.get("outline_color", "#000000")),
        ass_colour(style.get("back_color", "#000000"), back_alpha),
        "-1" if style.get("bold") else "0",
        "-1" if style.get("italic") else "0",
        "0", "0",            # Underline / StrikeOut
        "100", "100",        # ScaleX / ScaleY
        "0", "0",            # Spacing / Angle
        str(border_style),
        "%.1f" % float(style.get("outline", 0.0)),
        "%.1f" % float(style.get("shadow", 0.0)),
        str(int(style.get("align", 2))),
        str(round(w * float(style.get("margin_l", 0)) / 100.0)),
        str(round(w * float(style.get("margin_r", 0)) / 100.0)),
        str(round(h * float(style.get("margin_v", 0)) / 100.0)),
        "1",                 # Encoding
    ]
    return "Style: " + ",".join(fields)


def build_ass(cases, w=W, h=H, factor=1.0):
    out = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: %d" % w,
        "PlayResY: %d" % h,
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for case in cases:
        out.append(ass_style_line(case["id"], case["style"], w, h, factor))
    out += ["", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for case in cases:
        align, x, y = anchor_px(case["style"], w, h)
        text = case["text"].replace("\n", r"\N")
        # \an 与 \pos 是必写的：百分比边距要换算成像素锚点，MarginV 表达不了九宫格全部位置
        out.append("Dialogue: 0,0:00:00.00,0:00:05.00,%s,,0,0,0,,{\\an%d\\pos(%d,%d)}%s"
                   % (case["id"], align, x, y, text))
    return "\n".join(out) + "\n"


def filter_path(path):
    """和 pic2video._filter_path 同一套转义：反斜杠 -> 正斜杠、冒号转义、整体单引号。"""
    p = os.path.abspath(path).replace("\\", "/").replace(":", r"\:")
    return "'%s'" % p


def render_ff(ass_path, out_png):
    vf = ("subtitles=filename=%s:fontsdir=%s"
          % (filter_path(ass_path), filter_path(FONTS_DIR)))
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "color=c=0x202020:s=%dx%d:d=1" % (W, H),
           "-vf", vf, "-frames:v", "1", out_png]
    subprocess.run(cmd, check=True)
    return out_png


def load_fonts():
    """offscreen 平台不会自动加载系统字体：显式注册，否则全是缺字方块。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
        path = os.path.join(FONTS_DIR, name)
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    fams = QFontDatabase.families()
    for want in ("Microsoft YaHei", "Microsoft YaHei UI"):
        if want in fams:
            return want
    return fams[0] if fams else FONT_NAME


def qt_font(style, family=None):
    font = QFont(family or style["font"])
    font.setPixelSize(max(8, int(style["size"])))
    font.setBold(bool(style.get("bold")))
    font.setItalic(bool(style.get("italic")))
    return font


def render_qt(case, out_png, family=None):
    """预览侧映射：样式字典 -> QPainter 直接画（不用 QLabel，避免样式表干扰）。"""
    style = case["style"]
    img = QImage(W, H, QImage.Format.Format_RGB32)
    img.fill(QColor(BG))
    qp = QPainter(img)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    qp.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font = qt_font(style, family)
    qp.setFont(font)
    fm = QFontMetricsF(font)
    # 行高取字体自身的 ascent+descent（libass 默认行距也是字体行距，不额外加行间距）
    line_h = fm.ascent() + fm.descent()
    lines = case["text"].split("\n")
    align, ax, ay = anchor_px(style, W, H)
    col = (align - 1) % 3
    row = 2 - (align - 1) // 3
    block_h = line_h * len(lines)
    # 锚点 y 对应整块的上/中/下边缘
    top = ay if row == 0 else (ay - block_h / 2.0 if row == 1 else ay - block_h)
    colors = [QColor(c) for c in [style["primary"]]]
    outline = float(style.get("outline", 0.0))
    boxed = float(style.get("back_opacity", 0)) > 0
    widths = [fm.horizontalAdvance(line) for line in lines]
    block_w = max(widths) if widths else 0.0
    if boxed:
        # libass 的框 = 文本宽 + 2×Outline、整块高 + 2×Outline，没有额外内边距
        pad = outline
        qp.setPen(Qt.PenStyle.NoPen)
        bc = QColor(style.get("back_color", "#000000"))
        bc.setAlphaF(float(style.get("back_opacity", 100)) / 100.0)
        x0 = ax - pad if col == 0 else (ax - block_w / 2.0 - pad if col == 1 else ax - block_w - pad)
        qp.fillRect(int(round(x0)), int(round(top - pad)),
                    int(round(block_w + 2 * pad)), int(round(block_h + 2 * pad)), bc)
    for i, line in enumerate(lines):
        tw = widths[i]
        if col == 0:
            x = ax
        elif col == 1:
            x = ax - tw / 2.0
        else:
            x = ax - tw
        baseline = top + line_h * i + fm.ascent()
        path = QPainterPath()
        path.addText(QPointF(x, baseline), font, line)
        if outline > 0:
            # libass 的 Outline 是外扩描边；QPen 以路径为中轴，故取 2 倍宽
            qp.setPen(QPen(QColor(style.get("outline_color", "#000000")),
                           outline * 2.0, Qt.PenStyle.SolidLine,
                           Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            qp.setBrush(Qt.BrushStyle.NoBrush)
            qp.drawPath(path)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.fillPath(path, colors[0])
    qp.end()
    img.save(out_png)
    return out_png


def mask_of(img, bg):
    """非背景像素掩膜（set of (x, y)），并给出包围盒。"""
    bgr, bgg, bgb = bg.red(), bg.green(), bg.blue()
    pts = set()
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if abs(c.red() - bgr) + abs(c.green() - bgg) + abs(c.blue() - bgb) > 24:
                pts.add((x, y))
                if x < x0: x0 = x
                if y < y0: y0 = y
                if x > x1: x1 = x
                if y > y1: y1 = y
    if x1 < 0:
        return pts, None
    return pts, (x0, y0, x1, y1)


def composite(ff_img, qt_img, diff_img, out_png, panel=(640, 360)):
    pw, ph = panel
    canvas = QImage(pw * 3, ph, QImage.Format.Format_RGB32)
    canvas.fill(QColor("#101010"))
    qp = QPainter(canvas)
    for i, im in enumerate((ff_img, qt_img, diff_img)):
        qp.drawImage(i * pw, 0, im.scaled(pw, ph, Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
    qp.end()
    canvas.save(out_png)
    return out_png


def union_box(a, b, pad=28):
    if not a or not b:
        return None
    return (max(0, min(a[0], b[0]) - pad), max(0, min(a[1], b[1]) - pad),
            min(W - 1, max(a[2], b[2]) + pad), min(H - 1, max(a[3], b[3]) + pad))


def make_zoom(ff_img, qt_img, box, out_png):
    """1:1 放大对比：左 libass、右 QPainter，便于肉眼看对齐。"""
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    gap = 12
    canvas = QImage(cw * 2 + gap, ch, QImage.Format.Format_RGB32)
    canvas.fill(QColor("#000000"))
    qp = QPainter(canvas)
    qp.drawImage(0, 0, ff_img.copy(x0, y0, cw, ch))
    qp.drawImage(cw + gap, 0, qt_img.copy(x0, y0, cw, ch))
    qp.end()
    canvas.save(out_png)
    return out_png


def main():
    QGuiApplication(sys.argv)
    family = load_fonts()
    factor = line_factor(family)
    print("qt family:", family, "line_factor:", round(factor, 4))
    metrics = {"font_family": family, "line_factor": round(factor, 4), "cases": []}
    for case in CASES:
        ass_path = os.path.join(HERE, "probe_%s.ass" % case["id"])
        with open(ass_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(build_ass([case], factor=factor))
        ff_png = os.path.join(HERE, "ff_%s.png" % case["id"])
        qt_png = os.path.join(HERE, "qt_%s.png" % case["id"])
        render_ff(ass_path, ff_png)
        render_qt(case, qt_png, family)
        ff_img = QImage(ff_png)
        qt_img = QImage(qt_png)
        bg = QColor(BG)
        mf, bf = mask_of(ff_img, bg)
        mq, bq = mask_of(qt_img, bg)
        inter = len(mf & mq)
        union = len(mf | mq)
        diff_img = QImage(W, H, QImage.Format.Format_RGB32)
        diff_img.fill(QColor("#101010"))
        dp = QPainter(diff_img)
        for (x, y) in mf:
            dp.setPen(QColor("#FF4040") if (x, y) not in mq else QColor("#8A8A8A"))
            dp.drawPoint(x, y)
        for (x, y) in mq:
            if (x, y) not in mf:
                dp.setPen(QColor("#40FF7A"))
                dp.drawPoint(x, y)
        dp.end()
        diff_png = os.path.join(HERE, "diff_%s.png" % case["id"])
        diff_img.save(diff_png)
        cmp_png = composite(ff_img, qt_img, diff_img,
                            os.path.join(HERE, "compare_%s.png" % case["id"]))
        zb = union_box(bf, bq)
        zoom_png = None
        if zb:
            zoom_png = make_zoom(ff_img, qt_img, zb,
                                 os.path.join(HERE, "zoom_%s.png" % case["id"]))
        item = {
            "case": case["id"],
            "ff_bbox": bf, "qt_bbox": bq,
            "ff_px": len(mf), "qt_px": len(mq),
            "iou": round(inter / union, 4) if union else None,
            "bbox_delta": (None if not (bf and bq) else
                           [bq[0] - bf[0], bq[1] - bf[1], bq[2] - bf[2], bq[3] - bf[3]]),
            "compare": cmp_png,
            "zoom": zoom_png,
        }
        metrics["cases"].append(item)
        print(json.dumps(item, ensure_ascii=False))
    with open(os.path.join(HERE, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    for case in CASES:
        print("--- probe_%s.ass ---" % case["id"])
        print(build_ass([case], factor=factor))


main()
