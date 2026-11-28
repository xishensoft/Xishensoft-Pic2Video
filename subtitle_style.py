# -*- coding: utf-8 -*-
"""字幕样式：15 个键的样式字典，以及「同一套样式 → ASS / 预览」的换算。

这里只有纯函数，不 import Qt、也不 import pic2video：预览（QPainter）和烧录
（libass）各取所需，两边算出来的位置/字号/描边宽度必须一致，所以换算规则只
写在这一处（对应 docs/字幕改造方案.md §4）。

几条实测常数（docs/research/subtitle-style-parity/）：
  · libass 的 Style Fontsize 是字形 ascent+descent 的高度，不是 em 框；
    同一个 em 像素，libass 要比 Qt setPixelSize 放大 line_factor 倍才一样大
    （微软雅黑 ≈ 1.32，常见字体 1.2~1.4），所以 Fontsize 一律乘 factor。
  · 九宫格编号是 keypad 序（1-3 贴底、4-6 居中、7-9 贴顶），和 CSS 的行序
    反过来，见 anchor_px()。
  · 底色框只有 BorderStyle=4 才画（3 在 libass 里画的是描边色），框 = 文本
    宽 + 2×Outline、行数 × 行高 + 2×Outline，没有额外内边距。
  · 描边：libass 的 Outline 是外扩，Qt 的 QPen 是压在中轴上的，所以 QPen 宽
    = 2 × Outline（paint 侧用，见 §5）。
"""

STYLE_KEYS = ("font", "size", "bold", "italic",
              "primary", "outline_color", "outline",
              "shadow_color", "shadow",
              "back_color", "back_opacity",
              "align", "margin_l", "margin_r", "margin_v")

# 15 个键：字体、字号、粗体、斜体、主色、描边色、描边宽、阴影色、阴影偏移、
# 底色、底色不透明度、九宫格对齐、左右下三个边距（画面百分比）
DEFAULT_STYLE_NAME = "Default"

DEFAULT_SUBTITLE_STYLE = {
    "font": "Microsoft YaHei",
    "size": 48,               # 1080 高下的 em 像素，其它分辨率按 h/1080 缩放
    "bold": False,
    "italic": False,
    "primary": "#FFFFFF",
    "outline_color": "#000000",
    "outline": 2.5,           # 像素（1080 基准），0 = 不描边
    "shadow_color": "#000000",
    "shadow": 0.0,            # 偏移像素（右下方向），0 = 不投影
    "back_color": "#000000",
    "back_opacity": 0,        # 0 = 不加底色框，>0 才画
    "align": 2,               # 九宫格 1..9，同 ASS \\an
    "margin_l": 3.1,          # 画面宽度的百分比（3.1% ≈ 1920 下的 60px）
    "margin_r": 3.1,
    "margin_v": 3.7,          # 画面高度的百分比（3.7% ≈ 1080 下的 40px）
}

# 每个键的取值范围；超出就夹住，类型不对就用默认值
LIMITS = {
    "size": (16, 160),
    "outline": (0.0, 20.0),
    "shadow": (0.0, 20.0),
    "back_opacity": (0, 100),
    "align": (1, 9),
    "margin_l": (0.0, 45.0),
    "margin_r": (0.0, 45.0),
    "margin_v": (0.0, 45.0),
}

# ASS Style 行的 23 个字段（v4+），format_ass() 按这个顺序写
ASS_STYLE_FORMAT = ("Name", "Fontname", "Fontsize", "PrimaryColour",
                    "SecondaryColour", "OutlineColour", "BackColour", "Bold",
                    "Italic", "Underline", "StrikeOut", "ScaleX", "ScaleY",
                    "Spacing", "Angle", "BorderStyle", "Outline", "Shadow",
                    "Alignment", "MarginL", "MarginR", "MarginV", "Encoding")


def _clamp(value, low, high):
    return max(low, min(high, value))


def is_colour(value) -> bool:
    """#RRGGBB 或 #RGB，别的都不认。"""
    if not isinstance(value, str):
        return False
    s = value.strip()
    if not s.startswith("#"):
        return False
    body = s[1:]
    return len(body) in (3, 6) and all(c in "0123456789abcdefABCDEF" for c in body)


def norm_colour(value, fallback="#FFFFFF") -> str:
    """统一成 #RRGGBB 大写。"""
    if not is_colour(value):
        return fallback
    body = value.strip()[1:].upper()
    if len(body) == 3:
        body = "".join(c * 2 for c in body)
    return "#" + body


def clean_style_name(name) -> str:
    """样式名写进 ASS 的 Style 行，逗号和换行会毁掉整行，一律换掉。"""
    text = str(name or "").strip()
    text = text.replace(",", " ").replace("\\n", " ").replace("\\r", " ")
    return text or DEFAULT_STYLE_NAME


def clean_style(raw, base=None) -> dict:
    """把一份（可能残缺/越界的）样式补成完整字典；base 决定缺省值。"""
    out = dict(base or DEFAULT_SUBTITLE_STYLE)
    if not isinstance(raw, dict):
        return out
    for key in STYLE_KEYS:
        if key not in raw:
            continue
        val = raw[key]
        try:
            if key == "font":
                fam = val.strip() if isinstance(val, str) else ""
                if fam:
                    out["font"] = fam
            elif key in ("bold", "italic"):
                out[key] = bool(val)
            elif key in ("primary", "outline_color", "shadow_color", "back_color"):
                out[key] = norm_colour(val, out[key])
            elif key in LIMITS:
                low, high = LIMITS[key]
                num = _clamp(float(val), low, high)
                if key in ("size", "align", "back_opacity"):
                    out[key] = int(round(num))
                else:
                    out[key] = round(num, 2)
        except (TypeError, ValueError):
            continue
    return out


def clean_overrides(raw) -> dict:
    """一条字幕的覆盖项：只留认识的键，值照 clean_style 的规则夹一遍。"""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in clean_style(raw).items()
            if k in raw and k in STYLE_KEYS}


def merge_style(base, overrides) -> dict:
    """基准样式 + 覆盖项 → 完整样式。"""
    style = clean_style(base)
    for key, val in (overrides or {}).items():
        if key in STYLE_KEYS:
            style = clean_style({key: val}, style)
    return style


def seed_default(font="", size=0, extra=None) -> dict:
    """用设置里的字体/字号补一份 Default 样式（v2 工程迁移时用）。"""
    style = dict(DEFAULT_SUBTITLE_STYLE)
    if isinstance(font, str) and font.strip():
        style["font"] = font.strip()
    try:
        size = int(size)
    except (TypeError, ValueError):
        size = 0
    if size > 0:
        style["size"] = int(_clamp(size, *LIMITS["size"]))
    return clean_style(extra, style)


def effective_style(cue, styles=None, default_name=DEFAULT_STYLE_NAME) -> dict:
    """某条字幕最终生效的样式：基准样式 + cue 自己的覆盖。"""
    styles = styles or {}
    name = str((cue or {}).get("style") or "") if isinstance(cue, dict) else ""
    base = styles.get(name) if name else None
    if base is None:
        base = styles.get(default_name) or DEFAULT_SUBTITLE_STYLE
    return merge_style(base, (cue or {}).get("overrides") if isinstance(cue, dict) else None)


def em_px(style, height=1080) -> int:
    """样式里的字号（1080 基准的 em 像素）换算到成片高度。"""
    size = float(style.get("size", DEFAULT_SUBTITLE_STYLE["size"]))
    return max(8, int(round(size * float(height) / 1080.0)))


def ass_fontsize(style, height=1080, factor=1.0) -> int:
    """Style 行要写的 Fontsize：em 像素 × line_factor（libass 按字形高度缩放）。"""
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        factor = 1.0
    if factor <= 0:
        factor = 1.0
    return max(1, int(round(em_px(style, height) * factor)))


def ass_colour(colour, alpha=0) -> str:
    """#RRGGBB → ASS 的 &HAABBGGRR（AA = 00 不透明 / FF 全透明）。"""
    hexed = norm_colour(colour)[1:]
    aa = int(_clamp(int(alpha), 0, 255))
    return "&H%02X%s%s%s" % (aa, hexed[4:6], hexed[2:4], hexed[0:2])


def back_alpha(opacity) -> int:
    """底色不透明度 0..100 → ASS 的 AA（0 不透明、255 全透明）。"""
    try:
        pct = float(opacity)
    except (TypeError, ValueError):
        pct = 0.0
    pct = _clamp(pct, 0.0, 100.0)
    return int(round(255.0 * (1.0 - pct / 100.0)))


def anchor_px(style, width=1920, height=1080):
    """九宫格 + 百分比边距 → (align, x, y)。

    ASS \\an 是 keypad 序：1-3 贴底、4-6 居中、7-9 贴顶，和 CSS 从上往下数
    相反；x 是左/中/右，y 是三条基线。写反了字幕会跑到画面另一头。
    """
    try:
        align = int(style.get("align", 2))
    except (TypeError, ValueError):
        align = 2
    align = int(_clamp(align, 1, 9))
    col = (align - 1) % 3              # 0 左 / 1 中 / 2 右
    row = 2 - (align - 1) // 3         # 0 上 / 1 中 / 2 下
    ml = float(style.get("margin_l", 0)) / 100.0 * float(width)
    mr = float(style.get("margin_r", 0)) / 100.0 * float(width)
    mv = float(style.get("margin_v", 0)) / 100.0 * float(height)
    if col == 0:
        x = ml
    elif col == 2:
        x = float(width) - mr
    else:
        x = float(width) / 2.0
    if row == 0:
        y = mv
    elif row == 2:
        y = float(height) - mv
    else:
        y = float(height) / 2.0
    return align, int(round(x)), int(round(y))


def escape_text(text) -> str:
    """正文转义：{ } 会被 libass 当成 override 定界符，统一加反斜杠。"""
    return (str(text or "").replace("{", "\\{").replace("}", "\\}"))


def style_line(name, style, height=1080, factor=1.0, box=None) -> str:
    """一条 "Style: …"（23 个字段）。box=None 时按 back_opacity 决定底色框。"""
    style = clean_style(style)
    opacity = style["back_opacity"]
    if box is None:
        box = opacity > 0
    border_style = 4 if box else 1
    primary = ass_colour(style["primary"], 0)
    secondary = ass_colour(style["outline_color"], 0)
    outline_c = ass_colour(style["outline_color"], 0)
    back = ass_colour(style["back_color"], back_alpha(opacity))
    field = [
        clean_style_name(name),
        style["font"],
        str(ass_fontsize(style, height, factor)),
        primary,
        secondary,
        outline_c,
        back,
        "-1" if style["bold"] else "0",
        "-1" if style["italic"] else "0",
        "0", "0", "100", "100", "0", "0",
        str(border_style),
        "%.1f" % float(style["outline"]),
        "%.1f" % float(style["shadow"]),
        str(int(style["align"])),
        str(int(round(float(style["margin_l"]) / 100.0 * 1920))),
        str(int(round(float(style["margin_r"]) / 100.0 * 1920))),
        str(int(round(float(style["margin_v"]) / 100.0 * 1080))),
        "1",
    ]
    return "Style: " + ",".join(field)


def ass_style_line(name, style, height=1080, factor=1.0, box=None) -> str:
    """style_line 的同义名：方案文档里就叫 ass_style_line。"""
    return style_line(name, style, height, factor, box)


def dialogue_prefix(style, base, width=1920, height=1080, factor=1.0) -> str:
    """一条 Dialogue 的 override 前缀：位置永远写，别的键只写和基准不一样的。"""
    style = clean_style(style)
    base = clean_style(base)
    align, x, y = anchor_px(style, width, height)
    bits = ["\\an%d" % align, "\\pos(%d,%d)" % (x, y)]
    # 注意 \4c 在 ASS 里只有一份：BorderStyle=1 时是阴影色，=4 时是底色框。
    # 阴影色和底色框色同时被覆盖时后写的赢，这是 libass 的限制，不是笔误。
    if style["font"] != base["font"]:
        bits.append("\\fn%s" % style["font"])
    fs = ass_fontsize(style, height, factor)
    if fs != ass_fontsize(base, height, factor):
        bits.append("\\fs%d" % fs)
    if style["primary"] != base["primary"]:
        bits.append("\\1c%s" % ass_colour(style["primary"], 0))
    if style["outline_color"] != base["outline_color"]:
        bits.append("\\3c%s" % ass_colour(style["outline_color"], 0))
    if style["outline"] != base["outline"]:
        bits.append("\\bord%.1f" % float(style["outline"]))
    if style["shadow"] != base["shadow"]:
        bits.append("\\shad%.1f" % float(style["shadow"]))
    if style["shadow_color"] != base["shadow_color"]:
        bits.append("\\4c%s" % ass_colour(style["shadow_color"], 0))
    if style["bold"] != base["bold"]:
        bits.append("\\b%d" % (1 if style["bold"] else 0))
    if style["italic"] != base["italic"]:
        bits.append("\\i%d" % (1 if style["italic"] else 0))
    if style["back_opacity"] != base["back_opacity"]:
        bits.append("\\4a&H%02X&" % back_alpha(style["back_opacity"]))
    if style["back_color"] != base["back_color"] and style["back_opacity"] > 0:
        bits.append("\\4c%s" % ass_colour(style["back_color"], back_alpha(style["back_opacity"])))
    return "{" + "".join(bits) + "}"


