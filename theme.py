# -*- coding: utf-8 -*-
"""theme —— 深色/浅色主题、主色与对撞色、以及整套 QSS。

只做纯 Python 的颜色计算和样式表拼装，不 import Qt：
窗口那边把 palette() 的结果丢给 build_qss()，再把同一份调色板交给
时间线（它自己画色带，不吃 QSS）。

配色规则（用户拍板）：
  主色 accent —— 按钮、选中、焦点这类"操作"颜色；
  对撞色 accent2 = accent 在色环上转 180° 的互补色 —— 进度条、播放头、
      时间线高亮这类"点缀"颜色；
  其余界面保持中性灰，只往主色偏一点点，避免死灰。
"""
from __future__ import annotations

import os
import sys

THEMES = ("auto", "light", "dark")
THEME_LABELS = {"auto": "跟随系统", "light": "浅色", "dark": "深色"}
DEFAULT_ACCENT = "#3b82f6"

# 预设主色：直接用各家视频站的品牌色，选完对撞色照旧按色环 180° 自动算
PRESETS = (
    ("哔哩哔哩", "#fb7299"),
    ("YouTube", "#ff0000"),
    ("抖音", "#fe2c55"),
    ("快手", "#ff4906"),
    ("小红书", "#ff2442"),
    ("微博", "#e6162d"),
    ("腾讯视频", "#00a4ff"),
    ("爱奇艺", "#00be06"),
    ("优酷", "#1bb1f4"),
    ("Netflix", "#e50914"),
    ("Twitch", "#9146ff"),
    ("Vimeo", "#1ab7ea"),
)


# ---------------------------------------------------------------- 颜色小工具

def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return (59, 130, 246)
    try:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except ValueError:
        return (59, 130, 246)


def rgb_to_hex(rgb) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return "#%02x%02x%02x" % (r, g, b)


def mix(a: str, b: str, t: float) -> str:
    """a 与 b 按 t（0=a，1=b）混色。"""
    ca, cb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ca[i] + (cb[i] - ca[i]) * t for i in range(3)])


def luminance(value: str) -> float:
    r, g, b = hex_to_rgb(value)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def contrast(a: str, b: str) -> float:
    """WCAG 对比度（1..21）。文字可读性至少要 4.5。"""
    def rel(v: str) -> float:
        out = []
        for c in hex_to_rgb(v):
            s = c / 255.0
            out.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]

    la, lb = rel(a), rel(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def contrast_text(bg: str) -> str:
    """压在 bg 上的文字颜色（深底白字 / 浅底黑字）。"""
    return "#101216" if luminance(bg) > 0.62 else "#ffffff"


def _to_hsv(value: str) -> tuple[float, float, float]:
    r, g, b = (c / 255.0 for c in hex_to_rgb(value))
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d == 0:
        h = 0.0
    elif mx == r:
        h = (60 * ((g - b) / d)) % 360
    elif mx == g:
        h = 60 * ((b - r) / d) + 120
    else:
        h = 60 * ((r - g) / d) + 240
    s = 0.0 if mx == 0 else d / mx
    return h, s, mx


def _from_hsv(h: float, s: float, v: float) -> str:
    c = v * s
    x = c * (1 - abs((h / 60.0) % 2 - 1))
    m = v - c
    seg = int(h // 60) % 6
    r, g, b = [(c, x, 0), (x, c, 0), (0, c, x),
               (0, x, c), (x, 0, c), (c, 0, x)][seg]
    return rgb_to_hex([(r + m) * 255, (g + m) * 255, (b + m) * 255])


def complementary(value: str) -> str:
    """色环上转 180° 的对撞色，保留明度、稍微压一点饱和，免得刺眼。"""
    h, s, v = _to_hsv(value)
    return _from_hsv((h + 180) % 360, min(1.0, s * 0.92), max(0.12, v))


def readable_accent(value: str, dark_bg: bool) -> str:
    """主色太暗或太亮时往背景方向拉一把，保证在底色上看得见。"""
    h, s, v = _to_hsv(value)
    if dark_bg and v < 0.45:
        v = min(1.0, v + (0.45 - v) + 0.12)
    if (not dark_bg) and v > 0.88:
        v = 0.78
    if s < 0.18:                      # 灰调主色在界面上没有存在感
        s = 0.35
    return _from_hsv(h, s, v)


def _fit_fill(fill: str, fg: str) -> str:
    """按钮底色微调明度，直到 fg 压上去至少 4.5:1（文字看得清）。"""
    if contrast(fg, fill) >= 4.5:
        return fill
    h, s, v = _to_hsv(fill)
    lighten = luminance(fg) < 0.5          # 深字 → 底要更亮
    for _ in range(40):
        v = min(1.0, v + 0.02) if lighten else max(0.0, v - 0.02)
        cand = _from_hsv(h, s, v)
        if contrast(fg, cand) >= 4.5 or v in (0.0, 1.0):
            return cand
    return fill


# ---------------------------------------------------------------- 调色板

def palette(theme: str, accent: str) -> dict:
    """theme: light / dark（auto 由调用方先解析），accent: #rrggbb。"""
    dark = theme == "dark"
    acc = readable_accent(accent, dark)
    acc2 = readable_accent(complementary(acc), dark)

    if dark:
        window = mix("#0b0d10", acc, 0.03)
        panel = mix("#12151a", acc, 0.03)
        raised = mix("#171b22", acc, 0.03)
        field = mix("#0e1116", acc, 0.02)
        border = mix("#343c48", acc, 0.10)
        hover = mix("#1e242d", acc, 0.08)
        pressed = mix("#2a3039", acc, 0.12)
        text = "#f1f4f8"
        subtext = "#a4adbb"
        muted = "#6f7888"
    else:
        window = mix("#f3f5f9", acc, 0.05)
        panel = "#ffffff"
        raised = mix("#f7f8fb", acc, 0.03)
        field = mix("#fbfcfe", acc, 0.03)
        border = mix("#d8dce6", acc, 0.10)
        hover = mix("#eceff5", acc, 0.12)
        pressed = mix("#e0e4ed", acc, 0.16)
        text = "#161a21"
        subtext = "#5c6472"
        muted = "#8a92a1"

    # 按钮一律"白字压深色 / 深字压浅色"，再把底色明度调到 4.5:1
    acc_fg = "#ffffff" if luminance(acc) < 0.55 else "#101216"
    acc = _fit_fill(acc, acc_fg)
    acc2_fg = "#ffffff" if luminance(acc2) < 0.55 else "#101216"
    acc2 = _fit_fill(acc2, acc2_fg)
    return {
        "theme": "dark" if dark else "light",
        "window": window,
        "panel": panel,
        "raised": raised,
        "field": field,
        "border": border,
        "hover": hover,
        "pressed": pressed,
        "text": text,
        "subtext": subtext,
        "muted": muted,
        "accent": acc,
        "accent_fg": acc_fg,
        "accent_hover": mix(acc, "#ffffff" if dark else "#000000", 0.12),
        "accent_press": mix(acc, "#000000" if dark else "#ffffff", 0.16),
        "accent_soft": mix(panel, acc, 0.22 if dark else 0.16),
        "accent2": acc2,
        "accent2_fg": acc2_fg,
        "selection": mix(panel, acc, 0.38 if dark else 0.28),
        "row_alt": mix(panel, acc, 0.06),
        "piece_from": mix(panel, acc, 0.55 if dark else 0.45),
        "piece_to": mix(panel, acc2, 0.55 if dark else 0.45),
        "piece_text": contrast_text(mix(panel, acc, 0.5)),
    }


def timeline_colors(pal: dict) -> dict:
    """时间线自己画，需要一份独立的颜色。"""
    return {
        "theme": pal["theme"],
        "bg": pal["field"],
        "empty": pal["muted"],
        "piece_a": pal["piece_from"],
        "piece_b": pal["piece_to"],
        "piece_text": pal["piece_text"],
        "playhead": pal["accent2"],
        "selected_edge": pal["accent"],
        "border": pal["border"],
        "raised": pal.get("raised", pal["panel"]),
        "audio_gap": mix(pal["panel"], pal["accent2"], 0.55),
    }


# ---------------------------------------------------------------- 样式表

def build_qss(p: dict, assets: str = "") -> str:
    """assets: 放 up.png / down.png / chevron.png 的目录（Qt 画的箭头）。

    QSS 的箭头只能给图片，纯 border 三角在 Windows 下画不出来；调用方
    （GUI）用当前配色生成三张小图后把目录传进来。没有 assets 时退回
    箭头规则不写，控件的其它样式照旧。
    """
    a = assets.replace("\\", "/").rstrip("/") if assets else ""
    up = f"{a}/up.png" if a else ""
    down = f"{a}/down.png" if a else ""
    chev = f"{a}/chevron.png" if a else ""
    arrow_rules = ""
    if a:
        arrow_rules = f"""
QComboBox::down-arrow {{ image: url({chev}); width: 9px; height: 6px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({up}); width: 8px; height: 5px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({down}); width: 8px; height: 5px;
}}
"""
    return arrow_rules + f"""
* {{ outline: none; }}
QWidget {{ background: {p['window']}; color: {p['text']};
           font-size: 12px;
           font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei"; }}
QMainWindow, QDialog {{ background: {p['window']}; }}
QLabel {{ background: transparent; }}
QLabel:disabled {{ color: {p['muted']}; }}
QLabel#sectitle {{ color: {p['subtext']}; font-weight: 600; }}

/* 自己画的窗口边框：一圈细边 + 标题栏 + 三个窗口按钮 */
QMainWindow#frame, QDialog#frame {{
    background: {p['window']};
    border: 1px solid {p['border']};
}}
QWidget#titlebar {{
    background: {p['raised']};
    border-bottom: 1px solid {p['border']};
}}
QLabel#wintitle {{ color: {p['subtext']}; font-weight: 600; }}
QLabel#msgicon {{ color: {p['accent']}; font-size: 16px; font-weight: 700; }}
QPushButton#winbtn {{
    background: transparent; border: none; border-radius: 5px;
    color: {p['subtext']}; padding: 0px; font-size: 12px;
}}
QPushButton#winbtn:hover {{ background: {p['hover']}; color: {p['text']}; }}
QPushButton#winbtn:pressed {{ background: {p['pressed']}; }}
QPushButton#winclose:hover {{ background: #e81123; color: #ffffff; }}
QPushButton#winclose:pressed {{ background: #c50f1f; color: #ffffff; }}
QPushButton[danger="true"] {{
    background: #e5484d; color: #ffffff; border-color: #e5484d;
}}
QPushButton[danger="true"]:hover {{ background: #d93c41; border-color: #d93c41; }}
QFrame#sep {{ background: {p['border']}; border: none;
              min-height: 1px; max-height: 1px; }}
QFrame#vsep {{ background: {p['border']}; border: none;
              min-width: 1px; max-width: 1px; }}
QWidget#timingbar {{
    background: {p['panel']}; border: 1px solid {p['border']};
    border-radius: 8px;
}}
QWidget#panelCard {{
    background: {p['panel']}; border: 1px solid {p['border']};
    border-radius: 10px;
}}
QTabWidget#leftDock::pane {{
    background: {p['panel']}; border: 1px solid {p['border']};
    border-radius: 10px; top: 0px;
}}
QTabWidget#leftDock QTabBar::tab {{
    padding: 9px 16px;
}}
QTabWidget#leftDock QListWidget {{
    border: none; background: transparent; padding: 2px;
}}
QLabel#pill {{
    background: {p['field']}; border: 1px solid {p['border']};
    border-radius: 12px; padding: 3px 11px; color: {p['subtext']};
}}

QTabWidget::pane {{
    border: none; border-top: 1px solid {p['border']};
    background: transparent; top: 0;
}}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {p['subtext']};
    border: none; border-bottom: 2px solid transparent;
    padding: 7px 15px; margin-right: 2px;
}}
QTabBar::tab:hover:!selected {{ color: {p['text']}; background: {p['hover']}; }}
QTabBar::tab:selected {{
    background: transparent; color: {p['text']};
    border-bottom: 2px solid {p['accent']}; font-weight: 600;
}}

QPushButton, QToolButton {{
    background: {p['raised']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 7px;
    padding: 5px 12px;
}}
QToolButton::menu-indicator {{ image: none; width: 0px; }}
QPushButton:hover, QToolButton:hover {{ background: {p['hover']}; border-color: {p['accent']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {p['pressed']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {p['muted']}; border-color: {p['border']}; }}
QPushButton:default, QPushButton[accent="true"] {{
    background: {p['accent']}; color: {p['accent_fg']};
    border: 1px solid {p['accent']}; font-weight: 600;
}}
QPushButton:default:hover, QPushButton[accent="true"]:hover {{
    background: {p['accent_hover']}; border-color: {p['accent_hover']};
}}
QPushButton:default:pressed, QPushButton[accent="true"]:pressed {{
    background: {p['accent_press']};
}}
/* 磁吸 / 切割这类「按下去就是开着」的按钮：亮起来，一眼看出状态 */
QPushButton:checked {{
    background: {p['accent']}; color: {p['accent_fg']};
    border: 1px solid {p['accent']}; font-weight: 600;
}}
QPushButton:checked:hover {{
    background: {p['accent_hover']}; border-color: {p['accent_hover']};
}}
QPushButton:checked:pressed {{ background: {p['accent_press']}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p['field']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 7px;
    padding: 5px 9px; selection-background-color: {p['accent']};
    selection-color: {p['accent_fg']};
}}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QPlainTextEdit, QTextEdit {{
    background: {p['field']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 7px;
    padding: 6px; selection-background-color: {p['accent']};
    selection-color: {p['accent_fg']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {p['accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled {{ color: {p['muted']}; background: {p['window']}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 24px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; height: 13px; border: 0;
    border-left: 1px solid {p['border']};
    border-top-right-radius: 5px; background: {p['panel']};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; height: 13px; border: 0;
    border-left: 1px solid {p['border']};
    border-bottom-right-radius: 5px; background: {p['panel']};
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p['hover']};
}}

QComboBox QAbstractItemView {{
    background: {p['panel']}; color: {p['text']};
    border: 1px solid {p['border']};
    selection-background-color: {p['accent']};
    selection-color: {p['accent_fg']};
}}

QListWidget {{
    background: {p['panel']}; border: 1px solid {p['border']};
    border-radius: 9px; padding: 3px;
}}
QListWidget::item {{ color: {p['text']}; border-radius: 5px; margin: 2px 1px; }}
QListWidget::item:hover {{ background: {p['hover']}; }}
QListWidget::item:selected {{
    background: {p['accent_soft']}; color: {p['text']};
    border: none; border-left: 3px solid {p['accent']};
}}

QRadioButton, QCheckBox {{ background: transparent; color: {p['text']}; spacing: 7px; }}
QRadioButton:disabled, QCheckBox:disabled {{ color: {p['muted']}; }}

QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {p['border']}; background: {p['field']};
}}
QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
QCheckBox::indicator:checked {{
    background: {p['accent']}; border-color: {p['accent']};
}}
QCheckBox::indicator:disabled {{ background: {p['window']}; }}

QRadioButton::indicator {{
    width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid {p['border']}; background: {p['field']};
}}
QRadioButton::indicator:hover {{ border-color: {p['accent']}; }}
QRadioButton::indicator:checked {{
    /* 外框仍是 14+2 = 16px：勾上时字数不会整体右移 */
    width: 6px; height: 6px; border-radius: 8px;
    background: {p['panel']};
    border: 5px solid {p['accent']};
}}

QProgressBar {{
    background: {p['field']}; border: 1px solid {p['border']};
    border-radius: 4px; text-align: center; color: {p['subtext']};
    height: 8px;
}}
QProgressBar::chunk {{ background: {p['accent2']}; border-radius: 3px; }}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{
    height: 4px; background: {p['border']}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {p['accent']}; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0;
    border-radius: 6px; background: {p['accent']};
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}
QSlider::handle:horizontal:disabled {{ background: {p['muted']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {p['border']}; }}

QMenu {{
    background: {p['raised']}; color: {p['text']};
    border: 1px solid {p['border']}; border-radius: 8px; padding: 5px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p['accent']}; color: {p['accent_fg']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 6px; }}

QSplitter::handle {{ background: {p['window']}; }}
QSplitter::handle:hover {{ background: {p['accent_soft']}; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 1px; }}
QScrollBar::handle:vertical {{
    background: {p['border']}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['accent']}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 1px; }}
QScrollBar::handle:horizontal {{
    background: {p['border']}; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: {p['panel']}; color: {p['text']};
    border: 1px solid {p['accent']}; padding: 4px 6px;
}}
QDialogButtonBox QPushButton {{ min-width: 72px; }}
"""


# ---------------------------------------------------------------- 系统主题

def system_theme() -> str:
    """Windows 的「应用模式」是浅色还是深色；读不到就当浅色。"""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if int(val) else "dark"
        except Exception:
            return "light"
    if sys.platform == "darwin":
        try:
            import subprocess
            out = subprocess.run(["defaults", "read", "-g",
                                  "AppleInterfaceStyle"],
                                 capture_output=True, text=True).stdout
            return "dark" if "Dark" in out else "light"
        except Exception:
            return "light"
    return "light"


def resolve(theme: str) -> str:
    """auto → 当前系统主题。"""
    if theme in ("light", "dark"):
        return theme
    return system_theme()


def settings_stub() -> dict:
    """测试用：不读系统，直接给个稳定结果。"""
    return {"theme": "dark", "accent": DEFAULT_ACCENT}


if __name__ == "__main__":  # 手动看一眼两套配色
    for th in ("light", "dark"):
        pal = palette(th, DEFAULT_ACCENT)
        print(th, "accent", pal["accent"], "accent2", pal["accent2"],
              "window", pal["window"], "text", pal["text"])
