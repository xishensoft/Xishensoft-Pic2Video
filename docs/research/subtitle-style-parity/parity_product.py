# -*- coding: utf-8 -*-
"""阶段 2 验收：预览（paint_cues）与烧录（format_ass + libass）是否一致。

走的是产品自己的两条代码路径，不手写 ASS、不手画字：
  A) pic2video.format_ass(cues, ...) + ass_filter() + ffmpeg 真烧 → PNG
  B) pic2video.paint_cues(qp, cues, ...) 画到 QImage → PNG
然后按墨迹掩膜算 IoU 与包围盒偏差，并检查 540p 与 1080p 的相对位置。
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pic2video as p  # noqa: E402
import subtitle_style as S  # noqa: E402

from PyQt6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402

app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
from PyQt6.QtGui import QFontDatabase  # noqa: E402

for _f in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc"):
    _path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", _f)
    if os.path.exists(_path):
        QFontDatabase.addApplicationFont(_path)

OUT = os.path.dirname(os.path.abspath(__file__))

BG = "0x202020"


def style_table():
    base = dict(S.DEFAULT_SUBTITLE_STYLE)
    return {"Default": base}


def burn(cues, w, h, path, styles=None, default="Default"):
    """真代码路径：format_ass → ass_filter → ffmpeg。"""
    ass = p.format_ass(cues, font="Microsoft YaHei", size=48, w=w, h=h,
                       styles=styles, default_style=default)
    ap = os.path.join(tempfile.gettempdir(), "p2v_parity.ass")
    with open(ap, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(ass)
    vf = p.ass_filter(ap, "Microsoft YaHei", 48, p.default_font_dir())
    cmd = [p.FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "color=c=%s:s=%dx%d:d=1" % (BG, w, h),
           "-vf", vf + ",format=rgb24", "-frames:v", "1", "-y", path]
    subprocess.run(cmd, check=True)
    return path


def preview(cues, w, h, path, styles=None, default="Default"):
    """真代码路径：paint_cues。"""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#202020"))   # 和烧录底色一致，掩膜才有可比性
    from PyQt6.QtGui import QPainter
    qp = QPainter(img)
    try:
        p.paint_cues(qp, cues, styles, default, w, h, "Microsoft YaHei", 48)
    finally:
        qp.end()
    img.save(path)
    return path


def mask(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    px = im.load()
    w, h = im.size
    pts = set()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - 32) + abs(g - 32) + abs(b - 32) > 24:
                pts.add((x, y))
    return pts, (w, h)


def bbox(pts):
    if not pts:
        return None
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


CASES = [
    ("bottom", [p._cue(0.0, 1.0, "第一行：预览要和成片一致\n第二行：换行与描边都要对齐",
                       overrides={"primary": "#FFD24D", "outline_color": "#402000",
                                  "outline": 3.0, "size": 72, "margin_v": 6.0})]),
    ("mid_right", [p._cue(0.0, 1.0, "右侧居中",
                          overrides={"size": 64, "align": 6, "margin_r": 8.0,
                                     "outline": 2.0})]),
    ("topleft_box", [p._cue(0.0, 1.0, "左上角字幕块",
                            overrides={"size": 56, "bold": True, "align": 7,
                                       "back_color": "#1E5AA8", "back_opacity": 100,
                                       "margin_l": 4.0, "margin_v": 8.0})]),
]


def run(w, h, tag, save_figs):
    styles = style_table()
    rows = []
    for name, cues in CASES:
        ff_png = os.path.join(OUT, "prod_ff_%s_%s.png" % (name, tag))
        qt_png = os.path.join(OUT, "prod_qt_%s_%s.png" % (name, tag))
        burn(cues, w, h, ff_png, styles)
        preview(cues, w, h, qt_png, styles)
        a, size = mask(ff_png)
        b, _ = mask(qt_png)
        inter = len(a & b)
        union = len(a | b)
        iou = inter / union if union else 1.0
        ba, bb = bbox(a), bbox(b)
        if ba and bb:
            delta = [bb[i] - ba[i] for i in range(4)]
        else:
            delta = [999, 999, 999, 999]
        rows.append((name, iou, delta, len(a), len(b)))
        print("%-14s %-5s IoU %.4f  bbox_delta %s  ff=%d qt=%d"
              % (name, tag, iou, delta, len(a), len(b)))
        if save_figs:
            from PIL import Image
            im1 = Image.open(ff_png).convert("RGB")
            im2 = Image.open(qt_png).convert("RGB")
            cmb = Image.new("RGB", (w // 3, max(1, h // 3) * 2))
            cmb.paste(im1.resize((w // 3, h // 3)), (0, 0))
            cmb.paste(im2.resize((w // 3, h // 3)), (0, h // 3))
            cmb.save(os.path.join(OUT, "prod_compare_%s.png" % name))
    return rows


def main():
    ok = True
    rows = run(1920, 1080, "1080p", True)
    for name, iou, delta, _, _ in rows:
        if iou < 0.85:
            print("FAIL IoU %s=%.4f < 0.85" % (name, iou))
            ok = False
        if max(abs(d) for d in delta) > 2:
            print("FAIL bbox %s delta=%s" % (name, delta))
            ok = False

    # 540p：位置按比例缩放，IoU 同样要达标
    rows2 = run(960, 540, "540p", False)
    for name, iou, delta, _, _ in rows2:
        if iou < 0.85:
            print("FAIL 540p IoU %s=%.4f" % (name, iou))
            ok = False

    # 相对位置一致：1080p 的包围盒按 0.5 缩放后，与 540p 的包围盒差 <= 2px
    d1080 = {r[0]: r[2] for r in rows}
    d540 = {r[0]: r[2] for r in rows2}
    for name in d1080:
        scaled = [v * 0.5 for v in d1080[name]]
        diff = [abs(scaled[i] - d540[name][i]) for i in range(4)]
        if max(diff) > 2:
            print("FAIL 相对位置 %s: 1080p*0.5=%s 540p=%s 差=%s"
                  % (name, [round(v, 1) for v in scaled], d540[name], diff))
            ok = False
        else:
            print("相对位置 %-14s 一致（差 %s）"
                  % (name, [round(v, 1) for v in diff]))

    print("RESULT:", "ALL PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
