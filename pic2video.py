# -*- coding: utf-8 -*-
"""pic2video —— 图片 + 站内音乐 → 视频（单文件小工具）

把若干张图片按顺序拼成一条 1920x1080 / 30fps 的视频，背景音乐直接从
bilibili 链接整条取下来（不分离人声）。

用法：
  双击「启动.bat」打开窗口：拖图 → 粘 B 站链接 → 预览 → 导出。
  命令行（脚本/测试用）：
    python pic2video.py --export 输出.mp4 --audio 本地音频.m4a 图1 图2 ...
    python pic2video.py --export 输出.mp4 --bili BV1xx411c7mD 图1 图2 ... --total 12
    python pic2video.py --export 输出.mp4 --audio "抖音链接" 图1 图2 ...

只依赖：ffmpeg/ffprobe 在 PATH、PyQt6（仅窗口模式需要）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import components
import theme
import tts_engine
from audio_source import (AUDIO_EXTS, SourceError, fetch_audio, install_ytdlp,
                          kind_of, remove_ytdlp, ytdlp_ready, ytdlp_version)
from i18n import (LOCALES, LOCALE_LABELS, SYSTEM, bili_message,
                  current_locale, language_label, set_locale, tr)

components.prepend_path()      # tools/ 里的 ffmpeg 优先于系统那份

# exe 是窗口程序，没有控制台：起子进程时别让它继承无效的句柄
CREATION_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

APP_VERSION = "1.2.0"

WIDTH, HEIGHT, FPS = 1920, 1080, 30
PREVIEW_W, PREVIEW_H = 960, 540
MIN_DUR = 0.2
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def is_image_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMG_EXTS


def is_video_path(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in MEDIA_EXTS
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v"}
FADE_IN, FADE_OUT = 0.5, 1.5
# 画面是 limited range（16–235）。不写清范围/色度，播放器只能猜：猜成 full 就会发灰、发暗。
# setparams 把标记写进帧属性，x264 才会把 VUI（color_range/colour_primaries…）写进 mp4。
COLOR_TAG = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"

FFMPEG = os.environ.get("PIC2VIDEO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("PIC2VIDEO_FFPROBE", "ffprobe")

_FFMPEG_HELP: str | None = None


def ffmpeg_help() -> str:
    """ffmpeg -h full 的文本（只取一次，失败就当空）。"""
    global _FFMPEG_HELP
    if _FFMPEG_HELP is None:
        try:
            proc = subprocess.run(
                [FFMPEG, "-hide_banner", "-h", "full"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=30,
                creationflags=CREATION_NO_WINDOW)
            _FFMPEG_HELP = proc.stdout.decode("utf-8", "replace")
        except (OSError, subprocess.SubprocessError):
            _FFMPEG_HELP = ""
    return _FFMPEG_HELP


def graph_file_option() -> str:
    """把滤镜图写进临时文件时用的参数名。

    ffmpeg 8 起 -filter_complex_script 被删了，改成通用的「值从文件读」写法
    -/filter_complex；老版本只认前者。-h full 里还会列出老名字就说明是老版本。
    """
    return ("-filter_complex_script"
            if "-filter_complex_script" in ffmpeg_help()
            else "-/filter_complex")


# ---------------------------------------------------------------- 基础工具

class RenderError(RuntimeError):
    pass


def _unlink_quiet(path: str) -> None:
    """删临时文件，删不掉就算了（Windows 上偶尔还被别人握着）。"""
    try:
        os.remove(path)
    except OSError:
        pass


def asset_path(name: str) -> str:
    """找随包的资源（图标之类）：exe 旁边 → 打包解包目录 → 源码目录。"""
    bases = [components.app_dir(),
             getattr(sys, "_MEIPASS", components.app_dir()),
             HERE]
    for base in bases:
        cand = os.path.join(base, "assets", name)
        if os.path.isfile(cand):
            return cand
    return ""


def missing_tools() -> list[str]:
    """ffmpeg / ffprobe 缺哪个（空列表＝都找得到；tools/ 里下过就算有）。"""
    miss = []
    for name, exe in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        found = os.path.isfile(exe) or shutil.which(exe)
        if not found:
            found = components.exe(name)
        if not found:
            miss.append(name)
    return miss


def expand_paths(paths) -> list:
    """拖进来的路径展开成文件列表：目录按名字排序取里面的文件，文件原样留着。"""
    out = []
    for p in paths:
        if os.path.isdir(p):
            try:
                names = sorted(os.listdir(p))
            except OSError:
                continue
            out += [q for q in (os.path.join(p, n) for n in names)
                    if not os.path.isdir(q)]      # 只取一层，子文件夹不递归
        else:
            out.append(p)
    return out


_IMG_OK: dict = {}


def image_ok(path: str) -> bool:
    """这张图 ffprobe 能不能读（按 路径+mtime+大小 缓存，重渲预览不重复花时间）。"""
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = (path, st.st_mtime, st.st_size)
    if key in _IMG_OK:
        return _IMG_OK[key]
    if missing_tools():
        _IMG_OK[key] = True          # 没 ffprobe 就别拦，交给 ffmpeg 自己报
        return True
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=20,
                           stdin=subprocess.DEVNULL,
                           creationflags=CREATION_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    ok = r.returncode == 0 and bool(r.stdout.strip()) and "N/A" not in r.stdout
    _IMG_OK[key] = ok
    return ok


def probe_duration(path: str) -> float:
    """返回媒体时长（秒），失败返回 0。"""
    try:
        p = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=CREATION_NO_WINDOW)
        return float((p.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def extract_audio(src: str, dst: str) -> str:
    """从本地音视频里抽一条 AAC 音轨（拖本地视频当 BGM 用）。"""
    folder = os.path.dirname(os.path.abspath(dst)) or "."
    os.makedirs(folder, exist_ok=True)
    p = subprocess.run(
        [FFMPEG, "-y", "-nostdin", "-v", "error", "-i", src, "-vn",
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", dst],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, creationflags=CREATION_NO_WINDOW)
    if p.returncode != 0 or not os.path.exists(dst):
        raise RenderError(tr("从本地文件抽音轨失败：{msg}",
                             msg=(p.stderr or "")[-300:]))
    return dst


def human_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


def unique_path(directory: str, stem: str, ext: str = ".mp4") -> str:
    cand = os.path.join(directory, stem + ext)
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(directory, f"{stem}_{n}{ext}")
        n += 1
    return cand

# ---------------------------------------------------------------- 设置

SETTINGS_PATH = components.settings_path()   # 打包后跟着 exe 走，目录不可写就放 LOCALAPPDATA

# ---------------------------------------------------------------- 工程文件
# .p2v 是带版本号的 JSON。改格式就 +1，并在这里写往上迁移的分支。
PROJECT_VERSION = 2
PROJECT_SUFFIX = ".p2v"
PROJECT_FILTER = "Pic2Video 工程 (*.p2v)"


class ProjectFileError(Exception):
    """工程文件读写出错。

    reason 给程序分支用：
      missing      文件不在了
      bad-json     不是 JSON（或编码坏了）
      bad-shape    是 JSON 但不像本软件的工程
      newer        更新版本写的，读不了
      write-failed 写不进去（磁盘满 / 只读 / 没权限）
    """

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason

FIT_MODES = ("own", "color", "image", "video", "cover")
FIT_LABELS = {"own": "原图铺底", "color": "纯色留白",
              "image": "自定义背景图", "video": "自定义背景视频",
              "cover": "裁切铺满"}
FIT_BY_LABEL = {v: k for k, v in FIT_LABELS.items()}
FIT_LEGACY = {"blur": "own", "black": "color"}  # 老 settings.json / 老命令行的写法
BG_MODES = ("image", "video")
RATIOS = ("16:9", "9:16", "1:1", "4:5", "4:3", "21:9", "3:4", "2:3", "自定义")
RATIO_VALUE = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:5": 0.8,
               "4:3": 4 / 3, "21:9": 21 / 9, "3:4": 3 / 4, "2:3": 2 / 3}
SHORT_SIDE = 1080  # 选比例时按短边 1080 给出预设尺寸
QUALITY = {"高": 18, "标准": 20, "小体积": 23}
MODE_LABELS = {"bgm": "跟音乐", "material": "跟素材总长",
               "total": "自设总长", "free": "逐段秒数",
               "voice": "跟旁白"}
MODE_BY_LABEL = {v: k for k, v in MODE_LABELS.items()}
# 交叉淡化能用的转场：全是 ffmpeg xfade 的内置名，不吃外部文件
XFADE_NAMES = ("fade", "fadeblack", "wipeleft", "wiperight", "wipeup",
               "wipedown", "slideleft", "slideright", "circleopen",
               "circleclose", "radial", "pixelize")
XFADE_LABELS = {
    "fade": "交叉淡化", "fadeblack": "黑场过渡", "wipeleft": "向左擦除",
    "wiperight": "向右擦除", "wipeup": "向上擦除", "wipedown": "向下擦除",
    "slideleft": "向左推移", "slideright": "向右推移",
    "circleopen": "圆形展开", "circleclose": "圆形收拢",
    "radial": "径向旋转", "pixelize": "马赛克",
}
XFADE_MIN, XFADE_MAX = 0.1, 3.0   # 单个转场的秒数范围
MOTIONS = ("none", "in", "out", "inout")          # 画面动效
MOTION_LABELS = {"none": "静止", "in": "缓慢推近",
                 "out": "缓慢拉远", "inout": "交替推拉"}
MOTION_AMOUNT = 0.06                              # 推拉幅度：放大 6%
CROP_FOCUS = {"center": 0.5, "top": 0.0, "bottom": 1.0}   # 裁切铺满时留住哪一段
CROP_LABELS = {"center": "居中", "top": "偏上", "bottom": "偏下"}
WIN_MODES = ("remember", "max", "fixed")             # 启动时窗口
WIN_LABELS = {"remember": "记住上次", "max": "启动时最大化",
              "fixed": "固定大小"}
# 快捷键：默认值照剪辑软件的惯例（Ctrl+I 导入、Ctrl+M 导出、Space 播放…）
SHORTCUT_DEFAULTS = {
    "add": "Ctrl+I",
    "export": "Ctrl+M",
    "undo": "Ctrl+Z",
    "redo": "Ctrl+Shift+Z",
    "play": "Space",
    "remove": "Delete",
    "preview": "F5",
}
SHORTCUT_ORDER = ("add", "export", "undo", "redo", "play", "remove", "preview")
SHORTCUT_LABELS = {"add": "加素材", "export": "导出视频", "undo": "撤回",
                   "redo": "还原", "play": "播放 / 暂停",
                   "remove": "删除素材", "preview": "刷新预览"}


# 界面语言的显示文案（内部值保持中文，只有显示时翻译）
def fit_label(fit: str) -> str:
    return tr(FIT_LABELS.get(fit, fit))


def mode_label(mode: str) -> str:
    return tr(MODE_LABELS.get(mode, mode))


def even(v: float) -> int:
    """yuv420p 要求宽高为偶数。"""
    return max(2, int(round(float(v) / 2.0)) * 2)


def slider_value_from_x(x: float, groove_x: float, groove_w: float,
                        handle_w: float, minimum: int, maximum: int,
                        upside_down: bool = False) -> int:
    """鼠标在滑轨上的 x 坐标 → 滑块该取的数值。

    进度条要支持「点哪儿跳哪儿」：Qt 的 QSlider 默认只会按 pageStep 翻页，
    pageStep 设成 0 就干脆不动，所以自己按滑轨几何算。
    """
    span = groove_w - handle_w
    if span <= 0 or maximum <= minimum:
        return maximum if maximum > minimum else minimum
    rel = (float(x) - groove_x - handle_w / 2.0) / span
    rel = min(1.0, max(0.0, rel))
    if upside_down:
        rel = 1.0 - rel
    return int(round(minimum + (maximum - minimum) * rel))


def preset_size(ratio: str) -> tuple[int, int] | None:
    """比例 → 短边 1080 的成片尺寸；「自定义」返回 None。"""
    r = RATIO_VALUE.get(ratio)
    if not r:
        return None
    if r >= 1:
        return even(SHORT_SIDE * r), SHORT_SIDE
    return SHORT_SIDE, even(SHORT_SIDE / r)


def is_hex_color(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 7 and value[0] == "#"
            and all(c in "0123456789abcdefABCDEF" for c in value[1:]))


# ---------------------------------------------------------------- 导出预设
# 平台规格表：宽高、帧率、CRF、音频码率、文件名后缀。
# 值是 None 的项表示「沿用设置里的当前值」——「通用」那一项全是 None。
EXPORT_PRESETS = {
    "generic": {"label": "通用（沿用设置）", "w": None, "h": None,
                "fps": None, "crf": None, "ab": None, "suffix": ""},
    "bilibili": {"label": "B 站 1080p60", "w": 1920, "h": 1080,
                 "fps": 60, "crf": 18, "ab": "192k", "suffix": "_bilibili"},
    "douyin": {"label": "抖音 1080×1920", "w": 1080, "h": 1920,
               "fps": 30, "crf": 20, "ab": "128k", "suffix": "_douyin"},
    "channels": {"label": "视频号 1080p30", "w": 1920, "h": 1080,
                 "fps": 30, "crf": 20, "ab": "128k", "suffix": "_channels"},
    "youtube": {"label": "YouTube 1080p30", "w": 1920, "h": 1080,
                "fps": 30, "crf": 20, "ab": "192k", "suffix": "_youtube"},
}
EXPORT_PRESET_ORDER = ("generic", "bilibili", "douyin", "channels", "youtube")
EXPORT_AUDIO_RATES = ("96k", "128k", "160k", "192k", "256k")
EXPORT_FPS = (30, 60)


def export_plan(name: str, base: dict | None = None) -> dict:
    """把平台预设和「通用」那套设置合并成一次导出实际要用的参数。

    base 是通用档的取值（w / h / fps / crf / ab），界面上就是设置里的值；
    不传就照软件默认值。宽高一律取偶数（yuv420p 的要求）。
    """
    base = base or {}
    key = name if name in EXPORT_PRESETS else "generic"
    spec = EXPORT_PRESETS[key]

    def pick(field, default):
        value = spec[field]
        return default if value is None else value

    def num(value, lo, hi, default):
        try:
            return int(min(hi, max(lo, int(round(float(value))))))
        except (TypeError, ValueError):
            return default

    w = even(num(pick("w", base.get("w", WIDTH)), 16, 4096, WIDTH))
    h = even(num(pick("h", base.get("h", HEIGHT)), 16, 4096, HEIGHT))
    fps = 60 if num(pick("fps", base.get("fps", FPS)), 1, 120, FPS) >= 50 else 30
    crf = num(pick("crf", base.get("crf", 20)), 0, 51, 20)
    ab = str(pick("ab", base.get("ab", "192k")) or "192k")
    return {"preset": key, "label": spec["label"], "w": w, "h": h,
            "fps": fps, "crf": crf, "ab": ab, "suffix": spec["suffix"]}


def estimate_kbps(plan: dict) -> int:
    """按经验粗算视频码率（kbps），只在导出框里给个量级，不当准。"""
    try:
        w = int(plan["w"])
        h = int(plan["h"])
        fps = int(plan["fps"])
        crf = int(plan["crf"])
    except (KeyError, TypeError, ValueError):
        return 0
    # 以 1080p30 / CRF 20 ≈ 6 兆比特/秒 为基准；CRF 每降 6 码率大约翻一倍
    bpp = 6.0e6 / (1920.0 * 1080.0 * 30.0)
    factor = 2.0 ** ((20 - crf) / 6.0)
    return max(100, int(round(w * h * fps * bpp * factor / 1000.0 / 100.0)) * 100)


def rescale_counts(counts, from_fps: int, to_fps: int) -> list[int]:
    """按另一个帧率重算每段帧数；工程不动，成片时长不变。

    导出预设会换帧率（B 站 60，其它 30），而时间线帧数是按设置里的帧率
    存的；不换算成片就会变短或变长，所以渲染前先换一遍。
    """
    f = int(from_fps or 30)
    t = int(to_fps or f)
    if t == f:
        return list(counts)
    return [max(1, int(round(nf * t / float(f)))) for nf in counts]


def clamp_cover_time(t, total) -> float:
    """封面时间点夹在 [0, 成片总长]；坏值当 0。"""
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = 0.0
    try:
        total = float(total)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0 or t != t:      # 没时长 / NaN
        return 0.0
    return min(max(0.0, t), total)


def cover_default_time(clips, counts, fps: int, xfade_sec: float = 0.3) -> float:
    """封面默认时间点：第一张图片段在成片时间轴上的正中。

    时间线算法跟 render() 里一致（交叉淡化把边界挤掉一块）。
    """
    if not clips or not counts:
        return 0.0
    fps = float(fps) or 30.0
    counts2, overlaps = _xfade_plan(clips, counts, int(fps), xfade_sec)
    idx = next((i for i, c in enumerate(clips) if c.kind == "image"), 0)
    acc = 0.0
    for i in range(idx):
        acc += counts2[i] / fps
        if i < len(overlaps):
            acc -= overlaps[i] / fps
    seg = counts2[idx] / fps
    if idx < len(overlaps):
        seg -= overlaps[idx] / fps
    return max(0.0, acc + max(0.05, seg) / 2.0)


# 预览质量：只影响样片的清晰度，不影响成片。值是「最长边」像素，0 = 跟成片一样大。
PREVIEW_QUALITY = {"fast": 640, "normal": 960, "sharp": 1280, "full": 0}
PREVIEW_QUALITY_ORDER = ("fast", "normal", "sharp", "full")
PREVIEW_QUALITY_LABELS = {"fast": "流畅", "normal": "标准",
                          "sharp": "清晰", "full": "原尺寸"}

# 预览倍速：只改变播放速度，成片永远按原速导出。
PREVIEW_SPEEDS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

# 界面三档：简洁 = 只做出片（加图 / 配乐 / 导出），标准 = 日常剪辑，
# 专业 = 全部参数。可见性在这里定死，窗口只照着名单执行。
UI_TIERS = ("simple", "normal", "pro")
UI_TIER_LABELS = {"simple": "简洁", "normal": "标准", "pro": "专业"}
UI_TOOLBAR = {
    # 简洁：工具条上只有三件事，工程菜单收进「⋯」
    "simple": ("add", "export", "mode", "more"),
    "normal": ("project", "add", "overlay", "snap", "razor", "undo",
               "redo", "settings", "export", "mode"),
    "pro": ("project", "add", "overlay", "up", "down", "remove", "snap",
            "razor", "undo", "redo", "settings", "rhythm", "total",
            "refresh", "export", "mode"),
}


def ui_tier(value) -> str:
    """把设置里的界面档位洗干净：认不出的值当简洁。"""
    return value if value in UI_TIERS else "simple"


def ui_toolbar(tier) -> tuple:
    """这一档的工具条上留哪些控件（名字见 UI_TOOLBAR）。"""
    return UI_TOOLBAR[ui_tier(tier)]


def ui_has(tier, name: str) -> bool:
    return name in ui_toolbar(tier)


def ui_advanced(tier) -> bool:
    """倍速、预览清晰度、时间线缩放条这些「工具味」控件：标准以上才给。"""
    return ui_tier(tier) != "simple"


TTS_VOICES = tts_engine.TTS_VOICES


class Settings:
    """持久设置：存在脚本旁边的 settings.json，坏文件就退回默认值。

    预览音频不在这里：它永远跟随 Windows 当前默认输出（见 run_gui 里的
    follow_default_audio），系统换设备时自动跟着换。
    """

    DEFAULTS = {
        "mode": "bgm",            # 默认时长模式
        "per_image": 3.0,         # 逐张模式每张默认秒数
        "ratio": "16:9",          # 画面比例（自定义时只当标签）
        "width": 1920,            # 成片宽高（可直接手填）
        "height": 1080,
        "fps": 30,
        "quality": "标准",        # -> crf
        "fit": "own",             # 铺底：own/color/image/video/cover
        "bg_blur": True,          # 背景模糊（原图铺底 / 自定义背景都适用）
        "pad_color": "#000000",   # 纯色留白颜色
        "bg_path": "",            # 自定义背景图片或视频
        "lut_dir": "",            # 调色用的 .cube 放哪个目录（挑 LUT 时列它）
        "fade_in": 0.5,
        "fade_out": 1.5,
        "transition": "none",     # none / fade
        "transition_sec": 0.3,
        "export_dir": "",         # 空 = 图片所在目录
        "last_export_dir": "",
        "recent_projects": [],    # 最近打开的工程（最多 8 条，新的在前）
        "lang": SYSTEM,           # 界面语言：auto 跟随系统 / zh-CN / zh-TW / en / ja / ko
        "ui": "simple",           # 界面档位：simple 简洁 / normal 标准 / pro 专业
        "theme": "auto",          # 外观：auto 跟随系统 / light / dark
        "accent": theme.DEFAULT_ACCENT,   # 主色（对撞色由它自动算）
        "motion": "none",         # 画面动效：none / in / out / inout
        "music_volume": 1.0,      # 背景音乐音量倍数（0..2）
        "music_start": 0.0,       # 背景音乐从第几秒开始用
        "music_len": 0.0,         # 音乐用多长；0 = 一直用到音乐结尾
        "crop_focus": "center",   # 裁切铺满时留中心 / 偏上 / 偏下
        "subtitle_font": "Microsoft YaHei",   # 烧字幕用的字体
        "subtitle_size": 48,      # 字号（按成片像素算）
        # 配音（文稿 -> TTS -> 旁白 + 字幕）
        "tts_provider": "edge",   # edge / gemini / azure / openai
        "tts_voice": "zh-CN-XiaoxiaoNeural",   # 上次用的音色
        "tts_rate": 0,            # 语速（%，-50..100）
        "tts_keys": {},           # 各家的 key：{"gemini": "...", "azure": "..."}
        "tts_azure_region": "eastasia",
        "tts_base_url": "",
        "tts_model": "",          # OpenAI 兼容端点的模型名（空 = tts-1）       # OpenAI 兼容端点（硅基流动/火山/MiniMax 等）
        "tts_proxy": "",          # 合成时走这个代理，空 = 直连
        "tts_gap": 0.6,           # 段与段之间的停顿（秒）
        "tts_cleanup": True,      # 合成完顺手清一下（去咔哒、去噗声）
        "tts_voice_b": "zh-CN-YunjianNeural",   # 对话稿里「听友」的音色
        "tts_prefix": False,      # 字幕带「主讲：」这种前缀
        "tts_script": "",         # 上次的文稿，重开还在
        "preview_quality": "normal",   # 样片清晰度：fast/normal/sharp/full
        "preview_speed": 1.0,          # 上次用的预览倍速（0.5..2.0）
        "open_after_export": True,   # 导出完自动打开所在文件夹
        "ask_export_dir": True,      # 导出时先问保存到哪（关掉就直接用默认目录）
        "export_preset": "generic",  # 上次用的平台预设（导出框默认选它）
        "cover_export": False,       # 导出时顺便截一张封面
        "cover_at": -1.0,            # 封面时间点；-1 = 第一张图片段正中间
        "snap_on": True,             # 时间线磁吸（贴住附近的边）
        "win_w": 0,               # 窗口大小/位置，0 = 用默认
        "win_h": 0,
        "win_x": -1,
        "win_y": -1,
        "win_mode": "remember",   # remember 记住上次 / max 启动最大化 / fixed 固定大小
        "win_fixed_w": 1475,
        "win_fixed_h": 1000,
        "intro_path": "",        # 默认片头视频（加第一张图时自动放到最前）
        "outro_path": "",        # 默认片尾视频
        "shortcuts": dict(SHORTCUT_DEFAULTS),   # 各动作的快捷键，可改
    }

    def __init__(self, path: str = SETTINGS_PATH):
        self.path = path
        self.data = dict(self.DEFAULTS)
        self.load()

    # -- 读 / 写 ------------------------------------------------------
    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for k, v in raw.items():
            if k in self.DEFAULTS:
                self.data[k] = v
        self._sanitize()

    def save(self) -> None:
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass  # 目录只读之类，设置存不下不该拖垮导出

    RECENT_MAX = 8

    # -- 最近打开的工程 ------------------------------------------------
    def recent(self) -> list[str]:
        """最近打开的工程，新的在前；磁盘上已经没有的顺手剔掉。"""
        out: list[str] = []
        for path in self.data.get("recent_projects") or []:
            if (isinstance(path, str) and path and path not in out
                    and os.path.isfile(path)):
                out.append(path)
        return out[:self.RECENT_MAX]

    def push_recent(self, path: str | None) -> None:
        """记一条最近打开：置顶、去重、最多 8 条，马上落盘。"""
        if not isinstance(path, str) or not path:
            return
        path = os.path.abspath(path)
        cur = [p for p in (self.data.get("recent_projects") or [])
               if isinstance(p, str) and p and p != path]
        cur.insert(0, path)
        self.data["recent_projects"] = cur[:self.RECENT_MAX]
        self.save()

    def _sanitize(self) -> None:
        d = self.data
        if d.get("mode") not in MODE_LABELS:
            d["mode"] = "bgm"
        fit = d.get("fit")
        if fit in FIT_LEGACY:
            d["fit"] = FIT_LEGACY[fit]
        elif fit not in FIT_MODES:
            d["fit"] = "own"
        if d.get("ratio") not in RATIOS:
            d["ratio"] = "16:9"
        if not isinstance(d.get("bg_blur"), bool):
            d["bg_blur"] = True
        if not is_hex_color(d.get("pad_color", "")):
            d["pad_color"] = "#000000"
        for key, lo, hi in (("width", 16, 4096), ("height", 16, 4096)):
            try:
                d[key] = int(min(hi, max(lo, even(d.get(key, self.DEFAULTS[key])))))
            except (TypeError, ValueError):
                d[key] = self.DEFAULTS[key]
        if d.get("quality") not in QUALITY:
            d["quality"] = "标准"
        if d.get("transition") not in ("none", "fade"):
            d["transition"] = "none"
        try:
            d["fps"] = 60 if int(d.get("fps", 30)) >= 50 else 30
        except (TypeError, ValueError):
            d["fps"] = 30
        for key, lo, hi in (("music_volume", 0.0, 2.0),
                            ("music_start", 0.0, 36000.0),
                            ("music_len", 0.0, 36000.0),
                            ("per_image", MIN_DUR, 3600.0),
                            ("fade_in", 0.0, 10.0),
                            ("fade_out", 0.0, 30.0),
                            ("transition_sec", 0.0, 5.0)):
            try:
                d[key] = float(min(hi, max(lo, float(d.get(key, self.DEFAULTS[key])))))
            except (TypeError, ValueError):
                d[key] = self.DEFAULTS[key]
        for key in ("export_dir", "last_export_dir", "bg_path",
                    "lut_dir", "intro_path", "outro_path"):
            if not isinstance(d.get(key), str):
                d[key] = ""
        rec = d.get("recent_projects")
        d["recent_projects"] = ([p for p in rec
                                 if isinstance(p, str) and p][:self.RECENT_MAX]
                                if isinstance(rec, list) else [])
        if d.get("win_mode") not in WIN_MODES:
            d["win_mode"] = "remember"
        for key, lo, hi in (("win_fixed_w", 800, 8000),
                            ("win_fixed_h", 560, 8000)):
            try:
                d[key] = int(min(hi, max(lo, int(d.get(key, self.DEFAULTS[key])))))
            except (TypeError, ValueError):
                d[key] = self.DEFAULTS[key]
        if d.get("lang") not in (SYSTEM,) + LOCALES:
            d["lang"] = SYSTEM
        d["ui"] = ui_tier(d.get("ui"))
        if d.get("theme") not in theme.THEMES:
            d["theme"] = "auto"
        if not is_hex_color(d.get("accent", "")):
            d["accent"] = theme.DEFAULT_ACCENT
        if not isinstance(d.get("open_after_export"), bool):
            d["open_after_export"] = True
        if not isinstance(d.get("ask_export_dir"), bool):
            d["ask_export_dir"] = True
        if d.get("export_preset") not in EXPORT_PRESETS:
            d["export_preset"] = "generic"
        if not isinstance(d.get("cover_export"), bool):
            d["cover_export"] = False
        try:
            d["cover_at"] = float(d.get("cover_at", -1.0))
        except (TypeError, ValueError):
            d["cover_at"] = -1.0
        if d["cover_at"] < -1.0:
            d["cover_at"] = -1.0
        if not isinstance(d.get("snap_on"), bool):
            d["snap_on"] = True
        if d.get("motion") not in MOTIONS:
            d["motion"] = "none"
        if d.get("crop_focus") not in CROP_FOCUS:
            d["crop_focus"] = "center"
        font = d.get("subtitle_font")
        d["subtitle_font"] = (font.strip() if isinstance(font, str) else ""
                              ) or "Microsoft YaHei"
        try:
            d["subtitle_size"] = int(min(120, max(16, int(
                d.get("subtitle_size", 48)))))
        except (TypeError, ValueError):
            d["subtitle_size"] = 48
        # 配音：服务名必须认识，key 只收字符串，语速和停顿夹进合理范围
        if d.get("tts_provider") not in tts_engine.TTS_PROVIDERS:
            d["tts_provider"] = "edge"
        if not isinstance(d.get("tts_voice"), str):
            d["tts_voice"] = "zh-CN-XiaoxiaoNeural"
        try:
            d["tts_rate"] = int(min(tts_engine.TTS_MAX_RATE,
                                    max(tts_engine.TTS_MIN_RATE,
                                        int(d.get("tts_rate", 0)))))
        except (TypeError, ValueError):
            d["tts_rate"] = 0
        keys = d.get("tts_keys")
        clean = {}
        if isinstance(keys, dict):
            for k, v in keys.items():
                if k in tts_engine.TTS_PROVIDERS and isinstance(v, str):
                    clean[k] = v.strip()
        d["tts_keys"] = clean
        for key, fallback in (("tts_azure_region", "eastasia"),
                              ("tts_base_url", ""), ("tts_model", ""),
                              ("tts_proxy", ""),
                              ("tts_script", "")):
            v = d.get(key)
            d[key] = v.strip() if isinstance(v, str) else fallback
        if not d.get("tts_azure_region"):
            d["tts_azure_region"] = "eastasia"
        try:
            d["tts_gap"] = float(min(5.0, max(0.0, float(
                d.get("tts_gap", 0.6)))))
        except (TypeError, ValueError):
            d["tts_gap"] = 0.6
        if not isinstance(d.get("tts_cleanup"), bool):
            d["tts_cleanup"] = True
        vb = d.get("tts_voice_b")
        d["tts_voice_b"] = (vb.strip() if isinstance(vb, str) else "") \
            or "zh-CN-YunjianNeural"
        if not isinstance(d.get("tts_prefix"), bool):
            d["tts_prefix"] = False
        if d.get("preview_quality") not in PREVIEW_QUALITY:
            d["preview_quality"] = "normal"
        try:
            speed = float(d.get("preview_speed", 1.0))
        except (TypeError, ValueError):
            speed = 1.0
        d["preview_speed"] = speed if speed in PREVIEW_SPEEDS else 1.0
        sc = d.get("shortcuts")
        clean = {}
        if isinstance(sc, dict):
            for k, v in sc.items():
                if k in SHORTCUT_DEFAULTS and isinstance(v, str) and v.strip():
                    clean[k] = v.strip()
        for k, v in SHORTCUT_DEFAULTS.items():
            clean.setdefault(k, v)
        d["shortcuts"] = clean
        for key, lo, hi in (("win_w", 0, 8000), ("win_h", 0, 8000),
                            ("win_x", -1, 8000), ("win_y", -1, 8000)):
            try:
                d[key] = int(min(hi, max(lo, int(d.get(key, self.DEFAULTS[key])))))
            except (TypeError, ValueError):
                d[key] = self.DEFAULTS[key]

    # -- 访问 ---------------------------------------------------------
    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, **kw):
        self.data.update(kw)
        self._sanitize()

    # -- 派生 ---------------------------------------------------------
    def size(self) -> tuple[int, int]:
        return even(self.data["width"]), even(self.data["height"])

    def preview_size(self, quality: str | None = None) -> tuple[int, int]:
        """样片尺寸：按所选清晰度缩放，最长边不超过那一档；不放大。"""
        w, h = self.size()
        edge = PREVIEW_QUALITY.get(
            quality or self.data.get("preview_quality"), 960)
        if edge <= 0:
            return w, h
        k = min(1.0, float(edge) / max(w, h))
        return even(w * k), even(h * k)

    def crf(self) -> int:
        return QUALITY[self.data["quality"]]


# ---------------------------------------------------------------- 时间线模型

# 调色：每段的亮度 / 对比度 / 饱和度，外加一个 .cube LUT。
# 默认值就是「没调过」：亮 0、对比 1、饱和 1、LUT 空。
COLOR_BRIGHT = (-1.0, 1.0)
COLOR_CONTRAST = (0.0, 2.0)
COLOR_SATURATION = (0.0, 3.0)
COLOR_DEFAULT = (0.0, 1.0, 1.0)


def _clamp_range(value, bounds: tuple[float, float], default: float) -> float:
    """把数字夹进区间；读不出来（None / 非数字）就用默认值。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    if v != v:          # NaN
        return float(default)
    return min(bounds[1], max(bounds[0], v))


def color_filter(brightness: float, contrast: float, saturation: float,
                 lut: str = "") -> str:
    """一段调色滤镜串（eq → lut3d），没调过就返回空串。

    顺序是固定的：先 eq 把亮度/对比度/饱和度摆正，再套 LUT。
    反过来 LUT 会按「原片」的色域发色，套完再拉对比就偏了。
    """
    parts = []
    b = _clamp_range(brightness, COLOR_BRIGHT, COLOR_DEFAULT[0])
    c = _clamp_range(contrast, COLOR_CONTRAST, COLOR_DEFAULT[1])
    s = _clamp_range(saturation, COLOR_SATURATION, COLOR_DEFAULT[2])
    if abs(b) > 1e-6 or abs(c - 1.0) > 1e-6 or abs(s - 1.0) > 1e-6:
        parts.append(f"eq=brightness={b:.3f}:contrast={c:.3f}"
                     f":saturation={s:.3f}")
    if lut:
        parts.append(f"lut3d=file='{_filter_path(lut)}'")
    return "," + ",".join(parts) if parts else ""


def list_luts(folder: str) -> list[str]:
    """滤镜目录里的 .cube 文件（按文件名排）；目录不在就返回空表。"""
    if not folder or not os.path.isdir(folder):
        return []
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".cube")]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names, key=str.lower)]


# ---------------- 资源包：字体 / LUT / 转场预设（zip 装进数据目录）
ASSET_KINDS = ("font", "lut", "transition")
ASSET_KIND_LABELS = {"font": "字体", "lut": "LUT", "transition": "转场预设"}
ASSET_EXTS = {"font": (".ttf", ".otf", ".ttc"),
              "lut": (".cube",),
              "transition": (".json",)}
ASSET_MAX_ENTRIES = 64
ASSET_MAX_BYTES = 64 * 1024 * 1024
ASSET_EXE_EXTS = (".exe", ".dll", ".com", ".bat", ".cmd", ".ps1",
                  ".vbs", ".js", ".msi", ".scr", ".sys", ".pif")


class AssetError(Exception):
    """资源包装不进去：reason 给程序判断，message 给人看。"""

    def __init__(self, reason: str, message: str = ""):
        self.reason = reason
        super().__init__(message or reason)


def persistent_media_dir() -> str:
    """程序自己取回来、工程会引用的素材（从链接取来的 BGM）放这里。

    不能放 self.tmpdir：那个目录关窗就删，工程下次打开会找不到音乐。
    """
    folder = os.path.join(components.data_dir(), "media")
    os.makedirs(folder, exist_ok=True)
    return folder


def assets_root() -> str:
    """资源包装在数据目录的 assets/ 下（跟工程文件无关）。"""
    return os.path.join(components.data_dir(), "assets")


ASSET_DIRS = {"font": "fonts", "lut": "luts", "transition": "transitions"}


def asset_dir(kind: str, root: str = "") -> str:
    """每类资源一个子目录：assets/fonts、assets/luts、assets/transitions。"""
    sub = ASSET_DIRS.get(kind, kind)
    return os.path.join(root or assets_root(), sub)


def _asset_rel(name) -> str:
    """把包内路径洗成相对路径；绝对路径、上跳、空段一律拒绝。"""
    if not isinstance(name, str):
        raise AssetError("unsafe-path", "包里有不认识的路径")
    rel = name.replace("\\", "/").strip()
    if not rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise AssetError("unsafe-path", str(name))
    bad = any(part in ("", ".", "..") for part in rel.split("/"))
    if bad or any(ch in rel for ch in ("\n", "\r", "\0")):
        raise AssetError("unsafe-path", str(name))
    return rel


def _asset_file_ok(rel: str, kind: str) -> bool:
    return os.path.splitext(rel)[1].lower() in ASSET_EXTS.get(kind, ())


def parse_transition_preset(raw) -> dict:
    """一个转场预设：{"name": 显示名, "xfade": 内置名, "duration": 秒}。"""
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = json.loads(bytes(raw).decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError("bad-preset", str(exc))
    if not isinstance(raw, dict):
        raise AssetError("bad-preset", "转场预设不是一个对象")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AssetError("bad-preset", "转场预设缺名字")
    xfade = raw.get("xfade")
    if xfade not in XFADE_NAMES:
        raise AssetError("bad-preset", "转场预设里的 xfade 不认识：" + str(xfade))
    try:
        dur = float(raw.get("duration", 0.5))
    except (TypeError, ValueError):
        raise AssetError("bad-preset", "转场时长不是数字")
    return {"name": name.strip(), "xfade": xfade,
            "duration": min(XFADE_MAX, max(XFADE_MIN, dur))}


def inspect_asset_zip(path: str) -> dict:
    """读 + 校验一个资源包 zip，不动任何文件。返回包内容。"""
    if not os.path.isfile(path):
        raise AssetError("missing", str(path))
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise AssetError("bad-zip", str(exc))
    with zf:
        try:
            infos = [i for i in zf.infolist() if not i.is_dir()]
        except (zipfile.BadZipFile, OSError) as exc:
            raise AssetError("bad-zip", str(exc))
        if len(infos) > ASSET_MAX_ENTRIES:
            raise AssetError("too-many", str(len(infos)))
        if sum(max(0, i.file_size) for i in infos) > ASSET_MAX_BYTES:
            raise AssetError("too-big", "解压后超过 64 MB")
        names = set()
        for info in infos:
            rel = _asset_rel(info.filename)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise AssetError("symlink", rel)
            if mode & 0o111 or rel.lower().endswith(ASSET_EXE_EXTS):
                raise AssetError("executable", rel)
            if rel in names:
                raise AssetError("bad-entry", "包里路径重了：" + rel)
            names.add(rel)
        if "manifest.json" not in names:
            raise AssetError("bad-manifest", "缺 manifest.json")
        try:
            doc = json.loads(zf.read("manifest.json").decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError("bad-manifest", str(exc))
        if not isinstance(doc, dict):
            raise AssetError("bad-manifest", "manifest.json 不是一个对象")
        kind = doc.get("kind")
        if kind not in ASSET_KINDS:
            raise AssetError("bad-manifest", "kind 只能是 font / lut / transition")
        name = doc.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AssetError("bad-manifest", "包里没写名字")
        version = doc.get("version", "")
        if not isinstance(version, str):
            raise AssetError("bad-manifest", "version 得是字符串")
        entries = doc.get("entries")
        if not isinstance(entries, list) or not entries:
            raise AssetError("bad-manifest", "entries 得是非空列表")
        files = {}
        for ent in entries:
            if not isinstance(ent, dict):
                raise AssetError("bad-manifest", "entries 里每项都得是对象")
            rel = _asset_rel(ent.get("path") or "")
            if rel not in names:
                raise AssetError("missing-entry", rel)
            if not _asset_file_ok(rel, kind):
                raise AssetError("bad-entry", rel)
            sha = str(ent.get("sha256") or "").strip().lower()
            try:
                data = zf.read(rel)
            except (KeyError, zipfile.BadZipFile, OSError) as exc:
                raise AssetError("bad-zip", str(exc))
            if hashlib.sha256(data).hexdigest() != sha:
                raise AssetError("hash-mismatch", rel)
            files[rel] = data
        declared = set(files) | {"manifest.json"}
        extra = names - declared
        if extra:
            raise AssetError("bad-manifest",
                             "有文件没写进 manifest：" + sorted(extra)[0])
        if kind == "transition":
            for rel, data in files.items():
                try:
                    parse_transition_preset(data)
                except AssetError as exc:
                    raise AssetError("bad-preset", rel + "：" + str(exc))
        return {"kind": kind, "name": name.strip(),
                "version": version.strip(), "files": files}


def asset_package_folder(kind: str, name: str) -> str:
    """包目录名：由 kind + 名字定，同名包始终落在同一个目录。"""
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "_"
                   for ch in name.strip())[:40] or "package"
    digest = hashlib.sha1((kind + "|" + name.strip().lower())
                          .encode("utf-8")).hexdigest()[:8]
    return "%s_%s_%s" % (kind, safe, digest)


def _read_pkg_manifest(folder: str):
    try:
        with open(os.path.join(folder, "manifest.json"), "r",
                  encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def installed_assets(kind: str, root: str = "") -> list:
    """已装的某个 kind 的包（按目录名排）；装一半、坏掉的直接跳过。"""
    base = asset_dir(kind, root)
    if not os.path.isdir(base):
        return []
    try:
        subs = sorted(os.listdir(base), key=str.lower)
    except OSError:
        return []
    out = []
    for sub in subs:
        if sub.startswith(".") or sub.endswith(".old"):
            continue
        full = os.path.join(base, sub)
        if not os.path.isdir(full):
            continue
        doc = _read_pkg_manifest(full)
        if not doc or doc.get("kind") != kind:
            continue
        files = []
        for ent in doc.get("entries") or []:
            if isinstance(ent, dict) and isinstance(ent.get("path"), str):
                files.append(os.path.join(full, *ent["path"].split("/")))
        out.append({"kind": kind, "name": str(doc.get("name") or sub),
                    "version": str(doc.get("version") or ""),
                    "folder": sub, "path": full, "files": files})
    return out


def installed_asset_packages(root: str = "") -> list:
    out = []
    for kind in ASSET_KINDS:
        out.extend(installed_assets(kind, root))
    return out


def install_asset_zip(path: str, root: str = "") -> dict:
    """校验并装一个资源包；先写临时目录，全写完再改名，失败不留半成品。"""
    pkg = inspect_asset_zip(path)
    base = asset_dir(pkg["kind"], root)
    folder = asset_package_folder(pkg["kind"], pkg["name"])
    final = os.path.join(base, folder)
    try:
        os.makedirs(base, exist_ok=True)
    except OSError as exc:
        raise AssetError("io", str(exc))
    if os.path.isdir(final):
        old = _read_pkg_manifest(final) or {}
        if str(old.get("version") or "") == pkg["version"]:
            raise AssetError("exists", pkg["name"])
    tmp = os.path.join(base, ".tmp-" + uuid.uuid4().hex[:8])
    try:
        os.makedirs(tmp)
        entries = []
        for rel, data in pkg["files"].items():
            target = os.path.join(tmp, *rel.split("/"))
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent)
            with open(target, "wb") as fh:
                fh.write(data)
            entries.append({"path": rel,
                            "sha256": hashlib.sha256(data).hexdigest()})
        manifest = {"name": pkg["name"], "version": pkg["version"],
                    "kind": pkg["kind"], "entries": entries}
        with open(os.path.join(tmp, "manifest.json"), "w",
                  encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        if os.path.isdir(final):
            backup = final + ".old"
            shutil.rmtree(backup, ignore_errors=True)
            os.rename(final, backup)
            os.rename(tmp, final)
            shutil.rmtree(backup, ignore_errors=True)
        else:
            os.rename(tmp, final)
    except OSError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AssetError("io", str(exc))
    return {"kind": pkg["kind"], "name": pkg["name"],
            "version": pkg["version"], "folder": folder, "path": final,
            "files": [os.path.join(final, *r.split("/"))
                      for r in pkg["files"]]}


def remove_asset_package(package: dict, root: str = "") -> bool:
    """删掉一个已装的包；只认 assets 目录底下的目录。"""
    path = os.path.abspath(str(package.get("path") or ""))
    base = os.path.abspath(root or assets_root())
    if not path.startswith(base + os.sep) or not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.isdir(path)


def _font_family(data: bytes) -> str:
    """从 ttf/otf/ttc 的 name 表里读 family（TTC 只取第一套字形）。"""
    if len(data) < 12:
        return ""
    off = 0
    if data[:4] == b"ttcf":
        if len(data) < 16:
            return ""
        off = int.from_bytes(data[12:16], "big")
    if off + 12 > len(data) or data[off:off + 4] not in (
            b"\x00\x01\x00\x00", b"OTTO", b"true"):
        return ""
    count = int.from_bytes(data[off + 4:off + 6], "big")
    name_off = name_len = 0
    for i in range(count):
        rec = off + 12 + i * 16
        if rec + 16 > len(data):
            return ""
        if data[rec:rec + 4] == b"name":
            name_off = int.from_bytes(data[rec + 8:rec + 12], "big")
            name_len = int.from_bytes(data[rec + 12:rec + 16], "big")
            break
    if not name_off or not name_len or name_off + 6 > len(data):
        return ""
    rec_count = int.from_bytes(data[name_off + 2:name_off + 4], "big")
    str_off = name_off + int.from_bytes(data[name_off + 4:name_off + 6], "big")
    best = ""
    for i in range(rec_count):
        rec = name_off + 6 + i * 12
        if rec + 12 > len(data):
            break
        pid = int.from_bytes(data[rec:rec + 2], "big")
        eid = int.from_bytes(data[rec + 2:rec + 4], "big")
        nid = int.from_bytes(data[rec + 6:rec + 8], "big")
        length = int.from_bytes(data[rec + 8:rec + 10], "big")
        start = str_off + int.from_bytes(data[rec + 10:rec + 12], "big")
        if nid not in (1, 16) or start + length > len(data):
            continue
        chunk = data[start:start + length]
        if not chunk:
            continue
        if pid == 3 and eid in (1, 10):
            text = chunk.decode("utf-16-be", "ignore").strip()
        elif pid == 1 and eid == 0:
            text = chunk.decode("mac-roman", "ignore").strip()
        else:
            continue
        if not text:
            continue
        if nid == 16:
            return text
        if not best:
            best = text
    return best


def font_family_of(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return _font_family(fh.read())
    except OSError:
        return ""


def asset_fonts(root: str = "") -> dict:
    """已装字体包里的字体：family -> 文件路径（先装的优先）。"""
    out = {}
    seen = set()
    for pkg in installed_assets("font", root):
        for path in pkg["files"]:
            if not path.lower().endswith((".ttf", ".otf", ".ttc")):
                continue
            fam = font_family_of(path)
            if fam and fam.lower() not in seen:
                seen.add(fam.lower())
                out[fam] = path
    return out


def font_lookup_dir(family: str, root: str = "") -> str:
    """烧字幕时给 libass 的字体目录：资源包的字体给它的包目录，否则系统字体。"""
    want = str(family or "").strip().lower()
    for fam, path in asset_fonts(root).items():
        if fam.lower() == want:
            return os.path.dirname(path)
    return default_font_dir()


def register_asset_fonts():
    """把资源包里的字体注册给 Qt：字体下拉、预览都能直接选。"""
    try:
        from PyQt6.QtGui import QFontDatabase
        from PyQt6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001  没装 Qt 也要能跑命令行
        return
    if QApplication.instance() is None:
        return
    for fam, path in asset_fonts().items():
        try:
            QFontDatabase.addApplicationFont(path)
        except Exception:  # noqa: BLE001  坏字体别把启动拖垮
            log("asset font failed: %s" % path)


def asset_luts(root: str = "") -> list:
    out = []
    for pkg in installed_assets("lut", root):
        for path in pkg["files"]:
            if path.lower().endswith(".cube") and os.path.isfile(path):
                out.append(path)
    return out


def all_luts(folder: str = "", root: str = "") -> list:
    """下拉里该列的 LUT：外部目录里的 + 资源包里的（同一文件只列一次）。"""
    out, seen = [], set()
    for path in list_luts(folder) + asset_luts(root):
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def asset_transition_presets(root: str = "") -> list:
    """资源包里带来的转场预设（名字 = 内置 xfade 名 + 默认时长）。"""
    out = []
    for pkg in installed_assets("transition", root):
        for path in pkg["files"]:
            if not path.lower().endswith(".json"):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    preset = parse_transition_preset(json.load(fh))
            except (OSError, ValueError, AssetError):
                continue
            preset["package"] = pkg["name"]
            out.append(preset)
    out.sort(key=lambda p: p["name"].lower())
    return out


def asset_error_text(exc: "AssetError") -> str:
    """把资源包出错翻译成一句人话。"""
    reason = getattr(exc, "reason", "")
    table = {
        "missing": tr("找不到这个 zip 文件"),
        "bad-zip": tr("这不是一个能读的 zip"),
        "too-many": tr("包里的文件太多了（上限 64 个）"),
        "too-big": tr("包解压后太大了（上限 64 MB）"),
        "unsafe-path": tr("包里有不安全的路径（绝对路径或 .. 上跳）"),
        "symlink": tr("包里有符号链接，不收"),
        "executable": tr("包里有可执行文件，不收"),
        "bad-manifest": tr("包的清单（manifest.json）写得不对"),
        "bad-entry": tr("包里的文件类型不对"),
        "missing-entry": tr("清单里写的文件不在包里"),
        "hash-mismatch": tr("文件校验对不上，包可能坏了"),
        "bad-preset": tr("转场预设写得不对"),
        "exists": tr("同名的包已经装过同一个版本；先删掉再装"),
        "io": tr("写文件失败"),
    }
    detail = str(exc) if str(exc) != reason else ""
    text = tr("装不进去：") + table.get(reason, tr("认不出来的问题"))
    return text + ("（" + detail + "）" if detail else "")


def asset_usage(project, package: dict, current_font: str = "") -> list:
    """工程里哪些地方用了这个包（删除前的提示用）。"""
    base = os.path.abspath(str(package.get("path") or "")).lower()
    used = []
    for i, clip in enumerate(getattr(project, "clips", None) or []):
        lut = getattr(clip, "lut", "") or ""
        if lut and os.path.abspath(lut).lower().startswith(base + os.sep):
            used.append(tr("第 {n} 段的滤镜", n=i + 1))
    if package.get("kind") == "font":
        want = str(current_font or "").strip().lower()
        for path in package.get("files") or []:
            if want and font_family_of(path).lower() == want:
                used.append(tr("字幕字体"))
                break
    return used


class Clip:
    """时间线上的一个视频元素：图片、片头或片尾。

    kind: image / intro / outro
    fade: 这个元素自己开头淡入、结尾淡出（黑场）
    xfade: 跟下一个元素交叉淡化
    brightness / contrast / saturation: 这一段自己的调色（默认 0 / 1 / 1）
    lut: 套在这个元素上的 .cube LUT（空 = 不套）
    """

    __slots__ = ("path", "dur", "kind", "fade", "xfade", "xfade_name",
                 "xfade_dur", "src_in", "src_out",
                 "brightness", "contrast", "saturation", "lut")

    def __init__(self, path: str, dur: float, kind: str = "image",
                 fade: bool = False, xfade: bool = False,
                 src_in: float = 0.0, src_out: float | None = None,
                 xfade_name: str = "", xfade_dur: float | None = None,
                 brightness: float = 0.0, contrast: float = 1.0,
                 saturation: float = 1.0, lut: str = ""):
        self.path = path
        self.dur = float(dur)
        self.kind = kind if kind in ("image", "video", "intro", "outro") else "image"
        self.fade = bool(fade)
        self.xfade = bool(xfade)
        self.xfade_name = xfade_name if xfade_name in XFADE_NAMES else ""
        self.xfade_dur = None if xfade_dur is None else float(xfade_dur)
        self.src_in = float(src_in)     # 视频：从源的第几秒开始
        self.src_out = None if src_out is None else float(src_out)
        self.brightness = _clamp_range(brightness, COLOR_BRIGHT,
                                       COLOR_DEFAULT[0])
        self.contrast = _clamp_range(contrast, COLOR_CONTRAST, COLOR_DEFAULT[1])
        self.saturation = _clamp_range(saturation, COLOR_SATURATION,
                                       COLOR_DEFAULT[2])
        self.lut = lut if isinstance(lut, str) else ""

    @property
    def has_color(self) -> bool:
        return bool(self.lut) or color_filter(self.brightness, self.contrast,
                                              self.saturation) != ""

    def color_chain(self) -> str:
        """build_graph 里接在这一段上的调色串（没调过就是空串）。"""
        return color_filter(self.brightness, self.contrast, self.saturation,
                            self.lut)

    @property
    def is_video(self) -> bool:
        return self.kind in ("video", "intro", "outro")

    @property
    def span(self) -> float:
        """这一段在时间线上占多长（视频扣掉入出点之后）。"""
        if not self.is_video:
            return max(0.0, self.dur)
        end = self.dur if self.src_out is None else self.src_out
        return max(0.0, end - self.src_in)

    def copy(self):
        return Clip(self.path, self.dur, self.kind, self.fade, self.xfade,
                    self.src_in, self.src_out, self.xfade_name, self.xfade_dur,
                    self.brightness, self.contrast, self.saturation, self.lut)

    def __repr__(self):
        return f"Clip({os.path.basename(self.path)!r}, {self.dur:.2f}, {self.kind})"


class AudioClip:
    """音频轨道上的一条：可以随时拖着换起始位置。

    gap=True 的（背景音乐）只在图片段出声，片头/片尾那几段会安静；
    别的音频从 start 开始按自己长度播。
    """

    __slots__ = ("path", "dur", "start", "volume", "gap",
                 "gain_db", "points", "src_in")

    def __init__(self, path: str, dur: float, start: float = 0.0,
                 volume: float = 1.0, gap: bool = False,
                 gain_db: float = 0.0, points=None, src_in: float = 0.0):
        self.path = path
        self.dur = float(dur)
        self.start = max(0.0, float(start))
        self.volume = max(0.0, min(2.0, float(volume)))
        self.gap = bool(gap)
        self.gain_db = clamp_gain_db(gain_db)
        self.points = clean_env_points(points)
        self.src_in = max(0.0, float(src_in))   # 从源的第几秒开始用（裁头）

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def __repr__(self):
        return f"AudioClip({self.name!r}, {self.dur:.2f}, @{self.start:.2f})"


AUDIO_GAIN_MIN, AUDIO_GAIN_MAX = -24.0, 12.0


def clamp_gain_db(value) -> float:
    """每段的增益（dB）：读不出来就当没加，夹在 -24..+12 之间。"""
    db = _doc_num(value, 0.0)
    return max(AUDIO_GAIN_MIN, min(AUDIO_GAIN_MAX, db))


def audio_level(gain_db) -> float:
    """dB 换成线性倍数：-6 dB ≈ 0.50，+6 dB ≈ 2.00。"""
    return 10.0 ** (clamp_gain_db(gain_db) / 20.0)


def clean_env_points(points) -> list:
    """音量包络点 [(秒, 增益 0..1)]：排好序、夹范围、同一时刻只留最后一个。"""
    out = []
    for raw in points or []:
        try:
            t = float(raw[0])
            g = float(raw[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if not math.isfinite(t) or not math.isfinite(g):
            continue
        out.append((max(0.0, t), max(0.0, min(1.0, g))))
    out.sort(key=lambda p: p[0])
    ded = []
    for t, g in out:
        if ded and abs(t - ded[-1][0]) < 0.01:
            ded[-1] = (ded[-1][0], g)      # 同一时刻以最后写的为准
        else:
            ded.append((t, g))
    return ded


def envelope_expr(points, base: float = 1.0, t0: float = 0.0) -> str:
    """把包络点拼成 ffmpeg 的 volume 表达式；没点就返回空串。

    points 的时间是相对音频段自己的起点；t0 是这个段在输出时间轴上的起点，
    给 extras 用（它们先 adelay 再套 volume，t 已经是输出时间）。
    两头的做法：第一个点之前保持第一个点的值，最后一个点之后保持最后一个。
    """
    pts = clean_env_points(points)
    if not pts:
        return ""
    tvar = "t" if abs(t0) < 1e-6 else "(t-%.3f)" % t0
    expr = "%.5f" % (pts[-1][1] * base)
    for i in range(len(pts) - 1, 0, -1):
        ta, ga = pts[i - 1]
        tb, gb = pts[i]
        slope = (gb - ga) / max(1e-4, tb - ta)
        seg = "%.5f+%.5f*(%s-%.3f)" % (ga * base, slope * base, tvar, ta)
        expr = "if(lt(%s,%.3f),%s,%s)" % (tvar, tb, seg, expr)
    expr = "if(lt(%s,%.3f),%.5f,%s)" % (tvar, pts[0][0], pts[0][1] * base, expr)
    return "volume=volume='%s':eval=frame" % expr


def audio_fx(gain_db, points, t0: float = 0.0) -> str:
    """一段音频该接的增益/包络滤镜；两样都没有就返回空串。"""
    level = audio_level(gain_db)
    pts = clean_env_points(points)
    if not pts:
        if abs(level - 1.0) < 0.005:
            return ""
        return "volume=%.5f" % level
    return envelope_expr(pts, base=level, t0=t0)


WAVE_RATE = 50            # 波形每秒取多少个峰
WAVE_CHUNK = 160          # 8 kHz 下 160 个采样 = 20 ms


def peaks_dir() -> str:
    """波形缓存目录：跟 settings.json 放一起，删了下次自己重建。"""
    d = os.path.join(components.data_dir(), "peaks")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def wave_key(path: str) -> str:
    """缓存指纹：文件大小 + 修改时间；文件不在就是空串。"""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return "%d:%d" % (st.st_size, st.st_mtime_ns)


def _peaks_cache_path(path: str) -> str:
    import hashlib
    name = hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()
    return os.path.join(peaks_dir(), name + ".peak")


def _decode_peaks(path: str) -> list:
    """用 ffmpeg 解成单声道 8 kHz，每 20 ms 取一个峰（0..1）。"""
    import array
    cmd = [FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1",
           "-ar", "8000", "-f", "s16le", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    if res.returncode != 0:
        return []
    data = res.stdout
    data = data[:len(data) - len(data) % 2]
    samples = array.array("h")
    samples.frombytes(data)
    peaks = []
    for i in range(0, len(samples), WAVE_CHUNK):
        chunk = samples[i:i + WAVE_CHUNK]
        if not chunk:
            break
        top = max(max(chunk), -min(chunk))
        peaks.append(round(min(1.0, top / 32768.0), 3))
    return peaks


def audio_peaks(path: str, force: bool = False) -> list:
    """一段音频的波形峰值；按「大小+修改时间」缓存，改了文件就重算。

    读不出来（文件不在、解码失败）就给空表，界面那边画成一根平线。
    """
    key = wave_key(path)
    if not key:
        return []
    cache = _peaks_cache_path(path)
    if not force:
        try:
            with open(cache, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("key") == key and isinstance(cached.get("peaks"), list):
                return [float(x) for x in cached["peaks"]]
        except (OSError, ValueError, TypeError):
            pass
    peaks = _decode_peaks(path)
    if not peaks:
        return []
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump({"key": key, "rate": WAVE_RATE, "peaks": peaks}, fh)
    except OSError:
        pass
    return peaks


def _media_dur(path: str) -> float:
    """片头片尾视频有多长；探不到就给 3 秒，别让界面卡住。"""
    try:
        d = probe_duration(path)
    except Exception:
        d = 0.0
    return d if d > 0.05 else 3.0


def _doc_num(value, default: float) -> float:
    """工程文件里的数字：读不出来就用默认值，别让一个字段废掉整份文件。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


# ---------------------------------------------------------------- 叠加层（画中画）
# 叠加层故意不挂在 Clip 上：它的进出时间、位置跟段链无关，
# 硬塞进 clips 会把 _xfade_plan / frame_counts 一起污染。
OVERLAY_ANCHORS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")
OVERLAY_ANCHOR_POS = {"tl": (0.0, 0.0), "tc": (0.5, 0.0), "tr": (1.0, 0.0),
                      "ml": (0.0, 0.5), "mc": (0.5, 0.5), "mr": (1.0, 0.5),
                      "bl": (0.0, 1.0), "bc": (0.5, 1.0), "br": (1.0, 1.0)}
OVERLAY_ANCHOR_LABELS = {"tl": "左上", "tc": "中上", "tr": "右上",
                         "ml": "左中", "mc": "正中", "mr": "右中",
                         "bl": "左下", "bc": "中下", "br": "右下"}
OVERLAY_MIN_SCALE, OVERLAY_MAX_SCALE = 5.0, 100.0
OVERLAY_MIN_SPAN = 0.1


class Overlay:
    """压在成片上的一层画面（画中画）：从 start 到 end 出现在哪个位置。

    scale 是相对成片宽度的百分比；anchor 是九宫格位置（tl…br），
    dx / dy 是在九宫格基础上的偏移（也是百分比，正数往右 / 往下）。
    opacity 是 0..100 的不透明度。
    """

    __slots__ = ("path", "start", "end", "scale", "anchor", "dx", "dy",
                 "opacity")

    def __init__(self, path: str, start: float, end: float,
                 scale: float = 40.0, anchor: str = "tr", dx: float = 0.0,
                 dy: float = 0.0, opacity: float = 100.0):
        self.path = path if isinstance(path, str) else ""
        self.start = max(0.0, _doc_num(start, 0.0))
        stop = _doc_num(end, self.start + 1.0)
        if stop - self.start < OVERLAY_MIN_SPAN:
            stop = self.start + OVERLAY_MIN_SPAN
        self.end = stop
        self.scale = min(OVERLAY_MAX_SCALE,
                         max(OVERLAY_MIN_SCALE, _doc_num(scale, 40.0)))
        self.anchor = anchor if anchor in OVERLAY_ANCHORS else "tr"
        self.dx = min(100.0, max(-100.0, _doc_num(dx, 0.0)))
        self.dy = min(100.0, max(-100.0, _doc_num(dy, 0.0)))
        self.opacity = min(100.0, max(0.0, _doc_num(opacity, 100.0)))

    @property
    def span(self) -> float:
        return max(0.0, self.end - self.start)

    def anchor_pos(self) -> tuple[float, float]:
        return OVERLAY_ANCHOR_POS.get(self.anchor, (1.0, 0.0))

    def copy(self):
        return Overlay(self.path, self.start, self.end, self.scale,
                       self.anchor, self.dx, self.dy, self.opacity)

    def __repr__(self):
        return (f"Overlay({os.path.basename(self.path)!r}, "
                f"{self.start:.2f}-{self.end:.2f}, {self.scale:.0f}%)")


def overlay_specs(overlays, base_index: int, total: float = 0.0) -> list[dict]:
    """把 Overlay 列表翻成 build_graph 要的规格（带上 ffmpeg 输入下标）。"""
    out: list[dict] = []
    for ov in overlays or []:
        start = max(0.0, float(ov.start))
        end = float(ov.end)
        if total and total > 0:
            start = min(start, float(total))
            end = min(end, float(total))
        if end - start < 0.05:
            continue
        ax, ay = ov.anchor_pos()
        out.append({"index": base_index + len(out), "path": ov.path,
                    "start": start, "end": end, "scale": float(ov.scale),
                    "ax": ax, "ay": ay, "dx": float(ov.dx),
                    "dy": float(ov.dy), "opacity": float(ov.opacity)})
    return out


class Project:
    """时间线模型：片头 + 图片序列 + 片尾，外加一条或多条音频。

    mode:
      bgm   跟音乐：图片段总长 = 音乐长度，片头片尾各占自己的时长
      total 自设总长：整个成片（含片头片尾）= total_setting
      free  逐段秒数：图片段 = 各图之和
    固定总长时手改某张，其余按原比例让位，总长守恒（片头片尾不动）。
    """

    def __init__(self, fps: int = FPS):
        self.clips: list[Clip] = []
        self.audios: list[AudioClip] = []
        self.overlays: list[Overlay] = []    # 画中画：压在成片上的那几层
        self.mode = "bgm"
        self.audio_start = 0.0        # 背景音乐从第几秒开始用
        self.audio_len = 0.0          # 用多长；0 = 到音乐结尾
        self.total_setting = 10.0
        self.fps = int(fps)
        self.subtitles_path = ""      # 导入的字幕文件（工程里记着，方便重定位）
        self.cues: list[dict] = []    # 解析好的字幕条目，时间对齐成片总轴
        self.export_preset = "generic"   # 上次选的导出预设（跟着工程存）
        self.narration_path = ""      # 配音旁白（跟旁白模式按它定时长）
        self.narration_dur = 0.0

    # -- 背景音乐：还是挂在第一条音频上，命令行/设置都靠它 ---------------
    @property
    def audio_path(self) -> str | None:
        for a in self.audios:
            if a.gap:
                return a.path
        return None

    @audio_path.setter
    def audio_path(self, value):
        if not value:
            self.audios = [a for a in self.audios if not a.gap]
            return
        dur = probe_duration(value) if os.path.exists(value) else 0.0
        for a in self.audios:
            if a.gap:
                a.path, a.dur = value, dur
                return
        self.audios.insert(0, AudioClip(value, dur, 0.0, 1.0, gap=True))

    @property
    def audio_dur(self) -> float:
        for a in self.audios:
            if a.gap:
                return a.dur
        return 0.0

    @audio_dur.setter
    def audio_dur(self, value):
        for a in self.audios:
            if a.gap:
                a.dur = float(value)
                return
        # 只有时长还没有来源（老代码 / 单元测试会这么写）：先记着
        self.audios.insert(0, AudioClip("", float(value), 0.0, 1.0, gap=True))

    def music_volume(self) -> float:
        for a in self.audios:
            if a.gap:
                return a.volume
        return 1.0

    def set_music_volume(self, value: float):
        for a in self.audios:
            if a.gap:
                a.volume = max(0.0, min(2.0, float(value)))

    # -- 工程文件 / 撤回快照（同一套格式：存盘和撤销都走 to_doc）------------
    def to_doc(self) -> dict:
        """整份工程：带版本号、能直接 json.dump。撤销栈存的也是这个。"""
        return {
            "version": PROJECT_VERSION,
            "app": {"name": "Pic2Video", "version": APP_VERSION},
            "fps": int(self.fps),
            "mode": self.mode,
            "audio_start": self.audio_start,
            "audio_len": self.audio_len,
            "total_setting": self.total_setting,
            "clips": [{"path": c.path, "dur": c.dur, "kind": c.kind,
                       "fade": bool(c.fade), "xfade": bool(c.xfade),
                       "xfade_name": c.xfade_name, "xfade_dur": c.xfade_dur,
                       "src_in": c.src_in, "src_out": c.src_out,
                       "brightness": c.brightness, "contrast": c.contrast,
                       "saturation": c.saturation, "lut": c.lut}
                      for c in self.clips],
            "audios": [{"path": a.path, "dur": a.dur, "start": a.start,
                        "volume": a.volume, "gap": bool(a.gap),
                        "gain_db": a.gain_db, "src_in": a.src_in,
                        "points": [[t, g] for t, g in a.points]}
                       for a in self.audios],
            "overlays": [{"path": o.path, "start": o.start, "end": o.end,
                          "scale": o.scale, "anchor": o.anchor,
                          "dx": o.dx, "dy": o.dy, "opacity": o.opacity}
                         for o in self.overlays],
            "subtitles": {"path": self.subtitles_path,
                          "cues": [dict(c) for c in self.cues]},
            "narration": {"path": self.narration_path, "dur": self.narration_dur},
            "export_preset": self.export_preset,
        }

    @classmethod
    def from_doc(cls, doc: dict) -> "Project":
        project = cls()
        project.load_doc(doc)
        return project

    def load_doc(self, doc: dict):
        """按工程文件格式读入；坏文件一律抛 ProjectFileError，不半途改坏现场。"""
        if not isinstance(doc, dict):
            raise ProjectFileError("bad-shape", "工程内容不是一个对象")
        version = doc.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ProjectFileError("bad-shape", "工程文件没有版本号")
        if version > PROJECT_VERSION:
            raise ProjectFileError(
                "newer", "工程是 %d 版格式，这个版本只认到 %d 版"
                % (version, PROJECT_VERSION))
        clips = doc.get("clips", [])
        audios = doc.get("audios", [])
        if not isinstance(clips, list) or not isinstance(audios, list):
            raise ProjectFileError("bad-shape", "素材表不是列表")
        raw_overlays = doc.get("overlays")
        if raw_overlays is None:
            raw_overlays = []
        if not isinstance(raw_overlays, list):
            raise ProjectFileError("bad-shape", "叠加层不是列表")
        new_clips = [self._clip_from_doc(raw) for raw in clips]
        new_audios = [self._audio_from_doc(raw) for raw in audios]
        new_overlays = [ov for ov in (self._overlay_from_doc(raw)
                                      for raw in raw_overlays) if ov is not None]
        raw_subs = doc.get("subtitles") or {}
        if not isinstance(raw_subs, dict):
            raise ProjectFileError("bad-shape", "字幕内容不是一个对象")
        raw_cues = raw_subs.get("cues") or []
        if not isinstance(raw_cues, list):
            raise ProjectFileError("bad-shape", "字幕条目不是列表")
        sub_path = raw_subs.get("path") or ""
        if not isinstance(sub_path, str):
            sub_path = ""
        new_cues = _clean_cues(raw_cues)
        raw_nar = doc.get("narration") or {}
        if not isinstance(raw_nar, dict):
            raw_nar = {}
        nar_path = raw_nar.get("path") or ""
        if not isinstance(nar_path, str):
            nar_path = ""
        nar_dur = max(0.0, _doc_num(raw_nar.get("dur"), 0.0))
        raw_preset = doc.get("export_preset")
        preset = raw_preset if raw_preset in EXPORT_PRESETS else "generic"
        mode = doc.get("mode")
        self.clips = new_clips
        self.audios = new_audios
        self.overlays = new_overlays
        self.mode = mode if mode in MODE_LABELS else "bgm"
        self.audio_start = _doc_num(doc.get("audio_start"), 0.0)
        self.audio_len = _doc_num(doc.get("audio_len"), 0.0)
        self.total_setting = _doc_num(doc.get("total_setting"), 10.0)
        self.fps = 60 if _doc_num(doc.get("fps"), FPS) >= 50 else 30
        self.subtitles_path = sub_path
        self.cues = new_cues
        self.narration_path = nar_path
        self.narration_dur = nar_dur
        self.export_preset = preset

    @staticmethod
    def _clip_from_doc(raw) -> Clip:
        if not isinstance(raw, dict):
            raise ProjectFileError("bad-shape", "素材条目不是对象")
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            raise ProjectFileError("bad-shape", "素材条目缺 path")
        kind = raw.get("kind", "image")
        if kind not in ("image", "video", "intro", "outro"):
            raise ProjectFileError("bad-shape", "不认识的素材类型：%r" % (kind,))
        dur = max(0.0, _doc_num(raw.get("dur"), 0.0))
        src_in = max(0.0, _doc_num(raw.get("src_in"), 0.0))
        raw_out = raw.get("src_out")
        src_out = None if raw_out is None else _doc_num(raw_out, 0.0)
        if src_out is not None:
            src_out = min(src_out, dur)
            if src_out <= src_in:
                src_out = None
        name = raw.get("xfade_name") or ""
        if not isinstance(name, str):
            name = ""
        raw_xd = raw.get("xfade_dur")
        xfade_dur = None if raw_xd is None else min(
            XFADE_MAX, max(XFADE_MIN, _doc_num(raw_xd, 0.3)))
        raw_lut = raw.get("lut")
        lut = raw_lut if isinstance(raw_lut, str) else ""
        return Clip(path, dur, kind,
                    bool(raw.get("fade", False)),
                    bool(raw.get("xfade", False)) or bool(name),
                    src_in, src_out, name, xfade_dur,
                    _doc_num(raw.get("brightness"), 0.0),
                    _doc_num(raw.get("contrast"), 1.0),
                    _doc_num(raw.get("saturation"), 1.0),
                    lut)

    @staticmethod
    def _overlay_from_doc(raw) -> "Overlay | None":
        """坏条目丢掉就好：一层画中画不值得废掉整份工程。"""
        if not isinstance(raw, dict):
            return None
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            return None
        return Overlay(path, _doc_num(raw.get("start"), 0.0),
                       _doc_num(raw.get("end"), 1.0),
                       _doc_num(raw.get("scale"), 40.0),
                       raw.get("anchor") or "tr",
                       _doc_num(raw.get("dx"), 0.0),
                       _doc_num(raw.get("dy"), 0.0),
                       _doc_num(raw.get("opacity"), 100.0))

    @staticmethod
    def _audio_from_doc(raw) -> AudioClip:
        if not isinstance(raw, dict):
            raise ProjectFileError("bad-shape", "音频条目不是对象")
        path = raw.get("path", "")
        if not isinstance(path, str):
            raise ProjectFileError("bad-shape", "音频条目的 path 不是文字")
        raw_points = raw.get("points")
        if not isinstance(raw_points, list):
            raw_points = []
        return AudioClip(path, max(0.0, _doc_num(raw.get("dur"), 0.0)),
                         _doc_num(raw.get("start"), 0.0),
                         _doc_num(raw.get("volume"), 1.0),
                         bool(raw.get("gap", False)),
                         raw.get("gain_db"), raw_points,
                         src_in=max(0.0, _doc_num(raw.get("src_in"), 0.0)))

    # 撤销栈沿用的老名字，底下就是工程格式，别再长两套
    def to_state(self) -> dict:
        return self.to_doc()

    def load_state(self, state: dict):
        if isinstance(state, dict) and "version" not in state:
            state = dict(state, version=PROJECT_VERSION)   # 早期撤销快照
        self.load_doc(state)

    # -- 素材检查 ------------------------------------------------------
    def missing_media(self) -> list[str]:
        """工程里登记了、磁盘上已经不在了的素材（按出现顺序，去重）。"""
        out: list[str] = []
        for path in ([c.path for c in self.clips]
                     + [c.lut for c in self.clips]
                     + [o.path for o in self.overlays]
                     + [a.path for a in self.audios]):
            if path and path not in out and not os.path.exists(path):
                out.append(path)
        return out

    def relink_media(self, old: str, new: str) -> int:
        """把登记成 old 的素材全换成 new（挪了文件之后重新指过去），返回换了几条。"""
        if not old or not new or old == new:
            return 0
        n = 0
        for clip in self.clips:
            if clip.path == old:
                clip.path = new
                n += 1
            if clip.lut == old:
                clip.lut = new
                n += 1
        for overlay in self.overlays:
            if overlay.path == old:
                overlay.path = new
                n += 1
        for audio in self.audios:
            if audio.path == old:
                audio.path = new
                n += 1
        return n

    # -- 片段分类 ------------------------------------------------------
    def images(self) -> list[Clip]:
        return [c for c in self.clips if not c.is_video]

    def image_count(self) -> int:
        return sum(1 for c in self.clips if not c.is_video)

    def segment_count(self) -> int:
        """画面段的条数（图片 + 视频，不算片头片尾）。"""
        return sum(1 for c in self.clips if c.kind in ("image", "video"))

    def intro(self) -> Clip | None:
        return next((c for c in self.clips if c.kind == "intro"), None)

    def outro(self) -> Clip | None:
        return next((c for c in self.clips if c.kind == "outro"), None)

    def fixed_span(self) -> float:
        """片头 + 片尾占掉的时间（图片段要减掉它）。"""
        return sum(c.span for c in self.clips if c.is_video)

    # -- 目标总长 ------------------------------------------------------
    def music_length(self) -> float:
        """截取之后这段音乐实际有多长（0 = 没有音乐）。"""
        if self.audio_dur <= 0:
            return 0.0
        start = max(0.0, float(self.audio_start))
        avail = max(0.1, self.audio_dur - start)
        if self.audio_len and self.audio_len > 0:
            return max(0.1, min(avail, float(self.audio_len)))
        return avail

    def target(self) -> float | None:
        n = self.image_count()
        if n == 0:
            return None
        if self.mode == "voice" and self.narration_dur > 0:
            return self.narration_dur + self.fixed_span()
        if self.mode == "bgm" and self.audio_dur > 0:
            return self.music_length() + self.fixed_span()
        if self.mode == "total":
            return max(self.total_setting, MIN_DUR * n + self.fixed_span())
        return None

    def material_len(self) -> float:
        """跟素材总长：素材总长（片头 + 片尾）÷ 图片数，每张图播这么久。"""
        n = self.image_count()
        fixed = self.fixed_span()
        if n <= 0 or fixed <= 0:
            return 0.0
        return max(MIN_DUR, fixed / n)

    def apply_material(self):
        """把「跟素材总长」算出来的每张时长铺到图片上。"""
        if self.mode != "material":
            return
        per = self.material_len()
        if per <= 0:
            return
        for c in self.images():
            c.dur = per

    def total(self) -> float:
        return sum(c.span for c in self.clips)

    # -- 归一化 --------------------------------------------------------
    def normalize(self):
        if self.mode == "material":
            self.apply_material()
        else:
            self._normalize_durations()
        self.fit_transitions()

    def _normalize_durations(self):
        t = self.target()
        imgs = self.images()
        if t is None or not imgs:
            return
        room = max(MIN_DUR * len(imgs), t - self.fixed_span())
        durs = [c.dur for c in imgs]
        total = sum(durs)
        if total <= 0:
            durs = [room / len(durs)] * len(durs)
        else:
            durs = [max(MIN_DUR, d * room / total) for d in durs]
        for c, d in zip(imgs, durs):
            c.dur = d
        self._fix_sum(room)

    def _fix_sum(self, room: float):
        imgs = self.images()
        if not imgs:
            return
        diff = room - sum(c.dur for c in imgs)
        if abs(diff) < 1e-9:
            return
        i = max(range(len(imgs)), key=lambda k: imgs[k].dur)
        imgs[i].dur = max(MIN_DUR, imgs[i].dur + diff)

    def set_duration(self, index: int, value: float):
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        if clip.is_video:           # 片头片尾时长跟着视频走
            return
        t = self.target()
        imgs = self.images()
        if t is None:
            clip.dur = max(MIN_DUR, value)
            self.fit_transitions()
            return
        room = max(MIN_DUR * len(imgs), t - self.fixed_span())
        pos = imgs.index(clip)
        value = min(max(value, MIN_DUR),
                    max(MIN_DUR, room - MIN_DUR * (len(imgs) - 1)))
        durs = [c.dur for c in imgs]
        durs[pos] = value
        self._fit_others(durs, pos, room)
        for c, d in zip(imgs, durs):
            c.dur = d
        self._fix_sum(room)
        self.fit_transitions()

    def set_video_len(self, index: int, value: float):
        """改片头 / 片尾这类视频元素播多久：比原片短就截短，长就循环。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        if not clip.is_video:
            return
        clip.dur = max(MIN_DUR, float(value))
        self.fit_transitions()
        t = self.target()
        imgs = self.images()
        if t is not None and imgs:
            room = max(MIN_DUR * len(imgs), t - self.fixed_span())
            self._fix_sum(room)

    def set_src_in(self, index: int, value: float):
        """拖左边 / 按 I：改入点，最晚到出点前一帧。"""
        clip = self._video_at(index)
        if clip is None:
            return
        end = clip.dur if clip.src_out is None else clip.src_out
        room = max(0.0, end - self._min_frame())
        clip.src_in = max(0.0, min(float(value), room))
        self.fit_transitions()

    def set_src_out(self, index: int, value: float):
        """拖右边 / 按 O：改出点，最长到源时长，最短留一帧。"""
        clip = self._video_at(index)
        if clip is None:
            return
        clip.src_out = max(clip.src_in + self._min_frame(),
                           min(float(value), clip.dur))
        self.fit_transitions()

    def fit_transitions(self) -> int:
        """切段/改时长后，把放不下的转场缩到能放下，免得导出时才报错。

        只动「手动定了时长」的转场：跟随全局的那些由 _xfade_plan 按可用空间收，
        本来就放得下。返回被改动的边界数。
        """
        changed = 0
        for i, clip in enumerate(self.clips):
            if clip.xfade_dur is None:
                continue
            if not (clip.xfade or clip.xfade_name):
                continue
            nxt = self.clips[i + 1] if i + 1 < len(self.clips) else None
            room = min(clip.span, nxt.span if nxt is not None else 0.0) / 2.0
            if clip.xfade_dur <= room + 1e-9:
                continue
            if room < XFADE_MIN:
                clip.xfade = False
                clip.xfade_name = ""
                clip.xfade_dur = None
            else:
                clip.xfade_dur = round(room, 2)
            changed += 1
        return changed

    def _video_at(self, index: int):
        if not (0 <= index < len(self.clips)):
            return None
        clip = self.clips[index]
        return clip if clip.is_video else None

    def _min_frame(self) -> float:
        return 1.0 / max(1, int(self.fps))

    @staticmethod
    def _fit_others(durs: list[float], fixed: int, target: float):
        idx = [j for j in range(len(durs)) if j != fixed]
        remaining = target - durs[fixed]
        for _ in range(8):
            if not idx:
                return
            base = sum(durs[j] for j in idx)
            if base <= 0:
                for j in idx:
                    durs[j] = remaining / len(idx)
                return
            scale = remaining / base
            low = [j for j in idx if durs[j] * scale < MIN_DUR]
            if not low:
                for j in idx:
                    durs[j] = max(MIN_DUR, durs[j] * scale)
                return
            for j in low:
                durs[j] = MIN_DUR
                remaining -= MIN_DUR
                idx.remove(j)

    # -- 增删排序 ------------------------------------------------------
    def _insert_at(self) -> int:
        """新图插在片尾前面（没有片尾就加到末尾）。"""
        outro = self.outro()
        if outro is not None:
            return self.clips.index(outro)
        return len(self.clips)

    def add_images(self, paths, dur: float | None = None,
                   intro: str = "", outro: str = ""):
        if not paths:
            return
        was_empty = self.image_count() == 0
        t = self.target()
        base = dur if dur is not None else (
            (t / (self.image_count() + len(paths))) if t else 3.0)
        at = self._insert_at()
        self.clips[at:at] = [Clip(p, max(MIN_DUR, base)) for p in paths]
        if was_empty:
            if intro and os.path.exists(intro) and self.intro() is None:
                self.clips.insert(0, Clip(intro, _media_dur(intro), "intro"))
            if outro and os.path.exists(outro) and self.outro() is None:
                self.clips.append(Clip(outro, _media_dur(outro), "outro"))
        self.normalize()

    def insert_images(self, index: int, paths, dur: float | None = None):
        """在指定位置插图片（从外面拖到列表某个位置时用）。"""
        if not paths:
            return
        t = self.target()
        base = dur if dur is not None else (
            (t / (self.image_count() + len(paths))) if t else 3.0)
        at = max(0, min(int(index), len(self.clips)))
        self.clips[at:at] = [Clip(p, max(MIN_DUR, base)) for p in paths]
        self.normalize()

    def replace_image(self, index: int, path: str):
        """换掉这一张，时长 / 淡入淡出 / 交叉淡化都照旧。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        if clip.is_video:
            return
        clip.path = path
        self.normalize()

    def add_media(self, paths, dur: float | None = None,
                  intro: str = "", outro: str = ""):
        """一段段素材：图片按图算，视频按它自己的长度算，顺序照给进来的来。"""
        paths = [p for p in paths if p]
        if not paths:
            return
        was_empty = not self.clips
        t = self.target()
        n_img = sum(1 for p in paths if is_image_path(p))
        base = dur if dur is not None else (
            (t / (self.image_count() + n_img)) if t and n_img else 3.0)
        at = self._insert_at()
        made = []
        for p in paths:
            if is_video_path(p):
                made.append(Clip(p, _media_dur(p), "video"))
            else:
                made.append(Clip(p, max(MIN_DUR, base)))
        self.clips[at:at] = made
        if was_empty:
            if intro and os.path.exists(intro) and self.intro() is None:
                self.clips.insert(0, Clip(intro, _media_dur(intro), "intro"))
            if outro and os.path.exists(outro) and self.outro() is None:
                self.clips.append(Clip(outro, _media_dur(outro), "outro"))
        self.normalize()

    def insert_media(self, index: int, paths, dur: float | None = None):
        """在指定位置插一段段素材（从外面拖到列表某个位置时用）。"""
        paths = [p for p in paths if p]
        if not paths:
            return
        t = self.target()
        n_img = sum(1 for p in paths if is_image_path(p))
        base = dur if dur is not None else (
            (t / (self.image_count() + n_img)) if t and n_img else 3.0)
        at = max(0, min(int(index), len(self.clips)))
        made = []
        for p in paths:
            if is_video_path(p):
                made.append(Clip(p, _media_dur(p), "video"))
            else:
                made.append(Clip(p, max(MIN_DUR, base)))
        self.clips[at:at] = made
        self.normalize()

    def replace_media(self, index: int, path: str):
        """换掉这一段；同类（图换图、视频换视频）时保留时长和所有设置。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        new_video = is_video_path(path)
        if clip.is_video and new_video:
            clip.path = path
            clip.dur = _media_dur(path)
            clip.src_in = 0.0
            clip.src_out = None
        elif not clip.is_video and not new_video:
            clip.path = path
        else:
            dur = _media_dur(path) if new_video else clip.dur
            self.clips[index] = Clip(
                path, dur, "video" if new_video else "image",
                clip.fade, clip.xfade,
                0.0 if new_video else clip.src_in, None,
                clip.xfade_name, clip.xfade_dur,
                clip.brightness, clip.contrast, clip.saturation, clip.lut)
        self.normalize()

    def add_video(self, path: str, kind: str) -> Clip | None:
        """手动挂片头/片尾（同一位置只留一个）。"""
        if not path or not os.path.exists(path):
            return None
        dur = _media_dur(path)
        old = self.intro() if kind == "intro" else self.outro()
        if old is not None:
            old.path, old.dur = path, dur
        else:
            clip = Clip(path, dur, kind)
            if kind == "intro":
                self.clips.insert(0, clip)
            else:
                self.clips.append(clip)
        self.normalize()
        return self.intro() if kind == "intro" else self.outro()

    def clear_video(self, kind: str):
        self.clips = [c for c in self.clips if c.kind != kind]
        self.normalize()

    def remove(self, indexes):
        for i in sorted(set(indexes), reverse=True):
            if 0 <= i < len(self.clips):
                del self.clips[i]
        self.normalize()

    def split_at(self, t: float) -> bool:
        """在时间线 t 秒处把一段切成两段；切点太靠边（不足一帧）就不切。

        图片段直接分时长；视频段按入出点切，两段的源区间首尾相接。
        """
        self.apply_material()
        mf = self._min_frame()
        acc = 0.0
        for i, c in enumerate(self.clips):
            span = c.span
            if t < acc + span - 1e-9:
                off = t - acc
                if off < mf - 1e-9 or span - off < mf - 1e-9:
                    return False
                if c.is_video:
                    cut = c.src_in + off
                    right = Clip(c.path, c.dur, c.kind, c.fade, c.xfade,
                                 cut, c.src_out)
                    c.src_out = cut
                else:
                    right = Clip(c.path, span - off, c.kind, c.fade, c.xfade)
                    c.dur = off
                self.clips.insert(i + 1, right)
                self.fit_transitions()
                return True
            acc += span
        return False

    def split_audio(self, k: int, t: float) -> bool:
        """在时间线 t 秒处把第 k 条音频切成两条（裁过头尾剩下的部分照切）。"""
        if not (0 <= k < len(self.audios)):
            return False
        a = self.audios[k]
        if a.gap:
            return False
        off = t - a.start
        if off <= 0.05 or off >= a.dur - 0.05:
            return False
        right = AudioClip(a.path, a.dur - off, a.start + off, a.volume,
                          a.gap, a.gain_db,
                          [[round(p - off, 3), g] for p, g in a.points
                           if p >= off],
                          a.src_in + off)
        a.dur = off
        a.points = clean_env_points([[p, g] for p, g in a.points
                                     if p <= off])
        self.audios.insert(k + 1, right)
        return True

    def remove_audio(self, indexes, ripple: bool = False):
        """删掉几条音频；ripple 时后面的音频整体前移，补上空出来的时间。"""
        rows = [k for k in sorted(set(indexes), reverse=True)
                if 0 <= k < len(self.audios)]
        if not rows:
            return
        gone = [(self.audios[k].start, self.audios[k].dur) for k in rows]
        for k in rows:
            del self.audios[k]
        if ripple:
            for lo, dur in gone:
                for a in self.audios:
                    if a.start >= lo - 1e-6:
                        a.start = max(0.0, a.start - dur)
        if self.narration_path and not any(a.path == self.narration_path
                                          for a in self.audios):
            self.narration_path = ""
            self.narration_dur = 0.0

    def move(self, index: int, delta: int):
        j = index + delta
        if 0 <= index < len(self.clips) and 0 <= j < len(self.clips):
            self.clips[index], self.clips[j] = self.clips[j], self.clips[index]

    def reorder(self, paths_and_durs):
        self.clips = [Clip(p, d) for p, d in paths_and_durs]
        self.normalize()

    def reorder_clips(self, order) -> bool:
        """按现有 Clip 对象重排，保住调色/转场/入出点/片头片尾这些字段。

        order 必须是 0..n-1 的一个排列；对不上就不动，交给老办法重建。
        """
        n = len(self.clips)
        if len(order) != n or sorted(order) != list(range(n)):
            return False
        self.clips = [self.clips[i] for i in order]
        self.normalize()
        return True

    def move_to(self, index: int, pos: float):
        """把第 index 个视频元素挪到时间线上的 pos（秒）附近。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        others = [c for c in self.clips if c is not clip]
        acc, dest = 0.0, len(others)
        for k, c in enumerate(others):
            if pos < acc + c.span / 2:
                dest = k
                break
            acc += c.span
        self.clips.remove(clip)
        self.clips.insert(min(dest, len(self.clips)), clip)
        self.normalize()

    def move_block(self, rows, dest: int) -> bool:
        """把连续选中的一段整体挪到 dest（挪完后的起始下标）。"""
        block = timeline_block_rows(rows)
        if block is None:
            return False
        lo, hi = block
        if not (0 <= lo <= hi < len(self.clips)):
            return False
        picked = self.clips[lo:hi + 1]
        rest = self.clips[:lo] + self.clips[hi + 1:]
        dest = max(0, min(int(dest), len(rest)))
        self.clips = rest[:dest] + picked + rest[dest:]
        self.normalize()
        return True

    def set_cue_start(self, index: int, start: float) -> bool:
        """把第 index 条字幕整条挪到 start 秒（时长不变，夹在片长里）。"""
        if not (0 <= index < len(self.cues)):
            return False
        cue = self.cues[index]
        dur = max(0.0, _doc_num(cue.get("end"), 0.0)
                  - _doc_num(cue.get("start"), 0.0))
        total = self.total()
        start = max(0.0, _doc_num(start, 0.0))
        if total > 0.0:
            start = min(start, max(0.0, total - dur))
        cue["start"] = round(start, 3)
        cue["end"] = round(start + dur, 3)
        return True

    # -- 帧数 ----------------------------------------------------------
    def frame_counts(self) -> list[int]:
        """按累计时间取整，保证总帧数 = round(总时长 * FPS)，不漂移。"""
        self.apply_material()
        counts, acc, prev = [], 0.0, 0
        for c in self.clips:
            acc += c.span
            boundary = int(round(acc * self.fps))
            n = max(1, boundary - prev)
            counts.append(n)
            prev += n
        return counts



# ---------------------------------------------------------------- 工程文件读写

def save_project_doc(doc: dict, path: str) -> None:
    """整份写出去：先写 .tmp 再改名，写到一半断掉也不会毁掉原工程。"""
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise ProjectFileError("write-failed", str(exc)) from exc


def load_project_doc(path: str) -> dict:
    """读成 dict。文件不在 / 不是 JSON / 不像工程，各自报不同的 reason。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise ProjectFileError("missing", path) from exc
    except UnicodeDecodeError as exc:
        raise ProjectFileError("bad-json", str(exc)) from exc
    except ValueError as exc:      # json 解析失败都是 ValueError
        raise ProjectFileError("bad-json", str(exc)) from exc
    except OSError as exc:
        raise ProjectFileError("missing", str(exc)) from exc
    if not isinstance(raw, dict):
        raise ProjectFileError("bad-shape", "工程内容不是一个对象")
    return raw


def save_project(project: "Project", path: str) -> None:
    save_project_doc(project.to_doc(), path)


def load_project(path: str) -> "Project":
    return Project.from_doc(load_project_doc(path))


# ---------------------------------------------------------------- 字幕

class SubtitleError(Exception):
    """字幕读不进来：missing 文件没了 / bad-format 认不出这个格式。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason


def _sub_time(text: str) -> float | None:
    """字幕时间戳 -> 秒。SRT 用逗号毫秒，ASS 用百分秒，点号也收。"""
    if not isinstance(text, str):
        return None
    raw = text.strip().replace(",", ".")
    parts = raw.split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        nums = [float(x) for x in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        nums = [0.0] + nums
    h, m, s = nums
    if min(h, m, s) < 0 or m >= 60 or s >= 60:
        return None
    return round(h * 3600 + m * 60 + s, 3)


def _cue(start: float, end: float, text: str, style: str = "") -> dict:
    return {"start": start, "end": end, "text": text, "style": style}


def _sub_body(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def parse_srt(text: str) -> list[dict]:
    """SRT -> cues。序号可省；多行文字用换行连起来；认不出就报 bad-format。"""
    if not isinstance(text, str):
        raise SubtitleError("bad-format", "字幕内容不是文本")
    cues = []
    for block in re.split(r"\n[ \t]*\n", _sub_body(text)):
        lines = block.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            continue
        at = 0
        if "-->" not in lines[0]:
            at = 1                       # 第一行是序号，时间在下一行
        if at >= len(lines) or "-->" not in lines[at]:
            continue
        left, _, right = lines[at].partition("-->")
        stamps = right.split()
        start, end = _sub_time(left), _sub_time(stamps[0]) if stamps else None
        if start is None or end is None or end <= start:
            continue                     # 时间认不出或者倒着，这条不要
        body = "\n".join(ln.rstrip() for ln in lines[at + 1:]).strip("\n")
        cues.append(_cue(start, end, body))
    if not cues:
        raise SubtitleError("bad-format", "没有一条读得出来的字幕")
    return cues


def _ass_text(text: str) -> str:
    r"""去掉 {\...} 排版指令，把 \N \n 还原成换行、\h 还原成空格。"""
    out = re.sub(r"\{[^}]*\}", "", text)
    out = out.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return out.strip()


ASS_FIELDS = ["layer", "start", "end", "style", "name", "marginl", "marginr",
              "marginv", "effect", "text"]


def parse_ass(text: str) -> list[dict]:
    """ASS/SSA 的 [Events] 段 -> cues（保留样式名，丢排版指令）。"""
    if not isinstance(text, str):
        raise SubtitleError("bad-format", "字幕内容不是文本")
    in_events, fields, cues = False, None, []
    for raw in _sub_body(text).split("\n"):
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("[") and low.endswith("]"):
            in_events = low == "[events]"
            fields = None
            continue
        if not in_events:
            continue
        if low.startswith("format:"):
            fields = [f.strip().lower()
                      for f in line.split(":", 1)[1].split(",")]
            continue
        if not low.startswith("dialogue:"):
            continue                  # Comment: / Picture: 之类不算
        cols = fields or ASS_FIELDS
        parts = line.split(":", 1)[1].split(",", len(cols) - 1)
        if len(parts) < len(cols):
            continue
        row = {k: parts[i].strip() for i, k in enumerate(cols)}
        start, end = _sub_time(row.get("start", "")), _sub_time(row.get("end", ""))
        if start is None or end is None or end <= start:
            continue
        cues.append(_cue(start, end, _ass_text(row.get("text", "")),
                         row.get("style", "")))
    if not cues:
        raise SubtitleError("bad-format", "没有一条 Dialogue 行")
    return cues


def load_subtitles(path: str) -> list[dict]:
    """按扩展名读一份字幕：.srt / .ass / .ssa。"""
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".srt", ".ass", ".ssa"):
        raise SubtitleError("bad-format", "只认 .srt / .ass 字幕文件")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        raise SubtitleError("missing", path)
    text = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise SubtitleError("bad-format", "字幕的编码认不出来（用 UTF-8 存一份）")
    return parse_ass(text) if ext in (".ass", ".ssa") else parse_srt(text)


def _clean_cues(raw_list) -> list:
    """把外来字幕条目洗成干净的一份：坏条目直接丢掉，不炸整个工程。"""
    out = []
    for raw in raw_list or []:
        if not isinstance(raw, dict):
            continue
        start = _doc_num(raw.get("start"), -1.0)
        end = _doc_num(raw.get("end"), -1.0)
        text = raw.get("text")
        if start < 0 or end <= start or not isinstance(text, str):
            continue
        out.append(_cue(start, end, text, str(raw.get("style") or "")))
    return out


def set_subtitles(project, path: str, cues: list):
    """挂一份字幕到工程上（工程只存路径 + 解析结果）。"""
    project.subtitles_path = str(path or "")
    project.cues = _clean_cues(cues)


def clear_subtitles(project):
    """把字幕从工程上摘掉。"""
    project.subtitles_path = ""
    project.cues = []


def set_narration(project, path: str, dur: float, cues=None):
    """把合成好的旁白挂到工程上：一条音频 + 折好行的字幕 + 跟旁白模式。"""
    dur = max(0.0, _doc_num(dur, 0.0))
    project.narration_path = str(path or "")
    project.narration_dur = dur
    if cues is not None:
        project.cues = [] if not cues else tts_engine.fold_cues(cues)
        project.subtitles_path = ""
    found = False
    for a in project.audios:
        if a.path == project.narration_path:
            a.start, a.dur, a.volume = 0.0, dur, 1.0
            found = True
            break
    if not found and project.narration_path:
        project.audios.append(AudioClip(project.narration_path, dur, 0.0, 1.0, gap=False))
    project.mode = "voice"
    project.normalize()


def _ass_time(t: float) -> str:
    """成片时间 → .ass 的 0:00:01.00 这种写法（百分秒）。"""
    cs_all = int(round(max(0.0, float(t)) * 100))
    h, rest = divmod(cs_all, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    r"""字幕正文 → .ass 一行：花括号是排版指令，必须转义，换行写 \N。"""
    t = str(text).replace("{", "\\{").replace("}", "\\}")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    return t.replace("\n", "\\N")


ASS_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding")


def format_ass(cues: list, font: str = "Microsoft YaHei", size: int = 48,
               w: int = WIDTH, h: int = HEIGHT) -> str:
    """把解析好的字幕写成一份 .ass（烧录用）。字号按像素，跟成片同尺寸。"""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {int(w)}",
        f"PlayResY: {int(h)}",
        "",
        "[V4+ Styles]",
        ASS_STYLE_FORMAT,
        ("Style: Default,%s,%d,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
         "0,0,0,0,100,100,0,0,1,2.5,0,2,60,60,40,1" % (font, int(size))),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Text",
    ]
    for c in cues or []:
        lines.append("Dialogue: 0,%s,%s,%s,%s" % (
            _ass_time(c.get("start", 0.0)), _ass_time(c.get("end", 0.0)),
            c.get("style") or "Default", _ass_escape(c.get("text", ""))))
    return "\n".join(lines) + "\n"


def subtitle_px(size: int, height: int) -> int:
    """字号按 1080p 定，实际按画面高度等比缩放（小窗预览才不会字撑满屏）。"""
    return max(8, int(round(float(size) * float(height) / 1080.0)))


SNAP_PX = 8        # 磁吸容差：离得这么近（像素）就贴上去


def snap_value(value: float, targets, tolerance: float):
    """把 value 吸到最近的 target；返回 (吸后的值, 吸到的目标或 None)。"""
    best, best_d = None, max(0.0, float(tolerance))
    for t in targets:
        d = abs(float(t) - value)
        if d <= best_d:
            best, best_d = float(t), d
    return (best if best is not None else value), best


def snap_candidates(project, playhead: float = 0.0, extra=()) -> list:
    """时间线上值得贴的点：首尾、播放头、每个边界、音频与画中画的首尾。"""
    out = [0.0, project.total(), max(0.0, float(playhead))]
    acc = 0.0
    for c in project.clips:
        acc += c.span
        out.append(acc)
    for a in project.audios:
        out.extend([a.start, a.start + a.dur])
    for o in project.overlays:
        out.extend([o.start, o.end])
    out.extend(float(v) for v in extra)
    return sorted({round(v, 3) for v in out if v >= 0.0})


TIMELINE_ZOOM_MIN = 1.0    # 1.0 = 整条片子铺满时间线
TIMELINE_ZOOM_MAX = 64.0
TIMELINE_RULER_H = 18      # 顶部时间刻度条的高度


def timeline_span(total: float, zoom: float) -> float:
    """当前看得见多长一段：整片时长除以缩放倍数（最小到一帧）。"""
    total = max(0.0, _doc_num(total, 0.0))
    z = _doc_num(zoom, 1.0)
    z = max(TIMELINE_ZOOM_MIN, min(TIMELINE_ZOOM_MAX, z))
    if total <= 0.0:
        return 0.0
    return max(1.0 / 60.0, total / z)


def timeline_clamp_start(start: float, total: float, span: float) -> float:
    """把可见窗口的起点夹在 [0, 总长-窗口长] 里；窗口比片子长就从头看。"""
    total = max(0.0, _doc_num(total, 0.0))
    span = max(0.0, _doc_num(span, 0.0))
    if span >= total:
        return 0.0
    return max(0.0, min(_doc_num(start, 0.0), total - span))


def timeline_x(t: float, start: float, span: float, width: float) -> float:
    """时间 → 像素。窗口外会算出负数或超出宽度，画的时候由 Qt 裁掉。"""
    w = max(1.0, float(width))
    sp = span if span > 0.0 else 1.0
    return (float(t) - float(start)) * w / sp


def timeline_time(x: float, start: float, span: float, width: float,
                  total: float) -> float:
    """像素 → 时间，夹在片子范围内（点窗口外的空白不会跑到片外）。"""
    w = max(1.0, float(width))
    sp = span if span > 0.0 else 1.0
    total = max(0.0, _doc_num(total, 0.0))
    return max(0.0, min(total, float(start) + float(x) * sp / w))


def timeline_zoom_start(start: float, span: float, anchor: float,
                        new_span: float, total: float) -> float:
    """缩放时让 anchor 这一刻待在原来的横向位置（鼠标下的点不跑）。"""
    sp = span if span > 0.0 else 1.0
    frac = (float(anchor) - float(start)) / sp
    return timeline_clamp_start(float(anchor) - frac * float(new_span),
                                total, float(new_span))


TIMELINE_TICK_STEPS = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0,
                       30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)


def timeline_tick_label(t: float, step: float) -> str:
    """刻度上的字：不到 1 秒写小数，整分钟写 m:ss，其余写 Ns。"""
    t = max(0.0, _doc_num(t, 0.0))
    if step < 0.999:
        return "%.1fs" % t
    if t >= 60.0:
        return human_time(t)
    return "%ds" % int(round(t))


def timeline_ticks(start: float, span: float, width: float,
                   min_px: float = 64.0) -> list:
    """时间线顶部的刻度：(时间, x, 标签)。步长按像素密度挑，不挤成一团。"""
    span = _doc_num(span, 0.0)
    width = float(width)
    if span <= 0.0 or width <= 0.0:
        return []
    step = TIMELINE_TICK_STEPS[-1]
    for cand in TIMELINE_TICK_STEPS:
        if cand / span * width >= min_px:
            step = cand
            break
    start = max(0.0, _doc_num(start, 0.0))
    first = math.ceil(start / step - 1e-9) * step
    out = []
    t = first
    while t <= start + span + 1e-6 and len(out) < 240:
        out.append((round(t, 3),
                    timeline_x(t, start, span, width),
                    timeline_tick_label(t, step)))
        t += step
    return out


def timeline_block_rows(rows):
    """连续的一段行号（升序去重）；不连续返回 None。"""
    rows = sorted({int(r) for r in rows})
    if not rows or rows[-1] - rows[0] + 1 != len(rows):
        return None
    return rows[0], rows[-1]


def timeline_block_dest(durations, rows, drop_t: float) -> int:
    """整块拖到 drop_t 时，落回序列里的下标（不含被拖的那几段）。"""
    block = timeline_block_rows(rows)
    if block is None or not durations:
        return 0
    lo, hi = block
    rest = [d for i, d in enumerate(durations) if not (lo <= i <= hi)]
    acc, dest = 0.0, len(rest)
    for k, d in enumerate(rest):
        if _doc_num(drop_t, 0.0) < acc + max(0.0, _doc_num(d, 0.0)) / 2.0:
            dest = k
            break
        acc += max(0.0, _doc_num(d, 0.0))
    return max(0, min(dest, len(rest)))


def _write_ass(cues, w: int, h: int, font: str, size: int) -> str:
    """把字幕写成临时 .ass，返回路径（用完由调用方删）。"""
    text = format_ass(cues, font=font, size=size, w=w, h=h)
    fd, path = tempfile.mkstemp(prefix="p2v_sub_", suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def _filter_path(path: str) -> str:
    """滤镜里的路径：反斜杠换正斜杠，冒号转义，整体单引号包住。"""
    return str(path).replace("\\", "/").replace(":", "\\:")


def default_font_dir() -> str:
    """字幕字体默认去系统字体目录找（libass 自己未必能翻到）。"""
    win = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(win, "Fonts")


def ass_filter(path: str, font: str = "Microsoft YaHei", size: int = 48,
               fontsdir: str = "") -> str:
    """一条 subtitles 滤镜：烧录临时 .ass，并强制字体/字号。"""
    out = "subtitles=filename='%s'" % _filter_path(path)
    if fontsdir:
        out += ":fontsdir='%s'" % _filter_path(fontsdir)
    out += ":force_style='FontName=%s,FontSize=%d'" % (font, int(size))
    return out


def project_error_text(exc: "ProjectFileError") -> str:
    """把工程读写出错翻译成一句人话（弹窗正文用）。"""
    detail = str(exc) if str(exc) != exc.reason else ""
    if exc.reason == "missing":
        return tr("文件不在了：") + (detail or tr("（路径没记住）"))
    if exc.reason == "bad-json":
        return tr("不是 JSON，读不出来：") + detail
    if exc.reason == "bad-shape":
        return tr("内容不像本软件的工程：") + detail
    if exc.reason == "newer":
        return tr("这个工程是更新版本的软件存的，先升级软件再打开。")
    if exc.reason == "write-failed":
        return tr("写不进去（磁盘满或者没有权限）：") + detail
    return detail or exc.reason


class AutoSaver:
    """自动保存：改动以后隔 interval 秒写一份，崩溃/断电后下次启动能捞回来。

    只有「保存」或「不保存」才清掉它；窗口标题上那个「有改动」是另一回事。
    """

    def __init__(self, path: str, interval: float = 15.0, clock=time.time):
        self.path = path
        self.interval = float(interval)
        self._clock = clock
        self._dirty_since: float | None = None

    def mark_dirty(self) -> None:
        if self._dirty_since is None:
            self._dirty_since = self._clock()

    def tick(self, project: "Project") -> bool:
        """到点了就写一份；返回这次写没写。"""
        if self._dirty_since is None:
            return False
        now = self._clock()
        if now - self._dirty_since < self.interval:
            return False
        try:
            save_project(project, self.path)
        except ProjectFileError:
            # 写不进去（只读盘之类）就过一会儿再试，别每个 tick 都撞盘
            self._dirty_since = now
            return False
        self._dirty_since = None
        return True

    def clear(self) -> None:
        self._dirty_since = None
        try:
            os.remove(self.path)
        except OSError:
            pass

    @staticmethod
    def restorable(path: str) -> bool:
        """盘上有能读的自动保存吗？读不了就当没有，别拿坏文件烦用户。"""
        try:
            project = load_project(path)
        except ProjectFileError:
            return False
        return bool(project.clips or project.audios)


def set_mode(project: Project, mode: str) -> None:
    project.mode = mode
    if mode == "material":
        project.apply_material()
        return
    if mode == "total":
        project.total_setting = max(MIN_DUR * max(1, project.image_count()),
                                    project.total())
    project.normalize()


# ---------------------------------------------------------------- 出片

def crop_expr(w: int, h: int, focus: str = "center") -> str:
    """裁切铺满时按 focus 决定纵向留住哪一段（人像常常要偏上）。"""
    frac = CROP_FOCUS.get(focus, 0.5)
    if frac == 0.5:
        return f"crop={w}:{h}"
    return f"crop={w}:{h}:(iw-{w})/2:(ih-{h})*{frac:.3f}"


def _motion_chain(chain: str, motion: str, index: int, nf: int,
                  w: int, h: int, fps: int, amount: float) -> str:
    """在 [s{i}] 之后插一段 zoompan，做成缓慢推近/拉远（Ken Burns）。"""
    amt = max(0.0, min(0.5, float(amount)))
    if amt <= 0.001:
        return chain
    if motion == "inout":
        direction = "in" if index % 2 == 0 else "out"
    else:
        direction = motion
    if direction == "out":
        z = f"1+{amt:.4f}*(1-on/{max(1, nf - 1)})"
    else:
        z = f"1+{amt:.4f}*on/{max(1, nf - 1)}"
    return (chain +
            f",zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={w}x{h}:fps={fps}")


def boundary_xfade(clips, i: int, xfade_sec: float):
    """第 i 段与第 i+1 段之间的转场：返回 (名字, 秒数)，硬切返回 None。

    谁开了 xfade 就听谁的：前一段开了用前一段的，否则用后一段的；
    名字空着就是 fade，时长空着跟全局 xfade_sec。
    """
    if not clips or i < 0 or i + 1 >= len(clips):
        return None
    for c in (clips[i], clips[i + 1]):
        if not getattr(c, "xfade", False):
            continue
        name = getattr(c, "xfade_name", "") or "fade"
        if name not in XFADE_NAMES:
            name = "fade"
        dur = getattr(c, "xfade_dur", None)
        if dur is None:
            dur = xfade_sec
        return name, min(XFADE_MAX, max(0.0, float(dur)))
    return None


def _xfade_plan(clips, counts, fps: int, xfade_sec: float):
    """交叉淡化时每条边界两侧各补一半帧，输出总帧数守恒。

    返回 (counts2, overlaps)，overlaps[i] 是第 i 与 i+1 段的重叠帧数（偶数，0=硬切）。
    """
    counts2 = list(counts)
    if not clips or len(clips) < 2:
        return counts2, []
    overlaps = [0] * (len(clips) - 1)
    for i in range(len(clips) - 1):
        hit = boundary_xfade(clips, i, xfade_sec)
        if not hit:
            continue
        want = max(1, int(round(hit[1] * fps / 2.0)))
        room = min(counts2[i], counts2[i + 1]) // 2
        half = min(want, room)
        if half < 1:
            continue
        overlaps[i] = 2 * half
        counts2[i] += half
        counts2[i + 1] += half
    return counts2, overlaps


def xfade_short(clips, counts, fps: int, xfade_sec: float) -> list[dict]:
    """哪几处转场塞不下：相邻两段都得比转场的两倍还长，否则提示而不是出坏片。"""
    out = []
    if not clips or len(counts) < 2:
        return out
    for i in range(min(len(clips), len(counts)) - 1):
        hit = boundary_xfade(clips, i, xfade_sec)
        if not hit:
            continue
        name, want = hit
        need = 2.0 * want
        a = counts[i] / float(fps)
        b = counts[i + 1] / float(fps)
        if a <= need or b <= need:
            out.append({"at": i, "name": name, "want": want, "need": need,
                        "a": a, "b": b})
    return out


def xfade_error_text(items) -> str:
    """把 xfade_short 的第一条翻成一句人话（导出前的提示）。"""
    it = items[0]
    return ("第 %d 段和第 %d 段之间放了 %.2f 秒转场，可这两段只有 "
            "%.2f / %.2f 秒；转场要求相邻两段各超过 %.2f 秒。"
            "把转场调短，或者把这两段拉长。"
            % (it["at"] + 1, it["at"] + 2, it["want"], it["a"], it["b"],
               it["need"]))


def grab_cover(video_path: str, out_path: str, at: float,
               total: float = 0.0) -> str:
    """从成片里截一帧当封面（jpg），at 是成片时间轴的秒数。"""
    if not video_path or not os.path.exists(video_path):
        raise RenderError(tr("成片不在，封面截不出来"))
    ss = clamp_cover_time(at, total) if total > 0 else max(0.0, _doc_num(at, 0.0))
    if total > 0:
        ss = min(ss, max(0.0, float(total) - 0.05))   # 正片最后一帧取不到，退一点
    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-ss", "%.3f" % ss, "-i", video_path, "-frames:v", "1",
           "-q:v", "2", out_path]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL,
                       creationflags=CREATION_NO_WINDOW)
    if p.returncode != 0 or not os.path.exists(out_path):
        raise RenderError(tr("封面截不出来：{msg}", msg=(p.stderr or "")[-200:]))
    return out_path
# ---------------------------------------------------------------- 预览合成
# 预览不再「渲一条整片再播」，而是按时间直接画出这一帧：
# 图片解码一次进内存，缩放/铺底/淡入淡出/推近拉远/转场/画中画/字幕
# 都在这里用 QPainter 拼出来。时间映射与 build_graph 完全一致，
# 只有 12 个转场是近似实现（ffmpeg 的 xfade 没有逐像素公开公式）。

def _preview_cache_dir() -> str:
    path = os.path.join(tempfile.gettempdir(), "p2v_preview")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def _plain_cue(text: str) -> list[str]:
    r"""字幕正文去掉 ASS 覆盖标签，按换行拆开（\N 与真换行都算）。"""
    out = []
    for line in (text or "").replace("\\N", "\n").replace("\r", "").split("\n"):
        clean, depth = [], 0
        for ch in line:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            elif depth == 0:
                clean.append(ch)
        out.append("".join(clean).strip())
    return [ln for ln in out if ln] if any(ln for ln in out) else [""]


class VideoFrameReader:
    """逐帧读视频：一条 ffmpeg 管道顺序读，跳太远才重开，别再一帧起一个进程。"""

    def __init__(self, path: str, start: float, w: int, h: int, fps: int):
        from PyQt6.QtGui import QImage
        self._QImage = QImage
        self.path = path
        self.start = max(0.0, float(start))
        self.w, self.h = max(2, int(w)), max(2, int(h))
        self.fps = max(1, int(fps))
        self.index = -1
        self.proc = None

    def close(self):
        proc, self.proc = self.proc, None
        self.index = -1
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _open(self, index: int):
        self.close()
        t = self.start + index / float(self.fps)
        vf = ("scale=%d:%d:force_original_aspect_ratio=increase,"
              "crop=%d:%d" % (self.w, self.h, self.w, self.h))
        cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-ss", "%.3f" % t, "-i", self.path, "-vf", vf,
               "-f", "rawvideo", "-pix_fmt", "rgb32", "-"]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=CREATION_NO_WINDOW)
        self.index = index

    def _read(self, need: int):
        proc = self.proc
        if proc is None or proc.stdout is None:
            return None
        try:
            buf = proc.stdout.read(need)
        except (OSError, ValueError):
            return None
        if not buf or len(buf) < need:
            return None
        return buf

    def frame(self, index: int):
        index = max(0, int(index))
        if self.proc is None or index < self.index or index > self.index + 90:
            self._open(index)
        need = self.w * self.h * 4
        while self.index < index:
            if self._read(need) is None:
                return self._QImage()
            self.index += 1
        data = self._read(need)
        if data is None:
            return self._QImage()
        self.index += 1
        img = self._QImage(data, self.w, self.h,
                           self._QImage.Format.Format_RGB32)
        return img.copy()


class Scene:
    """一个可以随时画任意时刻画面的预览引擎（不落盘、不编码）。"""

    def __init__(self, clips, counts, fps, opts, overlays=None, cues=None,
                 cache_dir=""):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QPainter,
                                 QPainterPath, QPen)
        self._Qt = Qt
        self._QColor = QColor
        self._QFont = QFont
        self._QImage = QImage
        self._QPainter = QPainter
        self._QPainterPath = QPainterPath
        self._QPen = QPen
        self._QBrush = QBrush

        self.cache_dir = cache_dir or _preview_cache_dir()
        self._imgs: dict = {}
        self._bases: dict = {}
        self._video_frames: dict = {}
        self._video_order: list = []
        self._readers: dict = {}
        self._geom = None
        self.total = 0.0
        self.reconfigure(clips, counts, fps, opts, overlays, cues)

    def reconfigure(self, clips, counts, fps, opts, overlays=None, cues=None):
        """换时间线/参数但留住解码好的图：改一处参数不用重新读盘。"""
        self.clips = [c.copy() for c in (clips or [])]
        self.fps = max(1, int(fps))
        self.w = max(2, int(opts.get("w", WIDTH)))
        self.h = max(2, int(opts.get("h", HEIGHT)))
        self.fit = opts.get("fit", "own")
        self.bg_blur = bool(opts.get("bg_blur", True))
        self.pad_color = opts.get("pad_color", "#000000")
        self.bg_path = opts.get("bg_path", "")
        self.transition = opts.get("transition", "none")
        self.transition_sec = float(opts.get("transition_sec", 0.3))
        self.xfade_sec = float(opts.get("xfade_sec", self.transition_sec))
        self.motion = opts.get("motion", "none")
        self.crop_focus = opts.get("crop_focus", "center")
        self.cues = [dict(c) for c in (cues or [])]
        self.subtitle_font = opts.get("subtitle_font", "Microsoft YaHei")
        self.subtitle_size = int(opts.get("subtitle_size", 48))

        geom = (self.w, self.h, self.fit, self.bg_blur, self.pad_color,
                self.bg_path, self.crop_focus)
        if geom != self._geom:
            self._bases.clear()        # 画面几何变了，底图得重铺
        self._geom = geom

        counts = list(counts or [])
        if len(counts) < len(self.clips):
            counts += [1] * (len(self.clips) - len(counts))
        total_frames = sum(counts)
        self.total = total_frames / float(self.fps) if total_frames else 0.0
        self.counts2, self.overlaps = _xfade_plan(
            self.clips, counts, self.fps, self.xfade_sec)
        self.segs = [nf / float(self.fps) for nf in self.counts2]
        self.starts = []
        acc = 0.0
        for i, seg in enumerate(self.segs):
            self.starts.append(acc)
            d_next = (self.overlaps[i] / float(self.fps)
                      if i < len(self.overlaps) else 0.0)
            acc += seg - d_next
        specs: list[dict] = []
        for item in overlays or []:
            if isinstance(item, dict):
                specs.append(dict(item))
            else:                      # 直接传 Overlay 对象（界面走这条）
                specs.extend(overlay_specs([item], 0, self.total))
        self.overlays = specs

    # -- 画面 -----------------------------------------------------------
    def _blank(self, color="#000000"):
        img = self._QImage(self.w, self.h,
                           self._QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(self._QColor(color))
        return img

    def _load(self, path: str):
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = 0.0
        key = (path, stamp)
        img = self._imgs.get(key)
        if img is not None:
            return img
        img = self._QImage(path)
        if img.isNull():
            img = self._blank("#000000")
        if len(self._imgs) > 64:
            self._imgs.pop(next(iter(self._imgs)))
        self._imgs[key] = img
        return img

    def _cover(self, img, focus: str | None = None):
        sw, sh = img.width(), img.height()
        if sw <= 0 or sh <= 0:
            return self._blank()
        k = max(self.w / float(sw), self.h / float(sh))
        nw, nh = max(1, int(round(sw * k))), max(1, int(round(sh * k)))
        sc = img.scaled(nw, nh, self._Qt.AspectRatioMode.IgnoreAspectRatio,
                        self._Qt.TransformationMode.SmoothTransformation)
        frac = CROP_FOCUS.get(focus or self.crop_focus, 0.5)
        x = max(0, (nw - self.w) // 2)
        y = max(0, int(round((nh - self.h) * frac)))
        return sc.copy(x, y, self.w, self.h)

    def _contain(self, img):
        """ffmpeg 的 force_original_aspect_ratio=decrease：小图不放大。"""
        sw, sh = img.width(), img.height()
        if sw <= 0 or sh <= 0:
            return self._blank()
        k = min(1.0, min(self.w / float(sw), self.h / float(sh)))
        if k >= 0.999:
            return img
        return img.scaled(max(1, int(round(sw * k))),
                          max(1, int(round(sh * k))),
                          self._Qt.AspectRatioMode.IgnoreAspectRatio,
                          self._Qt.TransformationMode.SmoothTransformation)

    def _blur(self, img):
        """gblur 的近似：缩到 1/12 再放大回来，够糊也够快。"""
        sw = max(2, self.w // 12)
        sh = max(2, self.h // 12)
        small = img.scaled(sw, sh, self._Qt.AspectRatioMode.IgnoreAspectRatio,
                           self._Qt.TransformationMode.SmoothTransformation)
        return small.scaled(self.w, self.h,
                            self._Qt.AspectRatioMode.IgnoreAspectRatio,
                            self._Qt.TransformationMode.SmoothTransformation)

    def _draw_centered(self, canvas, img, bg_blur=False, darken=0.0):
        qp = self._QPainter(canvas)
        if bg_blur:
            qp.drawImage(0, 0, self._blur(canvas))
        if darken > 0:
            qp.fillRect(0, 0, self.w, self.h,
                        self._QColor(0, 0, 0, int(255 * min(1.0, darken))))
        qp.drawImage(max(0, (self.w - img.width()) // 2),
                     max(0, (self.h - img.height()) // 2), img)
        qp.end()

    def _still_from_video(self, path: str, at: float):
        """视频素材取一帧（片头片尾 / 视频背景 / 视频画中画）。"""
        key = (path, round(max(0.0, at), 3))
        hit = self._video_frames.get(key)
        if hit is not None:
            return hit
        cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-ss", "%.3f" % max(0.0, at), "-i", path, "-frames:v", "1",
               "-f", "image2pipe", "-vcodec", "png", "-"]
        try:
            p = subprocess.run(cmd, capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=20,
                               creationflags=CREATION_NO_WINDOW)
            img = self._QImage.fromData(p.stdout, "PNG")
        except (subprocess.SubprocessError, OSError):
            img = self._QImage()
        if img.isNull():
            img = self._blank()
        if len(self._video_order) > 240:
            old = self._video_order.pop(0)
            self._video_frames.pop(old, None)
        self._video_frames[key] = img
        self._video_order.append(key)
        return img

    def _compose_base(self, clip):
        """一段的静态底图：铺底 + 缩放 + 调色（调色交给 ffmpeg 一次算准）。"""
        if clip.is_video:
            return self._cover(self._still_from_video(clip.path, clip.src_in))
        src = self._load(clip.path)
        if self.fit == "cover":
            base = self._cover(src)
        elif self.fit == "color":
            base = self._blank(self.pad_color)
            self._draw_centered(base, self._contain(src))
        elif self.fit in BG_MODES:
            bg = self._blank()
            if self.bg_path and os.path.exists(self.bg_path):
                if self.bg_path.lower().endswith(tuple(MEDIA_EXTS)):
                    bg = self._cover(self._still_from_video(self.bg_path, 0.0))
                else:
                    bg = self._cover(self._load(self.bg_path))
            base = bg.copy()
            self._draw_centered(base, self._contain(src))
        else:                                    # own：自己铺底（可模糊）
            base = self._cover(src)
            self._draw_centered(base, self._contain(src),
                                bg_blur=self.bg_blur,
                                darken=0.08 if self.bg_blur else 0.0)
        chain = clip.color_chain()
        if chain:
            base = self._baked_color(base, chain, clip.path)
        return base

    def _baked_color(self, base, chain: str, path: str):
        """调色交给 ffmpeg 现算一张（结果按参数缓存，之后一直复用）。"""
        key = "%s|%s|%s|%d|%d" % (chain, os.path.abspath(path),
                                  self.fit, self.w, self.h)
        key = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
        out = os.path.join(self.cache_dir, "color_%s.png" % key)
        if os.path.exists(out):
            done = self._QImage(out)
            if not done.isNull():
                return done
        src = os.path.join(self.cache_dir, "color_%s_src.png" % key)
        try:
            base.save(src, "PNG")
            cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                   "-i", src, "-vf", chain, "-frames:v", "1", out]
            subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=30, creationflags=CREATION_NO_WINDOW)
            done = self._QImage(out)
            if not done.isNull():
                return done
        except (OSError, subprocess.SubprocessError):
            pass
        return base

    def warm(self, index: int, span: int = 2) -> int:
        """空闲时把附近几段的底图先铺好，播放切到那段不卡。"""
        done = 0
        for i in range(max(0, index), min(len(self.clips), index + span + 1)):
            if self.clips[i].is_video:
                continue                  # 视频段自己有解码管道，不铺底图
            if self._base_key(i) not in self._bases:
                self._base(i)
                done += 1
        return done

    def _base_key(self, i: int):
        c = self.clips[i]
        try:
            stamp = os.path.getmtime(c.path)
        except OSError:
            stamp = 0.0
        return (c.path, stamp, c.kind, c.src_in, round(c.brightness, 3),
                round(c.contrast, 3), round(c.saturation, 3), c.lut,
                self.w, self.h, self.fit, self.bg_blur, self.pad_color,
                self.bg_path, self.crop_focus)

    def _base(self, i: int):
        key = self._base_key(i)
        hit = self._bases.get(key)
        if hit is None:
            if len(self._bases) > 96:
                self._bases.pop(next(iter(self._bases)))
            hit = self._compose_base(self.clips[i])
            self._bases[key] = hit
        return hit

    def _zoom(self, i: int, lt: float) -> float:
        """Ken Burns：跟 zoompan 的 on/(nf-1) 对齐。"""
        amt = MOTION_AMOUNT
        seg = max(1e-6, self.segs[i])
        nf = max(1, self.counts2[i])
        on = int(round(max(0.0, min(1.0, lt / seg)) * (nf - 1)))
        if (self.motion not in MOTIONS or self.motion == "none" or nf <= 1
                or self.clips[i].is_video):
            return 1.0
        direction = ("in" if i % 2 == 0 else "out") \
            if self.motion == "inout" else self.motion
        if direction == "out":
            return 1.0 + amt * (1.0 - on / float(max(1, nf - 1)))
        return 1.0 + amt * (on / float(max(1, nf - 1)))

    def _reader_for(self, path: str, start: float):
        key = (path, round(float(start), 3), self.w, self.h, self.fps)
        reader = self._readers.get(key)
        if reader is None:
            if len(self._readers) >= 4:
                old = next(iter(self._readers))
                self._readers.pop(old).close()
            reader = VideoFrameReader(path, start, self.w, self.h, self.fps)
            self._readers[key] = reader
        return reader

    def _video_picture(self, clip, lt: float):
        reader = self._reader_for(clip.path, clip.src_in)
        img = reader.frame(int(max(0.0, lt) * self.fps))
        if img.isNull():
            return self._blank()
        return img

    def close(self):
        for reader in self._readers.values():
            reader.close()
        self._readers.clear()

    def _content(self, i: int, lt: float):
        """第 i 段在段内 lt 秒时的画面（含推拉与淡入淡出）。"""
        clip = self.clips[i]
        if clip.is_video:
            base = self._video_picture(clip, lt)
        else:
            base = self._base(i)
            z = self._zoom(i, lt)
            if z > 1.0001:
                nw = max(2, int(round(self.w * z)))
                nh = max(2, int(round(self.h * z)))
                canvas = base.scaled(
                    nw, nh, self._Qt.AspectRatioMode.IgnoreAspectRatio,
                    self._Qt.TransformationMode.SmoothTransformation)
                x = max(0, (nw - self.w) // 2)
                y = max(0, (nh - self.h) // 2)
                base = canvas.copy(x, y, self.w, self.h)
        seg = self.segs[i]
        want_fade = bool(clip.fade)
        if not clip.is_video and self.transition == "fade":
            want_fade = True
        td = min(max(0.0, self.transition_sec), seg / 2.0)
        alpha = 0.0
        if want_fade and td > 0.02 and seg > 0.1:
            if lt < td:
                alpha = 1.0 - lt / td
            elif lt > seg - td:
                alpha = min(1.0, (lt - (seg - td)) / td)
        if alpha > 0.001:
            out = base.copy()
            qp = self._QPainter(out)
            qp.fillRect(0, 0, self.w, self.h,
                        self._QColor(0, 0, 0, int(255 * alpha)))
            qp.end()
            return out
        return base

    # -- 转场（近似 xfade 的 12 个名字）--------------------------------
    def _pixelated(self, img, blocks: int):
        bw, bh = max(1, self.w // blocks), max(1, self.h // blocks)
        small = img.scaled(bw, bh, self._Qt.AspectRatioMode.IgnoreAspectRatio,
                           self._Qt.TransformationMode.FastTransformation)
        return small.scaled(self.w, self.h,
                            self._Qt.AspectRatioMode.IgnoreAspectRatio,
                            self._Qt.TransformationMode.FastTransformation)

    def _blend(self, a, b, name: str, p: float):
        p = max(0.0, min(1.0, p))
        out = self._blank()
        qp = self._QPainter(out)
        if name == "fadeblack":
            if p < 0.5:
                qp.drawImage(0, 0, a)
                qp.fillRect(0, 0, self.w, self.h,
                            self._QColor(0, 0, 0, int(255 * min(1.0, p * 2))))
            else:
                qp.drawImage(0, 0, b)
                qp.fillRect(0, 0, self.w, self.h,
                            self._QColor(0, 0, 0,
                                         int(255 * max(0.0, (1 - p) * 2))))
        elif name in ("wipeleft", "wiperight", "wipeup", "wipedown"):
            qp.drawImage(0, 0, a)
            if name == "wipeleft":
                rect = (int(self.w * (1 - p)), 0, int(self.w * p), self.h)
            elif name == "wiperight":
                rect = (0, 0, int(self.w * p), self.h)
            elif name == "wipeup":
                rect = (0, int(self.h * (1 - p)), self.w, int(self.h * p))
            else:
                rect = (0, 0, self.w, int(self.h * p))
            qp.setClipRect(*rect)
            qp.drawImage(0, 0, b)
            qp.setClipping(False)
        elif name in ("slideleft", "slideright"):
            dx = int(self.w * p) * (-1 if name == "slideleft" else 1)
            qp.drawImage(dx, 0, a)
            qp.drawImage((self.w if name == "slideleft" else -self.w) + dx,
                         0, b)
        elif name in ("circleopen", "circleclose"):
            radius = (self.w ** 2 + self.h ** 2) ** 0.5 / 2.0
            r = radius * (p if name == "circleopen" else (1 - p))
            path = self._QPainterPath()
            path.addEllipse(self.w / 2.0 - r, self.h / 2.0 - r, r * 2, r * 2)
            if name == "circleopen":
                qp.drawImage(0, 0, a)
                qp.setClipPath(path)
                qp.drawImage(0, 0, b)
            else:
                qp.drawImage(0, 0, b)
                qp.setClipPath(path)
                qp.drawImage(0, 0, a)
            qp.setClipping(False)
        elif name == "radial":
            qp.drawImage(0, 0, a)
            path = self._QPainterPath()
            path.moveTo(self.w / 2.0, self.h / 2.0)
            path.arcTo(-self.w, -self.h, self.w * 3, self.h * 3, 90, -360 * p)
            path.closeSubpath()
            qp.setClipPath(path)
            qp.drawImage(0, 0, b)
            qp.setClipping(False)
        elif name == "pixelize":
            blocks = max(2, int(round(2 + 14 * (1 - abs(2 * p - 1)))))
            qp.drawImage(0, 0, self._pixelated(a, blocks))
            qp.setOpacity(p)
            qp.drawImage(0, 0, self._pixelated(b, blocks))
        else:                                      # fade 及未知名字
            qp.drawImage(0, 0, a)
            qp.setOpacity(p)
            qp.drawImage(0, 0, b)
        qp.end()
        return out

    # -- 整帧 -----------------------------------------------------------
    def _segment_at(self, t: float):
        if not self.clips:
            return -1, 0.0
        t = max(0.0, min(t, max(0.0, self.total - 1e-6)))
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= t:
                lo = mid
            else:
                hi = mid - 1
        return lo, t - self.starts[lo]

    def frame_at(self, t: float):
        if not self.clips:
            return self._blank()
        i, lt = self._segment_at(t)
        frame = self._content(i, lt)
        d_prev = (self.overlaps[i - 1] / float(self.fps)
                  if i > 0 and i - 1 < len(self.overlaps) else 0.0)
        if d_prev > 1e-6 and lt < d_prev:
            hit = boundary_xfade(self.clips, i - 1, self.xfade_sec)
            name = hit[0] if hit else "fade"
            prev = self._content(i - 1, t - self.starts[i - 1])
            frame = self._blend(prev, frame, name, lt / d_prev)
        if self.overlays:
            frame = self._draw_overlays(frame.copy(), t)
        if self.cues:
            active = [c for c in self.cues
                      if _doc_num(c.get("start"), -1) <= t
                      < _doc_num(c.get("end"), -1)]
            if active:
                frame = self._draw_subtitles(frame.copy(), active)
        return frame

    def _draw_overlays(self, frame, t: float):
        qp = self._QPainter(frame)
        for ov in self.overlays:
            if not (ov["start"] <= t <= ov["end"]):
                continue
            tw = max(2, int(round(self.w * ov["scale"] / 100.0)))
            if ov["path"].lower().endswith(tuple(MEDIA_EXTS)):
                img = self._reader_for(ov["path"], 0.0).frame(
                    int(max(0.0, t - ov["start"]) * self.fps))
            else:
                img = self._load(ov["path"])
            if img.isNull():
                continue
            th = max(2, int(round(img.height() * tw / max(1, img.width()))))
            x = (self.w - tw) * ov["ax"] + self.w * ov["dx"] / 100.0
            y = (self.h - th) * ov["ay"] + self.h * ov["dy"] / 100.0
            qp.setOpacity(max(0.0, min(1.0, ov["opacity"] / 100.0)))
            qp.drawImage(int(x), int(y), img.scaled(
                tw, th, self._Qt.AspectRatioMode.IgnoreAspectRatio,
                self._Qt.TransformationMode.SmoothTransformation))
        qp.end()
        return frame

    def _draw_subtitles(self, frame, cues):
        qp = self._QPainter(frame)
        qp.setRenderHint(self._QPainter.RenderHint.Antialiasing, True)
        font = self._QFont(self.subtitle_font)
        font.setPixelSize(max(8, int(self.subtitle_size)))
        qp.setFont(font)
        line_h = max(10, int(self.subtitle_size * 1.25))
        bottom = self.h - 40
        for cue in cues:
            lines = _plain_cue(cue.get("text", ""))
            total_h = line_h * len(lines)
            y = bottom - total_h
            for line in lines:
                metrics = qp.fontMetrics()
                tw = metrics.horizontalAdvance(line)
                path = self._QPainterPath()
                path.addText((self.w - tw) / 2.0, y + line_h * 0.82, font, line)
                qp.setPen(self._QPen(self._QColor("#000000"),
                                     max(1.0, self.subtitle_size * 0.06)))
                qp.drawPath(path)
                qp.fillPath(path, self._QColor("#ffffff"))
                y += line_h
        qp.end()
        return frame


def _sec(value: float) -> str:
    """0 就写 0（跟老滤镜串一致），其余保留三位。"""
    return "0" if abs(value) < 5e-4 else f"{value:.3f}"


def build_graph(counts: list[int], total: float, w: int = WIDTH, h: int = HEIGHT,
                *, fps: int = FPS, fit: str = "own", fade_in: float = FADE_IN,
                fade_out: float = FADE_OUT, transition: str = "none",
                transition_sec: float = 0.3, bg_blur: bool = True,
                pad_color: str = "#000000", bg_index: int | None = None,
                audio_index: int | None = None, motion: str = "none",
                motion_amount: float = MOTION_AMOUNT, volume: float = 1.0,
                crop_focus: str = "center", music_start: float = 0.0,
                clips=None, timeline=None, extras=None,
                xfade_sec: float = 0.3, subtitle_file: str = "",
                subtitle_font: str = "Microsoft YaHei", subtitle_size: int = 48,
                subtitle_fonts: str = "", overlays=None,
                bgm_gain: float = 0.0, bgm_points=None) -> str:
    """一条 filtergraph：逐元素切片 → 拼接（可交叉淡化）→ 音频 → 总长。

    clips: Clip 列表（含片头片尾）；不传就按「全是图片」
    timeline: [(是不是图片段, 输出时长)]，决定背景音乐在哪些段出声
    extras: [{"index","start","dur","volume"}]，叠加的额外音频
    fit: own 原图铺底（bg_blur 决定模糊不模糊）/ color 纯色留白（pad_color）
         / image·video 自定义背景（输入下标 bg_index）+ 原图完整居中
         / cover 裁切铺满；片头片尾视频一律铺满
    transition: none 硬切；fade 每张首尾淡黑（图片）
    motion: none 静帧；in 缓慢推近；out 缓慢拉远；inout 一张推近一张拉远
    volume: 背景音乐音量倍数（1.0 = 原样）
    crop_focus: 裁切铺满时留住 center / top / bottom
    subtitle_file: 已经写好的 .ass，挂在成片最后一环烧进去（空 = 不加字幕）
    overlays: overlay_specs() 的产物，画中画压在成片上、字幕之前
    bgm_gain / bgm_points: 背景音乐这一段的增益（dB）与音量包络点
    """
    K = len(counts)
    if K <= 0:
        return ""
    if audio_index is None:
        audio_index = K
    video_flags = [c.is_video for c in clips] if clips else [False] * K
    counts2, overlaps = _xfade_plan(clips, counts, fps, xfade_sec)
    segs = [nf / fps for nf in counts2]
    pad_hex = "0x" + pad_color.lstrip("#")
    starts, acc = [], 0.0
    for seg in segs:
        starts.append(acc)
        acc += seg

    parts = []
    if fit in BG_MODES and bg_index is not None:
        bg = (f"[{bg_index}:v]scale={w}:{h}:force_original_aspect_ratio=increase:"
              f"in_range=full:out_range=tv,{crop_expr(w, h, crop_focus)},setsar=1,"
              f"fps={fps},format=yuv420p")
        if bg_blur:
            bg += ",gblur=sigma=40"
        parts.append(bg + f",split={K}" + "".join(f"[bgx{i}]" for i in range(K)))

    for i, nf in enumerate(counts2):
        seg = segs[i]
        if video_flags[i]:
            pre = ""
            src = clips[i] if clips else None
            if src is not None and src.is_video and (src.src_in > 0
                                                     or src.src_out is not None):
                start = src.src_in
                end = (src.src_out if src.src_out is not None
                       else src.src_in + src.span)
                # 交叉淡化要往两边多借半截，借的帧得从源里真取出来，
                # 否则 trim 卡在出点上、段短一截，成片比计划短
                head = overlaps[i - 1] // 2 if i > 0 and i - 1 < len(overlaps) else 0
                tail = overlaps[i] // 2 if i < len(overlaps) else 0
                if head:
                    start = max(0.0, start - min(head / fps, start))
                if tail:
                    room = max(0.0, src.dur - end)
                    end += min(tail / fps, room)
                pre = (f"trim=start={start:.4f}:end={end:.4f},"
                       f"setpts=PTS-STARTPTS,")
            parts.append(
                f"[{i}:v]{pre}scale={w}:{h}:force_original_aspect_ratio=increase:"
                f"in_range=full:out_range=tv,{crop_expr(w, h, crop_focus)}[s{i}]")
        elif fit == "cover":
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase:"
                f"in_range=full:out_range=tv,{crop_expr(w, h, crop_focus)}[s{i}]")
        elif fit == "color":
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                f"in_range=full:out_range=tv,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={pad_hex}[s{i}]")
        elif fit in BG_MODES and bg_index is not None:
            parts.append(
                f"[bgx{i}]trim=start={starts[i]:.4f}:"
                f"end={starts[i] + seg + 0.5:.4f},setpts=PTS-STARTPTS[bgs{i}]")
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                f"in_range=full:out_range=tv[fgs{i}]")
            parts.append(
                f"[bgs{i}][fgs{i}]overlay=(W-w)/2:(H-h)/2:shortest=1[s{i}]")
        else:  # own：这张图自己当背景（可选模糊）
            parts.append(f"[{i}:v]split=2[bg{i}][fg{i}]")
            bg = (f"[bg{i}]scale={w}:{h}:force_original_aspect_ratio=increase:"
                  f"in_range=full:out_range=tv,{crop_expr(w, h, crop_focus)}")
            if bg_blur:
                bg += ",gblur=sigma=40,eq=brightness=-0.08"
            parts.append(bg + f"[bgz{i}]")
            parts.append(
                f"[fg{i}]scale={w}:{h}:force_original_aspect_ratio=decrease:"
                f"in_range=full:out_range=tv[fgz{i}]")
            parts.append(f"[bgz{i}][fgz{i}]overlay=(W-w)/2:(H-h)/2[s{i}]")
        chain = (f"[s{i}]setsar=1,fps={fps},format=yuv420p,"
                 f"trim=start_frame=0:end_frame={nf},setpts=PTS-STARTPTS")
        if clips and i < len(clips) and clips[i].has_color:
            # 调色接在缩放 / 铺底之后：整段连背景一起调，跟看到的一致
            chain += clips[i].color_chain()
        if (motion in MOTIONS and motion != "none" and nf > 1
                and not video_flags[i]):
            chain = _motion_chain(chain, motion, i, nf, w, h, fps,
                                  motion_amount)
        seg = segs[i]
        td = min(max(0.0, float(transition_sec)), seg / 2.0)
        if clips and i < len(clips):
            want_fade = bool(clips[i].fade)
            if not video_flags[i] and transition == "fade":
                want_fade = True
        else:
            want_fade = transition == "fade"
        if want_fade and td > 0.02 and seg > 0.1:
            chain += (f",fade=t=in:st=0:d={td:.3f}"
                      f",fade=t=out:st={max(0.0, seg - td):.3f}:d={td:.3f}")
        if any(overlaps):
            chain += ",settb=AVTB"
        parts.append(chain + f"[v{i}]")

    prev = "[v0]"
    acc = segs[0]
    for i in range(1, K):
        d = overlaps[i - 1] / fps if overlaps else 0.0
        if d > 0:
            off = max(0.0, acc - d)
            hit = boundary_xfade(clips, i - 1, xfade_sec)
            name = hit[0] if hit else "fade"
            parts.append(f"{prev}[v{i}]xfade=transition={name}:duration={d:.3f}:"
                         f"offset={off:.3f}[m{i}]")
            acc += segs[i] - d
        else:
            parts.append(f"{prev}[v{i}]concat=n=2:v=1:a=0[m{i}]")
            acc += segs[i]
        prev = f"[m{i}]"
    for k, ov in enumerate(overlays or []):
        tw = max(2, even(int(round(w * ov["scale"] / 100.0))))
        op = min(1.0, max(0.0, ov["opacity"] / 100.0))
        dx = w * ov["dx"] / 100.0
        dy = h * ov["dy"] / 100.0
        parts.append(
            f"[{ov['index']}:v]scale={tw}:-2,format=rgba,"
            f"colorchannelmixer=aa={op:.3f},fps={fps},"
            f"setpts=PTS-STARTPTS+{ov['start']:.3f}/TB[ov{k}]")
        parts.append(
            f"{prev}[ov{k}]overlay=x=(W-w)*{ov['ax']:.3f}{dx:+.1f}:"
            f"y=(H-h)*{ov['ay']:.3f}{dy:+.1f}:"
            f"enable='between(t,{ov['start']:.3f},{ov['end']:.3f})':"
            f"eof_action=pass[o{k}]")
        prev = f"[o{k}]"
    if subtitle_file:
        sub = ass_filter(subtitle_file, subtitle_font, subtitle_size,
                         subtitle_fonts or default_font_dir())
        parts.append(f"{prev}{COLOR_TAG},{sub}[vout]")
    else:
        parts.append(f"{prev}{COLOR_TAG}[vout]")

    # ---------------- 音频 ----------------
    mstart = max(0.0, float(music_start))
    spans = timeline if timeline else [(True, total)]
    split = any(not is_img for is_img, _ in spans)

    if split:
        labels, times, idx = [], [], 0
        pos = mstart
        run = 0.0
        for is_img, dur in spans:
            if is_img:
                parts.append(f"[{audio_index}:a]atrim=start={pos:.3f}:"
                             f"end={pos + dur:.3f},asetpts=N/SR/TB[bgs{idx}]")
                pos += dur
            else:
                parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                             f"atrim=0:{dur:.3f},asetpts=N/SR/TB[bgs{idx}]")
            times.append((run, run + dur, is_img))
            run += dur
            labels.append(f"[bgs{idx}]")
            idx += 1
        parts.append("".join(labels) + f"concat=n={idx}:v=0:a=1[bgm0]")
        head, pre = "[bgm0]", "anull"
        img_start = next((a for a, b, ok in times if ok), 0.0)
        img_end = max((b for a, b, ok in times if ok), default=img_start)
    else:
        head = f"[{audio_index}:a]"
        pre = (f"atrim=start={mstart:.3f}:"
               f"end={mstart + total:.3f},asetpts=N/SR/TB")
        img_start, img_end = 0.0, total

    filters = []
    if abs(volume - 1.0) > 0.005:
        filters.append(f"volume={max(0.0, float(volume)):.3f}")
    if fade_in > 0:
        filters.append(f"afade=t=in:st={_sec(img_start)}:d={fade_in:.3f}")
    span = max(0.0, img_end - img_start)
    fade = min(max(0.0, float(fade_out)), max(0.0, span - 0.05))
    if fade > 0.05:
        filters.append(
            f"afade=t=out:st={max(img_start, img_end - fade):.3f}:d={fade:.3f}")
    audio = head + pre
    if filters:
        audio += "," + ",".join(filters)
    fx = audio_fx(bgm_gain, bgm_points)
    if fx:
        audio += "," + fx

    if extras:
        # 主音频先落到一个标签上，才接得进 amix（否则把整条链塞进 input 列表就废了）
        main_label = "[bgmain]"
        parts.append(audio + main_label)
        mix = [main_label]
        for k, ex in enumerate(extras):
            si = max(0.0, float(ex.get("src_in", 0.0)))
            ch = (f"[{ex['index']}:a]atrim=start={si:.3f}:"
                  f"end={si + ex['dur']:.3f},asetpts=N/SR/TB")
            vol = float(ex.get("volume", 1.0))
            if abs(vol - 1.0) > 0.005:
                ch += f",volume={max(0.0, vol):.3f}"
            ms = int(round(max(0.0, float(ex["start"])) * 1000))
            ch += f",adelay={ms}|{ms}"
            # 包络在 adelay 之后接，表达式里的 t 已经是输出时间轴
            ex_fx = audio_fx(ex.get("gain_db", 0.0), ex.get("points"),
                             t0=max(0.0, float(ex["start"])))
            if ex_fx:
                ch += "," + ex_fx
            ch += f"[ax{k}]"
            parts.append(ch)
            mix.append(f"[ax{k}]")
        parts.append("".join(mix) +
                     f"amix=inputs={len(mix)}:normalize=0:duration=longest,"
                     f"apad,atrim=0:{total:.3f}[aout]")
    else:
        parts.append(audio + "[aout]")
    return ";".join(parts)


def build_audio_graph(total: float, *, timeline=None, audio_index: int = 0,
                      volume: float = 1.0, fade_in: float = FADE_IN,
                      fade_out: float = FADE_OUT, music_start: float = 0.0,
                      extras=None, bgm_gain: float = 0.0,
                      bgm_points=None) -> str:
    """只出音轨的 filtergraph（预览用：不碰视频，几毫秒就能混完）。

    规则跟 build_graph 的音频段一致：跟音乐 时片头片尾静音、
    整段音量、首尾淡入淡出、每段增益与包络、多音轨 amix。
    """
    mstart = max(0.0, float(music_start))
    spans = timeline if timeline else [(True, total)]
    split = any(not is_img for is_img, _ in spans)
    parts = []
    if split:
        labels, times, idx = [], [], 0
        pos = mstart
        run = 0.0
        for is_img, dur in spans:
            if is_img:
                parts.append(f"[{audio_index}:a]atrim=start={pos:.3f}:"
                             f"end={pos + dur:.3f},asetpts=N/SR/TB[bgs{idx}]")
                pos += dur
            else:
                parts.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                             f"atrim=0:{dur:.3f},asetpts=N/SR/TB[bgs{idx}]")
            times.append((run, run + dur, is_img))
            run += dur
            labels.append(f"[bgs{idx}]")
            idx += 1
        parts.append("".join(labels) + f"concat=n={idx}:v=0:a=1[bgm0]")
        head, pre = "[bgm0]", "anull"
        img_start = next((a for a, b, ok in times if ok), 0.0)
        img_end = max((b for a, b, ok in times if ok), default=img_start)
    else:
        head = f"[{audio_index}:a]"
        pre = (f"atrim=start={mstart:.3f}:"
               f"end={mstart + total:.3f},asetpts=N/SR/TB")
        img_start, img_end = 0.0, total

    filters = []
    if abs(volume - 1.0) > 0.005:
        filters.append(f"volume={max(0.0, float(volume)):.3f}")
    if fade_in > 0:
        filters.append(f"afade=t=in:st={_sec(img_start)}:d={fade_in:.3f}")
    span = max(0.0, img_end - img_start)
    fade = min(max(0.0, float(fade_out)), max(0.0, span - 0.05))
    if fade > 0.05:
        filters.append(
            f"afade=t=out:st={max(img_start, img_end - fade):.3f}:d={fade:.3f}")
    audio = head + pre
    if filters:
        audio += "," + ",".join(filters)
    fx = audio_fx(bgm_gain, bgm_points)
    if fx:
        audio += "," + fx

    if extras:
        main_label = "[bgmain]"
        parts.append(audio + main_label)
        mix = [main_label]
        for k, ex in enumerate(extras):
            si = max(0.0, float(ex.get("src_in", 0.0)))
            ch = (f"[{ex['index']}:a]atrim=start={si:.3f}:"
                  f"end={si + ex['dur']:.3f},asetpts=N/SR/TB")
            vol = float(ex.get("volume", 1.0))
            if abs(vol - 1.0) > 0.005:
                ch += f",volume={max(0.0, vol):.3f}"
            ms = int(round(max(0.0, float(ex["start"])) * 1000))
            ch += f",adelay={ms}|{ms}"
            ex_fx = audio_fx(ex.get("gain_db", 0.0), ex.get("points"),
                             t0=max(0.0, float(ex["start"])))
            if ex_fx:
                ch += "," + ex_fx
            ch += f"[ax{k}]"
            parts.append(ch)
            mix.append(f"[ax{k}]")
        parts.append("".join(mix) +
                     f"amix=inputs={len(mix)}:normalize=0:duration=longest,"
                     f"apad,atrim=0:{total:.3f}[aout]")
    else:
        parts.append(audio + "[aout]")
    return ";".join(parts)


def audio_timeline(clips, counts, fps: int, total: float,
                   xfade_sec: float = 0.3) -> list:
    """每段在成片里占的区间（跟 render 里算音画同步用的那份一致）。"""
    counts2, overlaps = _xfade_plan(clips, counts, fps, xfade_sec)
    segs = [nf / float(fps) for nf in counts2]
    spans, acc = [], 0.0
    for i, seg in enumerate(segs):
        d_next = overlaps[i] / float(fps) if i < len(segs) - 1 else 0.0
        spans.append((acc, acc + seg - d_next))
        acc += seg - (d_next if i < len(segs) - 1 else 0.0)
    timeline = [(not c.is_video, max(0.05, spans[i][1] - spans[i][0]))
                for i, c in enumerate(clips)]
    if abs(sum(d for _, d in timeline) - total) > 0.05:
        timeline = [(not c.is_video, counts[i] / float(fps))
                    for i, c in enumerate(clips)]
    return timeline


def render_audio_mix(clips, counts, audio, out_path, *, fps: int = FPS,
                     volume: float = 1.0, fade_in: float = FADE_IN,
                     fade_out: float = FADE_OUT, music_start: float = 0.0,
                     xfade_sec: float = 0.3, ab: str = "192k") -> str:
    """只把音频混成一个 .m4a（预览的主时钟，几毫秒到几百毫秒）。"""
    if not counts:
        raise RenderError(tr("还没有素材"))
    total = sum(counts) / float(fps)
    timeline = audio_timeline(clips, counts, fps, total, xfade_sec)
    items = _audio_items(audio)
    bgm = next((a for a in items if a.get("gap")), None)
    extras = [a for a in items if not a.get("gap")]
    cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error"]
    if bgm and bgm.get("path"):
        adur = bgm.get("dur") or probe_duration(bgm["path"])
        adur = max(0.0, adur - max(0.0, float(music_start)))
        need = sum(d for is_img, d in timeline if is_img)
        if adur > 0 and adur + 0.05 < need:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", bgm["path"]]
    else:
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000"]
    extra_specs = []
    for k, ex in enumerate(extras):
        cmd += ["-i", ex["path"]]
        dur = ex.get("dur") or probe_duration(ex["path"])
        extra_specs.append({"index": 1 + k, "start": ex.get("start", 0.0),
                            "dur": max(0.05, dur),
                            "src_in": max(0.0, float(ex.get("src_in") or 0.0)),
                            "volume": ex.get("volume", 1.0),
                            "gain_db": ex.get("gain_db", 0.0),
                            "points": ex.get("points")})
    graph = build_audio_graph(
        total, timeline=timeline, audio_index=0, volume=volume,
        fade_in=fade_in, fade_out=fade_out, music_start=music_start,
        extras=extra_specs,
        bgm_gain=bgm.get("gain_db", 0.0) if bgm else 0.0,
        bgm_points=bgm.get("points") if bgm else None)
    cmd += ["-filter_complex", graph, "-map", "[aout]", "-vn",
            "-c:a", "aac", "-b:a", (ab if isinstance(ab, str) and ab else "192k"),
            "-ar", "48000", "-ac", "2", "-t", f"{total:.3f}", out_path]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL,
                       creationflags=CREATION_NO_WINDOW)
    if p.returncode != 0 or not os.path.exists(out_path):
        raise RenderError((p.stderr or "")[-300:] or tr("音频混音失败"))
    return out_path


def _audio_items(audio) -> list[dict]:
    """把 render 的 audio 参数统一成 [{path,start,dur,volume,gap}]。"""
    if not audio:
        return []
    if isinstance(audio, str):
        return [{"path": audio, "start": 0.0,
                 "dur": probe_duration(audio), "volume": 1.0, "gap": True}]
    items = []
    for a in audio:
        if isinstance(a, dict):
            items.append(a)
        elif isinstance(a, AudioClip):
            items.append({"path": a.path, "start": a.start, "dur": a.dur,
                          "src_in": a.src_in, "volume": a.volume,
                          "gap": a.gap, "gain_db": a.gain_db,
                          "points": list(a.points)})
        else:  # 路径字符串
            items.append({"path": a, "start": 0.0,
                          "dur": probe_duration(a), "volume": 1.0, "gap": True})
    return items


def render(clips, counts, audio, out_path, *, w=WIDTH, h=HEIGHT,
           crf=20, preset="veryfast", fps=FPS, fit="own", fade_in=FADE_IN,
           fade_out=FADE_OUT, transition="none", transition_sec=0.3,
           bg_blur=True, pad_color="#000000", bg_path="", motion="none",
           volume=1.0, crop_focus="center", music_start=0.0, xfade_sec=0.3,
           subtitles=None, subtitle_font="Microsoft YaHei",
           subtitle_size=48, subtitle_fonts="", ab="192k", overlays=None,
           on_progress=None, proc_holder=None) -> str:
    """一条 filtergraph 渲完；on_progress(0..1) 回调。

    audio 可以是路径、AudioClip 列表或等价的 dict 列表。
    subtitles 给一份 cues（解析好的字幕）就直接烧进成片；留空不烧。
    proc_holder 给一个 dict 时，把 ffmpeg 进程塞进 proc_holder["proc"]，
    外面就能 terminate() 取消这次渲染。
    """
    if not counts:
        raise RenderError(tr("还没有素材"))
    for c, nf in zip(clips, counts):
        if c.is_video and not os.path.exists(c.path):
            raise RenderError(tr("这个文件读不出来：{name}",
                                 name=os.path.basename(c.path)))
    bad = [c.path for c in clips if not c.is_video and not image_ok(c.path)]
    if bad:
        raise RenderError(tr("这个文件读不出来：{name}",
                             name=os.path.basename(bad[0])))
    for c in clips:
        if c.lut and not os.path.exists(c.lut):
            raise RenderError(tr("调色用的滤镜文件不在了：{name}",
                                 name=os.path.basename(c.lut)))
    short = xfade_short(clips, counts, fps, xfade_sec)
    if short:
        raise RenderError(xfade_error_text(short))
    counts2, overlaps = _xfade_plan(clips, counts, fps, xfade_sec)
    total = sum(counts) / fps
    add_bg = fit in BG_MODES
    if add_bg:
        if not bg_path or not os.path.exists(bg_path):
            raise RenderError(tr("选的背景文件不存在，去设置里重新选一个"))
        if fit == "image" and not bg_path.lower().endswith(tuple(IMG_EXTS)):
            raise RenderError(tr("背景图格式不支持，用 jpg / png / webp / bmp"))

    # 时间线：每段在成片里占的区间（交叉淡化把边界挤掉一块）
    segs = [nf / fps for nf in counts2]
    spans, acc = [], 0.0
    for i, seg in enumerate(segs):
        d_prev = overlaps[i - 1] / fps if i > 0 else 0.0
        d_next = overlaps[i] / fps if i < len(segs) - 1 else 0.0
        spans.append((acc, acc + seg - d_next))
        acc += seg - (d_next if i < len(segs) - 1 else 0.0)
    timeline = [(not c.is_video, max(0.05, spans[i][1] - spans[i][0]))
                for i, c in enumerate(clips)]
    if abs(sum(d for _, d in timeline) - total) > 0.05:   # 别让音画漂
        timeline = [(not c.is_video, counts[i] / fps)
                    for i, c in enumerate(clips)]

    items = _audio_items(audio)
    bgm = next((a for a in items if a.get("gap")), None)
    extras = [a for a in items if not a.get("gap")]
    bgm_gain = bgm.get("gain_db", 0.0) if bgm else 0.0
    bgm_points = bgm.get("points") if bgm else None
    for ov in overlays or []:
        if not ov.path or not os.path.exists(ov.path):
            raise RenderError(tr("叠加的素材读不出来：{name}",
                                 name=os.path.basename(ov.path) or "?"))
        if (not ov.path.lower().endswith(tuple(MEDIA_EXTS))
                and not image_ok(ov.path)):
            raise RenderError(tr("叠加的素材读不出来：{name}",
                                 name=os.path.basename(ov.path)))

    sub_file = ""
    if subtitles:
        sub_file = _write_ass(subtitles, w, h, subtitle_font, subtitle_size)
    sub_kw = dict(subtitle_file=sub_file, subtitle_font=subtitle_font,
                  subtitle_size=subtitle_size, subtitle_fonts=subtitle_fonts)
    ov_base = len(counts) + (1 if add_bg else 0) + 1 + len(extras)
    ov_specs = overlay_specs(overlays or [], ov_base, total)

    graph = build_graph(counts, total, w, h, fps=fps, fit=fit,
                        fade_in=fade_in, fade_out=fade_out,
                        transition=transition, transition_sec=transition_sec,
                        bg_blur=bg_blur, pad_color=pad_color,
                        bg_index=(len(counts) if add_bg else None),
                        audio_index=len(counts) + (1 if add_bg else 0),
                        motion=motion, volume=volume,
                        crop_focus=crop_focus, music_start=music_start,
                        clips=clips, timeline=timeline,
                        extras=None, xfade_sec=xfade_sec, overlays=ov_specs,
                        bgm_gain=bgm_gain, bgm_points=bgm_points,
                        **sub_kw)
    cmd = [FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"]
    for c, nf in zip(clips, counts2):
        if c.is_video:
            natural = probe_duration(c.path)
            if natural > 0 and nf / fps > natural + 0.05:
                cmd += ["-stream_loop", "-1"]   # 要放得比原片长就循环
            cmd += ["-i", c.path]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t",
                    f"{nf / fps + 0.2:.3f}", "-i", c.path]
    if add_bg:
        if fit == "video":
            cmd += ["-stream_loop", "-1", "-i", bg_path]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t",
                    f"{total + 1.0:.3f}", "-i", bg_path]

    audio_index = len(counts) + (1 if add_bg else 0)
    need = sum(d for is_img, d in timeline if is_img)
    if bgm and bgm.get("path"):
        adur = bgm.get("dur") or probe_duration(bgm["path"])
        adur = max(0.0, adur - max(0.0, float(music_start)))
        if adur > 0 and adur + 0.05 < need:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", bgm["path"]]
    else:
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000"]

    extra_specs = []
    for k, ex in enumerate(extras):
        idx = audio_index + 1 + k
        cmd += ["-i", ex["path"]]
        dur = ex.get("dur") or probe_duration(ex["path"])
        extra_specs.append({"index": idx, "start": ex.get("start", 0.0),
                            "dur": max(0.05, dur),
                            "src_in": max(0.0, float(ex.get("src_in") or 0.0)),
                            "volume": ex.get("volume", 1.0),
                            "gain_db": ex.get("gain_db", 0.0),
                            "points": ex.get("points")})
    for ov in ov_specs:
        span = max(0.1, ov["end"] - ov["start"]) + 0.5
        if ov["path"].lower().endswith(tuple(MEDIA_EXTS)):
            cmd += ["-stream_loop", "-1", "-t", f"{span:.3f}",
                    "-i", ov["path"]]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t",
                    f"{span:.3f}", "-i", ov["path"]]
    if extra_specs:
        graph = build_graph(counts, total, w, h, fps=fps, fit=fit,
                            fade_in=fade_in, fade_out=fade_out,
                            transition=transition,
                            transition_sec=transition_sec,
                            bg_blur=bg_blur, pad_color=pad_color,
                            bg_index=(len(counts) if add_bg else None),
                            audio_index=audio_index, motion=motion,
                            volume=volume, crop_focus=crop_focus,
                            music_start=music_start, clips=clips,
                            timeline=timeline, extras=extra_specs,
                            xfade_sec=xfade_sec, overlays=ov_specs,
                            bgm_gain=bgm_gain, bgm_points=bgm_points,
                            **sub_kw)

    graph_file = None
    if len(graph) > 6000:
        fd, graph_file = tempfile.mkstemp(prefix="p2v_graph_", suffix=".txt")
        os.close(fd)
        with open(graph_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(graph)
        cmd += [graph_file_option(), graph_file]
    else:
        cmd += ["-filter_complex", graph]

    cmd += ["-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-profile:v", "high", "-r", str(fps),
            "-c:a", "aac", "-b:a", (ab if isinstance(ab, str) and ab else "192k"),
            "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", "-shortest", out_path]

    tail: list[str] = []
    ok = False
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=CREATION_NO_WINDOW)
        if proc_holder is not None:
            proc_holder["proc"] = proc
        for raw in proc.stdout:
            line = raw.strip()
            if line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                if on_progress and total > 0 and us >= 0:
                    on_progress(min(0.999, us / 1e6 / total))
            elif line and ("=" not in line):
                tail.append(line)
                if len(tail) > 12:
                    tail.pop(0)
        proc.stdout.close()
        rc = proc.wait()
        if rc != 0:
            raise RenderError("\n".join(tail[-8:]) or tr("ffmpeg 退出码 {rc}", rc=rc))
        ok = True
        return out_path
    finally:
        for tmp in (graph_file, sub_file):
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        if not ok and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass



# ---------------------------------------------------------------- 命令行

def cli_export(argv: list[str]) -> int:
    if missing_tools():
        print(tr("没找到 ffmpeg，正在下载（约 120 MB，只下这一次）…"),
              file=sys.stderr)

        def _cli_note(text: str, pct: float = 0.0):
            print("\r%s %3d%%" % (text, int(pct * 100)),
                  end="", file=sys.stderr, flush=True)

        try:
            components.install("ffmpeg", progress=_cli_note)
        except components.ComponentError as e:
            print("\n" + e.message, file=sys.stderr)
            return 2
        print("", file=sys.stderr)
        components.prepend_path()
        if missing_tools():
            print(tr("ffmpeg 装好了但还是找不到，重开一次窗口试试"),
                  file=sys.stderr)
            return 2
    import argparse
    ap = argparse.ArgumentParser(description=tr("图片和视频 + 音乐合成视频（无窗口）"))
    ap.add_argument("--export", required=True, help=tr("输出 mp4 路径"))
    ap.add_argument("--audio",
                    help=tr("本地音视频文件，或抖音 / YouTube 等视频链接"))
    ap.add_argument("--bili", help=tr("B 站链接或 BV 号（同 --audio）"))
    ap.add_argument("--total", type=float, help=tr("每段素材的秒数（默认均分音乐）"))
    ap.add_argument("--keep-audio", action="store_true",
                    help=tr("保留取下来的音频文件"))
    ap.add_argument("--fps", type=int, default=30, help=tr("帧率：30 或 60"))
    ap.add_argument("--fit", choices=list(FIT_MODES) + list(FIT_LEGACY),
                    default="own",
                    help=tr("铺底：原图 / 纯色 / 背景图 / 背景视频 / 裁切"))
    ap.add_argument("--ratio", choices=[r for r in RATIOS if r != "自定义"],
                    default="16:9", help=tr("画面比例（默认按短边 1080 算尺寸）"))
    ap.add_argument("--width", type=int, default=None,
                    help=tr("成片宽度（不给就按比例）"))
    ap.add_argument("--height", type=int, default=None,
                    help=tr("成片高度（不给就按比例）"))
    ap.add_argument("--bg", default="", help=tr("自定义背景图片或视频"))
    ap.add_argument("--pad-color", default="#000000",
                    help=tr("纯色留白的颜色，如 #ffffff"))
    ap.add_argument("--no-bg-blur", action="store_true", help=tr("背景不模糊"))
    ap.add_argument("--transition", choices=("none", "fade"), default="none",
                    help=tr("片段转场：硬切 / 淡入淡出"))
    ap.add_argument("--motion", choices=list(MOTIONS), default="none",
                    help=tr("画面动效：静止 / 缓慢推近 / 缓慢拉远 / 交替推拉"))
    ap.add_argument("--crop-focus", choices=list(CROP_FOCUS), default="center",
                    help=tr("裁切铺满时留住哪里：居中 / 偏上 / 偏下"))
    ap.add_argument("--volume", type=int, default=100, metavar="0-200",
                    help=tr("背景音乐音量百分比（默认 100）"))
    ap.add_argument("--music-start", type=float, default=0.0,
                    help=tr("背景音乐从第几秒开始用（默认 0）"))
    ap.add_argument("--music-len", type=float, default=0.0,
                    help=tr("背景音乐用多长，0 = 一直用到音乐结尾"))
    ap.add_argument("images", nargs="+")
    a = ap.parse_args(argv)

    pw, ph = preset_size(a.ratio) or (1920, 1080)
    st = Settings()
    set_locale(st["lang"])
    st.update(fps=a.fps, fit=FIT_LEGACY.get(a.fit, a.fit), ratio=a.ratio,
              width=a.width or pw, height=a.height or ph,
              transition=a.transition, bg_path=a.bg, pad_color=a.pad_color,
              bg_blur=not a.no_bg_blur, motion=a.motion,
              music_volume=max(0.0, min(2.0, a.volume / 100.0)),
              music_start=max(0.0, a.music_start),
              music_len=max(0.0, a.music_len),
              crop_focus=a.crop_focus)
    w, h = st.size()
    opts = dict(w=w, h=h, fps=int(st["fps"]), fit=st["fit"], crf=st.crf(),
                bg_blur=bool(st["bg_blur"]), pad_color=st["pad_color"],
                bg_path=st["bg_path"],
                transition=st["transition"],
                transition_sec=float(st["transition_sec"]),
                motion=st["motion"], volume=float(st["music_volume"]),
                music_start=float(st["music_start"]),
                crop_focus=st["crop_focus"],
                fade_in=float(st["fade_in"]), fade_out=float(st["fade_out"]))

    from bili_audio import BiliError
    audio = None
    tmp_audio = None
    src = a.bili or a.audio
    if src and kind_of(src) == "local":
        audio = src
    elif src:
        print(tr("[1/3] 取音轨：{url}", url=src))
        try:
            if kind_of(src) == "bili":
                from bili_audio import bili_audio
                tmp_audio = bili_audio(src)
            else:
                tmp_audio = fetch_audio(
                    src, os.path.join(tempfile.gettempdir(),
                                      f"p2v_bgm_{os.getpid()}.m4a"))
        except BiliError as e:
            print(tr("          失败：{msg}", msg=bili_message(e)),
                  file=sys.stderr)
            return 2
        except SourceError as e:
            print(tr("          失败：{msg}", msg=e.message), file=sys.stderr)
            return 2
        audio = tmp_audio
        print(f"          -> {tmp_audio}  "
              f"{human_time(probe_duration(tmp_audio))}")

    pr = Project(fps=int(st["fps"]))
    pr.add_images(a.images, dur=a.total)
    if a.total:
        pr.mode = "total"
        pr.total_setting = max(MIN_DUR * pr.image_count(),
                               a.total * pr.image_count())
    if audio:
        pr.audio_path = audio
        pr.audio_dur = probe_duration(audio)
        pr.audio_start = float(st["music_start"])
        pr.audio_len = float(st["music_len"])
        pr.normalize()
    print(tr("[2/3] {n} 段素材，总长 {total}s，{durs}",
             n=len(pr.clips), total=f"{pr.total():.2f}",
             durs=" + ".join(f"{c.dur:.2f}" for c in pr.clips)))
    out_dir = os.path.dirname(os.path.abspath(a.export)) or "."
    os.makedirs(out_dir, exist_ok=True)
    print(tr("[3/3] 渲染 {w}x{h} {fps} 帧/秒 {fit}…",
             w=w, h=h, fps=opts["fps"], fit=fit_label(st["fit"])))
    t0 = time.perf_counter()
    last = [-1]

    def prog(p):
        pct = int(p * 100)
        if pct != last[0] and pct % 10 == 0:
            last[0] = pct
            print(f"          {pct}%")

    render(pr.clips, pr.frame_counts(), pr.audios, a.export, **opts,
           on_progress=prog)
    print(tr("完成：{path}  ({secs}s, {mb} MB)", path=a.export,
             secs=f"{time.perf_counter() - t0:.1f}",
             mb=f"{os.path.getsize(a.export) / 1048576:.1f}"))
    if tmp_audio and not a.keep_audio:
        try:
            os.remove(tmp_audio)
        except OSError:
            pass
    return 0


# ---------------------------------------------------------------- 窗口

def run_gui() -> int:
    from PyQt6.QtCore import (QEvent, QEventLoop, QItemSelectionModel,
                              QLibraryInfo, QPoint, QPointF, QRect, QRectF,
                              QSize, Qt, QThread, QTimer, QTranslator, QUrl,
                              pyqtSignal)
    from PyQt6.QtGui import (QActionGroup, QBrush, QColor, QConicalGradient, QFont,
                            QFontDatabase, QIcon,
                            QImageReader, QKeySequence, QLinearGradient,
                            QPainter, QPainterPath, QPalette, QPen, QPixmap,
                            QPolygonF, QShortcut)
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    from PyQt6.QtWidgets import (
        QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup,
        QCheckBox, QColorDialog,
        QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
        QFontComboBox,
        QFormLayout, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
        QListWidget,
        QListWidgetItem, QMainWindow, QMenu, QProgressBar,
        QPlainTextEdit, QPushButton, QRadioButton, QScrollBar, QSizePolicy,
        QSlider,
        QSpinBox, QStyle, QStyleOptionSlider,
        QTextEdit,
        QSplitter, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget)
    from bili_audio import BiliError, bili_audio

    # ------------------------------------------------------------ 自绘边框
    # Windows 默认那圈蓝边框 + 白色标题栏跟软件配色对不上，所以全部窗口
    # 自己画：标题栏、按钮、拖动、拉伸都自己来。命中测试借 Win32 的
    # WM_NCHITTEST（见 hit_test），所以贴边分屏、双击最大化还是系统行为。
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:                      # noqa: BLE001
        ctypes = None
        wintypes = None

    HT_CLIENT = 1        # 客户区（按钮自己要收点击）
    HT_CAPTION = 2       # 标题栏（系统负责拖动/贴边/双击）

    def _win_icon(size: int = 18) -> QIcon:
        path = asset_path("icon.ico")
        return QIcon(path) if path else QIcon()

    def _screen_point(lparam: int) -> tuple[int, int]:
        x = ctypes.c_short(lparam & 0xFFFF).value
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
        return x, y

    _qt_translators = []

    def apply_qt_translator(code: str) -> None:
        """界面语言换了，Qt 自带控件的文案（文件框、右键菜单）也跟着换。"""
        app_ = QApplication.instance()
        if app_ is None:
            return
        for t in _qt_translators:
            app_.removeTranslator(t)
        _qt_translators.clear()
        name = {"zh-CN": "zh_CN", "zh-TW": "zh_TW",
                "ja": "ja", "ko": "ko"}.get(code)
        if not name:                      # 英文就是 Qt 的原始语言，不用装
            return
        base = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        qt_tr = QTranslator(app_)
        if qt_tr.load("qtbase_" + name, base):
            app_.installTranslator(qt_tr)
            _qt_translators.append(qt_tr)

    def hit_test(win, bar, event_type, message, *, resize: bool):
        """WM_NCHITTEST：边缘给系统拉伸，标题栏给系统当标题栏。

        返回 None 表示这条消息不归我管（交回 Qt）。
        """
        if wintypes is None or event_type != b"windows_generic_MSG":
            return None
        try:
            msg = wintypes.MSG.from_address(int(message))
        except Exception:                  # noqa: BLE001
            return None
        if msg.message != 0x0084:           # WM_NCHITTEST
            return None
        ratio = win.devicePixelRatioF() or 1.0
        gx, gy = _screen_point(msg.lParam)
        gx, gy = gx / ratio, gy / ratio     # 系统给的是物理像素
        r = win.geometry()
        x, y = gx - r.x(), gy - r.y()
        w, h = r.width(), r.height()
        if resize and not win.is_maxed() and not win.isFullScreen():
            b = 6.0
            left, right = x < b, x >= w - b
            top, bottom = y < b, y >= h - b
            if left and top:
                return 13                 # HTTOPLEFT
            if right and top:
                return 14                 # HTTOPRIGHT
            if left and bottom:
                return 16                 # HTBOTTOMLEFT
            if right and bottom:
                return 17                 # HTBOTTOMRIGHT
            if left:
                return 10                 # HTLEFT
            if right:
                return 11                 # HTRIGHT
            if top:
                return 12                 # HTTOP
            if bottom:
                return 15                 # HTBOTTOM
        if bar is not None and bar.isVisible():
            t = bar.mapTo(win, bar.rect().topLeft())
            if (t.x() <= x < t.x() + bar.width()
                    and t.y() <= y < t.y() + bar.height()):
                local = bar.mapFrom(win, QPoint(int(x), int(y)))
                for btn in bar.buttons():
                    if btn.isVisible() and btn.geometry().contains(local):
                        return HT_CLIENT   # 标题栏上的按钮照旧点得动
                return HT_CAPTION
        return HT_CLIENT

    class TitleBar(QWidget):
        """自绘标题栏：图标 + 标题 + 最小化 / 最大化 / 关闭。"""

        def __init__(self, win, title: str = "", *, maximize: bool = False):
            super().__init__(win)
            self.setObjectName("titlebar")
            self.setFixedHeight(34)
            self.win = win
            self._drag = None
            row = QHBoxLayout(self)
            row.setContentsMargins(10, 0, 6, 0)
            row.setSpacing(8)
            self.lbl_icon = QLabel()
            ico = _win_icon()
            if ico.isNull():
                self.lbl_icon.hide()
            else:
                self.lbl_icon.setPixmap(ico.pixmap(18, 18))
            row.addWidget(self.lbl_icon)
            self.lbl_title = QLabel(title)
            self.lbl_title.setObjectName("wintitle")
            row.addWidget(self.lbl_title)
            row.addStretch(1)
            self.btn_min = self._button("–", tr("最小化"))
            self.btn_min.clicked.connect(win.showMinimized)
            self.btn_max = self._button("□", tr("最大化"))
            self.btn_max.clicked.connect(self.toggle_max)
            self.btn_close = self._button("✕", tr("关闭"))
            self.btn_close.setObjectName("winclose")
            self.btn_close.clicked.connect(win.close)
            for b in (self.btn_min, self.btn_max, self.btn_close):
                row.addWidget(b)
            if not maximize:
                self.btn_min.hide()
                self.btn_max.hide()

        @staticmethod
        def _button(text: str, tip: str) -> QPushButton:
            b = QPushButton(text)
            b.setObjectName("winbtn")
            b.setFixedSize(32, 24)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.ArrowCursor)
            return b

        def buttons(self):
            return (self.btn_min, self.btn_max, self.btn_close)

        def set_title(self, text: str):
            self.lbl_title.setText(text)

        def sync_max(self):
            if not self.btn_max.isVisible():
                return
            maxed = self.win.is_maxed()
            self.btn_max.setText("❐" if maxed else "□")
            self.btn_max.setToolTip(tr("还原") if maxed else tr("最大化"))

        def toggle_max(self):
            self.win.toggle_maximum()

        # 系统命中测试生效时这些事件收不到；收得到就自己拖，双保险
        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._drag = (ev.globalPosition().toPoint()
                              - self.win.frameGeometry().topLeft())
                ev.accept()

        def mouseMoveEvent(self, ev):
            if (self._drag is not None
                    and ev.buttons() & Qt.MouseButton.LeftButton):
                self.win.move(ev.globalPosition().toPoint() - self._drag)
                ev.accept()

        def mouseReleaseEvent(self, ev):
            self._drag = None
            ev.accept()

        def mouseDoubleClickEvent(self, ev):
            if self.btn_max.isVisible():
                self.toggle_max()
            ev.accept()

    class FramelessDialog(QDialog):
        """自己画的对话框：自绘标题栏、能拖、Esc 关闭。"""

        def __init__(self, parent=None, title: str = "", *,
                     maximize: bool = False):
            super().__init__(parent)
            self.setWindowTitle(title)          # 任务栏 / 读屏还能读到
            self.setObjectName("frame")         # 配 QSS 那一圈细边
            self.setWindowIcon(_win_icon(32))
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowType.FramelessWindowHint)
            self.titlebar = TitleBar(self, title, maximize=maximize)
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            outer.addWidget(self.titlebar)
            body = QWidget()
            self.body = QVBoxLayout(body)
            self.body.setContentsMargins(12, 10, 12, 12)
            self.body.setSpacing(10)
            outer.addWidget(body, 1)

        def nativeEvent(self, event_type, message):
            bar = getattr(self, "titlebar", None)
            try:
                hit = hit_test(self, bar, event_type, message, resize=False)
            except Exception:             # noqa: BLE001
                return False, 0   # 别调 super()：PyQt6 在这里调会崩掉整个进程
            if hit is None:
                return False, 0   # 别调 super()：PyQt6 在这里调会崩掉整个进程
            return True, hit

    class MsgBox(FramelessDialog):
        """自己画的消息框（替掉 QMessageBox，边框和软件一个样）。"""

        GLYPH = {"info": "ⓘ", "warn": "⚠", "crit": "✕",
                 "ask": "?"}

        def __init__(self, parent, title: str, text: str, actions,
                     icon: str = "info"):
            super().__init__(parent, title)
            self._picked = -1
            row = QHBoxLayout()
            row.setSpacing(12)
            glyph = QLabel(self.GLYPH.get(icon, "ⓘ"))
            glyph.setObjectName("msgicon")
            glyph.setFixedWidth(24)
            glyph.setAlignment(Qt.AlignmentFlag.AlignTop
                               | Qt.AlignmentFlag.AlignHCenter)
            row.addWidget(glyph)
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(lbl, 1)
            self.body.addLayout(row)
            btns = QHBoxLayout()
            btns.addStretch(1)
            for i, (label, style) in enumerate(actions):
                b = QPushButton(label)
                b.clicked.connect(
                    lambda _c=False, idx=i: self._pick(idx))
                if style == "primary":
                    b.setDefault(True)
                elif style == "danger":
                    b.setProperty("danger", True)
                btns.addWidget(b)
            self.body.addSpacing(2)
            self.body.addLayout(btns)
            self.setMinimumWidth(400)
            self.setMaximumWidth(720)

        def _pick(self, index: int):
            self._picked = index
            self.accept()

        @classmethod
        def choose(cls, parent, title: str, text: str, actions,
                   icon: str = "info") -> int:
            box = cls(parent, title, text, actions, icon=icon)
            box.exec()
            return box._picked

        @classmethod
        def info(cls, parent, title: str, text: str):
            cls.choose(parent, title, text, [(tr("好"), "primary")], "info")

        @classmethod
        def warn(cls, parent, title: str, text: str):
            cls.choose(parent, title, text, [(tr("好"), "primary")], "warn")

        @classmethod
        def crit(cls, parent, title: str, text: str):
            cls.choose(parent, title, text, [(tr("好"), "primary")], "crit")

        @classmethod
        def ask(cls, parent, title: str, text: str, yes: str, no: str,
                *, danger: bool = False) -> bool:
            picked = cls.choose(
                parent, title, text,
                [(yes, "danger" if danger else "primary"), (no, "plain")],
                "ask")
            return picked == 0

    class ExportDialog(FramelessDialog):
        """导出前选规格：平台预设、临时改参数、要不要顺带截一张封面。"""

        CUSTOM = "custom"

        def __init__(self, parent, settings: Settings, total: float,
                     cover_at: float):
            super().__init__(parent, tr("导出"))
            self.settings = settings
            self.total = max(0.0, float(total or 0.0))
            self._loading = False
            self.setMinimumWidth(500)

            self.cmb_preset = QComboBox()
            for key in EXPORT_PRESET_ORDER:
                self.cmb_preset.addItem(tr(EXPORT_PRESETS[key]["label"]), key)
            self.cmb_preset.addItem(tr("自定义"), self.CUSTOM)

            self.spin_w = QSpinBox()
            self.spin_w.setRange(160, 4096)
            self.spin_w.setSingleStep(2)
            self.spin_w.setSuffix(tr(" 像素"))
            self.spin_h = QSpinBox()
            self.spin_h.setRange(160, 4096)
            self.spin_h.setSingleStep(2)
            self.spin_h.setSuffix(tr(" 像素"))
            row_wh = QHBoxLayout()
            row_wh.setContentsMargins(0, 0, 0, 0)
            row_wh.addWidget(self.spin_w, 1)
            row_wh.addWidget(QLabel("×"))
            row_wh.addWidget(self.spin_h, 1)
            wrap_wh = QWidget()
            wrap_wh.setLayout(row_wh)

            self.cmb_fps = QComboBox()
            for value in EXPORT_FPS:
                self.cmb_fps.addItem("%d fps" % value, value)
            self.spin_crf = QSpinBox()
            self.spin_crf.setRange(0, 51)
            self.spin_crf.setToolTip(tr("数字越小越清晰、文件越大；常见 18～23"))
            self.cmb_ab = QComboBox()
            for rate in EXPORT_AUDIO_RATES:
                self.cmb_ab.addItem(rate, rate)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.addRow(tr("平台预设"), self.cmb_preset)
            form.addRow(tr("分辨率"), wrap_wh)
            form.addRow(tr("帧率"), self.cmb_fps)
            form.addRow(tr("画质"), self.spin_crf)
            form.addRow(tr("音频码率"), self.cmb_ab)
            self.body.addLayout(form)

            self.lbl_summary = QLabel("")
            self.lbl_summary.setWordWrap(True)
            self.lbl_summary.setObjectName("hint")
            self.body.addWidget(self.lbl_summary)

            self.chk_cover = QCheckBox(tr("同时导出封面（同名 .jpg）"))
            self.body.addWidget(self.chk_cover)
            self.spin_cover = QDoubleSpinBox()
            self.spin_cover.setRange(0.0, max(0.1, self.total))
            self.spin_cover.setDecimals(1)
            self.spin_cover.setSingleStep(0.5)
            self.spin_cover.setSuffix(tr(" 秒"))
            row_cover = QHBoxLayout()
            row_cover.setContentsMargins(0, 0, 0, 0)
            row_cover.addWidget(QLabel(tr("封面时间点")))
            row_cover.addWidget(self.spin_cover, 1)
            wrap_cover = QWidget()
            wrap_cover.setLayout(row_cover)
            self.body.addWidget(wrap_cover)

            row = QHBoxLayout()
            row.addStretch(1)
            ok = QPushButton(tr("导出"))
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            cancel = QPushButton(tr("取消"))
            cancel.clicked.connect(self.reject)
            row.addWidget(ok)
            row.addWidget(cancel)
            self.body.addLayout(row)

            self.cmb_preset.activated.connect(self._on_preset)
            for widget in (self.spin_w, self.spin_h, self.spin_crf):
                widget.valueChanged.connect(self._touch)
            self.cmb_fps.currentIndexChanged.connect(self._touch)
            self.cmb_ab.currentIndexChanged.connect(self._touch)
            self.chk_cover.toggled.connect(self._on_cover_toggle)

            self.chk_cover.setChecked(bool(settings["cover_export"]))
            self.spin_cover.setValue(clamp_cover_time(cover_at, self.total)
                                     if self.total > 0 else 0.0)
            self.spin_cover.setEnabled(self.chk_cover.isChecked())
            remembered = settings["export_preset"]
            self._apply_preset(
                remembered if remembered in EXPORT_PRESETS else "generic")

        # -- 取值 ---------------------------------------------------------
        def _base(self) -> dict:
            s = self.settings
            return dict(w=even(s["width"]), h=even(s["height"]),
                        fps=int(s["fps"]), crf=s.crf(), ab="192k")

        def _apply_preset(self, key: str):
            """按预设填一遍控件；preset=generic 就用设置里的值。"""
            self._loading = True
            try:
                plan = export_plan(key, self._base())
                self.spin_w.setValue(plan["w"])
                self.spin_h.setValue(plan["h"])
                i = self.cmb_fps.findData(plan["fps"])
                self.cmb_fps.setCurrentIndex(i if i >= 0 else 0)
                self.spin_crf.setValue(plan["crf"])
                j = self.cmb_ab.findData(plan["ab"])
                self.cmb_ab.setCurrentIndex(j if j >= 0 else 3)
                k = self.cmb_preset.findData(key)
                self.cmb_preset.setCurrentIndex(k if k >= 0 else 0)
            finally:
                self._loading = False
            self._refresh_summary()

        def _on_preset(self, *_args):
            key = self.cmb_preset.currentData()
            if key and key != self.CUSTOM:
                self._apply_preset(key)

        def _touch(self, *_args):
            """手改任何一项就变成「自定义」。"""
            if self._loading:
                return
            self._loading = True
            try:
                idx = self.cmb_preset.findData(self.CUSTOM)
                if idx >= 0:
                    self.cmb_preset.setCurrentIndex(idx)
            finally:
                self._loading = False
            self._refresh_summary()

        def _on_cover_toggle(self, on: bool):
            self.spin_cover.setEnabled(bool(on))

        def _refresh_summary(self):
            plan = self.plan()
            mbps = estimate_kbps(plan) / 1000.0
            self.lbl_summary.setText(tr(
                "实际参数：{w}×{h} · {fps} 帧/秒 · 画质 {crf} · 音频 {ab}"
                " · 视频码率约 {mbps} 兆比特/秒",
                w=plan["w"], h=plan["h"], fps=plan["fps"],
                crf=plan["crf"], ab=plan["ab"], mbps="%.1f" % mbps))

        def plan(self) -> dict:
            key = self.cmb_preset.currentData() or "generic"
            plan = export_plan(
                "generic" if key == self.CUSTOM else key,
                dict(w=self.spin_w.value(), h=self.spin_h.value(),
                     fps=self.cmb_fps.currentData(),
                     crf=self.spin_crf.value(),
                     ab=self.cmb_ab.currentData()))
            if key == self.CUSTOM:
                plan["preset"] = self.CUSTOM
            plan["cover"] = bool(self.chk_cover.isChecked())
            plan["cover_at"] = clamp_cover_time(self.spin_cover.value(),
                                                self.total)
            return plan

        @classmethod
        def get_plan(cls, parent, settings: Settings, total: float,
                     cover_at: float):
            dlg = cls(parent, settings, total, cover_at)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            return dlg.plan()

    class OverlayDialog(FramelessDialog):
        """画中画的参数：素材、进出时间、九宫格位置、偏移、大小、透明度。"""

        def __init__(self, parent, ov: Overlay, total: float):
            super().__init__(parent, tr("画中画"))
            self.total = max(0.1, float(total or 0.0))
            self.setMinimumWidth(520)
            self._path = ov.path

            self.lbl_file = QLabel(os.path.basename(ov.path or tr("没选素材")))
            self.lbl_file.setWordWrap(True)
            self.lbl_file.setObjectName("hint")
            btn_file = QPushButton(tr("换素材…"))
            btn_file.clicked.connect(self._pick_file)
            row_file = QHBoxLayout()
            row_file.setContentsMargins(0, 0, 0, 0)
            row_file.addWidget(self.lbl_file, 1)
            row_file.addWidget(btn_file)
            wrap_file = QWidget()
            wrap_file.setLayout(row_file)

            self.spin_start = QDoubleSpinBox()
            self.spin_start.setRange(0.0, self.total)
            self.spin_start.setDecimals(1)
            self.spin_start.setSingleStep(0.1)
            self.spin_start.setSuffix(tr(" 秒"))
            self.spin_end = QDoubleSpinBox()
            self.spin_end.setRange(0.1, self.total)
            self.spin_end.setDecimals(1)
            self.spin_end.setSingleStep(0.1)
            self.spin_end.setSuffix(tr(" 秒"))

            self.cmb_anchor = QComboBox()
            for key in OVERLAY_ANCHORS:
                self.cmb_anchor.addItem(tr(OVERLAY_ANCHOR_LABELS[key]), key)
            self.spin_scale = QSpinBox()
            self.spin_scale.setRange(int(OVERLAY_MIN_SCALE),
                                     int(OVERLAY_MAX_SCALE))
            self.spin_scale.setSuffix("%")
            self.spin_scale.setToolTip(tr("相对成片宽度；40% 差不多是右上角一块"))
            self.spin_dx = QDoubleSpinBox()
            self.spin_dx.setRange(-100.0, 100.0)
            self.spin_dx.setDecimals(1)
            self.spin_dx.setSuffix("%")
            self.spin_dy = QDoubleSpinBox()
            self.spin_dy.setRange(-100.0, 100.0)
            self.spin_dy.setDecimals(1)
            self.spin_dy.setSuffix("%")
            self.spin_opacity = QSpinBox()
            self.spin_opacity.setRange(0, 100)
            self.spin_opacity.setSuffix("%")

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.addRow(tr("素材"), wrap_file)
            form.addRow(tr("出现时间"), self.spin_start)
            form.addRow(tr("消失时间"), self.spin_end)
            form.addRow(tr("九宫格位置"), self.cmb_anchor)
            form.addRow(tr("水平偏移"), self.spin_dx)
            form.addRow(tr("垂直偏移"), self.spin_dy)
            form.addRow(tr("缩放"), self.spin_scale)
            form.addRow(tr("不透明度"), self.spin_opacity)
            self.body.addLayout(form)

            hint = QLabel(tr("时间线上可以直接拖画中画改进出时间；"
                             "偏移是相对九宫格的位置，正数往右 / 往下。"))
            hint.setWordWrap(True)
            hint.setObjectName("hint")
            self.body.addWidget(hint)

            row = QHBoxLayout()
            row.addStretch(1)
            ok = QPushButton(tr("好了"))
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            cancel = QPushButton(tr("取消"))
            cancel.clicked.connect(self.reject)
            row.addWidget(ok)
            row.addWidget(cancel)
            self.body.addLayout(row)

            self.spin_start.setValue(max(0.0, ov.start))
            self.spin_end.setValue(max(ov.start + OVERLAY_MIN_SPAN,
                                       min(self.total, ov.end)))
            idx = self.cmb_anchor.findData(ov.anchor)
            self.cmb_anchor.setCurrentIndex(max(0, idx))
            self.spin_dx.setValue(ov.dx)
            self.spin_dy.setValue(ov.dy)
            self.spin_scale.setValue(int(round(ov.scale)))
            self.spin_opacity.setValue(int(round(ov.opacity)))

        def _pick_file(self):
            base = (os.path.dirname(self._path) if self._path
                    else os.path.expanduser("~"))
            picked = FileDialog.get_open_files(
                self, tr("选叠加素材"), base,
                tr("图片或视频 (*.jpg *.jpeg *.png *.bmp *.webp *.mp4"
                   " *.mov *.mkv *.webm *.avi)"))
            if picked:
                self._path = picked[0]
                self.lbl_file.setText(os.path.basename(self._path))

        def overlay(self) -> Overlay:
            start = self.spin_start.value()
            end = self.spin_end.value()
            if end <= start:
                end = start + OVERLAY_MIN_SPAN
            if end > self.total:
                end = self.total
                start = max(0.0, min(start, end - OVERLAY_MIN_SPAN))
            return Overlay(self._path, start, end,
                           scale=float(self.spin_scale.value()),
                           anchor=self.cmb_anchor.currentData() or "tr",
                           dx=self.spin_dx.value(), dy=self.spin_dy.value(),
                           opacity=float(self.spin_opacity.value()))

        @classmethod
        def get_overlay(cls, parent, ov: Overlay, total: float):
            dlg = cls(parent, ov, total)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            out = dlg.overlay()
            return out if out.path else None

    class ColorDialog(FramelessDialog):
        """一段素材的调色：亮度 / 对比度 / 饱和度三个滑杆 + 一个 .cube LUT。

        滑杆用整数：亮度 -100..100（除以 100 就是 -1..1），对比度 0..200、
        饱和度 0..300（除以 100 就是 0..2 / 0..3）。
        """

        def __init__(self, parent, clip: Clip, lut_dir: str = ""):
            super().__init__(parent, tr("调色"))
            self.setMinimumWidth(470)

            self._bright = QSlider(Qt.Orientation.Horizontal)
            self._bright.setRange(-100, 100)
            self._contrast = QSlider(Qt.Orientation.Horizontal)
            self._contrast.setRange(0, 200)
            self._sat = QSlider(Qt.Orientation.Horizontal)
            self._sat.setRange(0, 300)
            self._lbl_b = QLabel()
            self._lbl_c = QLabel()
            self._lbl_s = QLabel()
            for lbl in (self._lbl_b, self._lbl_c, self._lbl_s):
                lbl.setObjectName("hint")
                lbl.setMinimumWidth(52)

            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.addRow(tr("亮度"), self._slider_row(self._bright, self._lbl_b))
            form.addRow(tr("对比度"),
                         self._slider_row(self._contrast, self._lbl_c))
            form.addRow(tr("饱和度"), self._slider_row(self._sat, self._lbl_s))
            self.cmb_lut = QComboBox()
            self.cmb_lut.addItem(tr("不用"), "")
            luts = all_luts(lut_dir)
            for path in luts:
                self.cmb_lut.addItem(os.path.basename(path), path)
            if clip.lut and clip.lut not in luts:
                self.cmb_lut.addItem(
                    tr("{name}（不在目录里）", name=os.path.basename(clip.lut)),
                    clip.lut)
            form.addRow(tr("滤镜（LUT）"), self.cmb_lut)
            self.body.addLayout(form)

            hint = QLabel(tr("调色只作用在这一段上：先调亮度/对比度/饱和度，"
                             "再套滤镜。滤镜是 .cube 文件，目录在设置里指定，"
                             "也可以从资源包装进来。"))
            hint.setWordWrap(True)
            hint.setObjectName("hint")
            self.body.addWidget(hint)

            row = QHBoxLayout()
            reset = QPushButton(tr("重置"))
            reset.clicked.connect(self.reset)
            row.addWidget(reset)
            row.addStretch(1)
            ok = QPushButton(tr("好了"))
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            cancel = QPushButton(tr("取消"))
            cancel.clicked.connect(self.reject)
            row.addWidget(ok)
            row.addWidget(cancel)
            self.body.addLayout(row)

            self._bright.setValue(int(round(clip.brightness * 100)))
            self._contrast.setValue(int(round(clip.contrast * 100)))
            self._sat.setValue(int(round(clip.saturation * 100)))
            idx = self.cmb_lut.findData(clip.lut)
            self.cmb_lut.setCurrentIndex(max(0, idx))
            self._sync()

        @staticmethod
        def _slider_row(slider, label) -> QWidget:
            wrap = QWidget()
            row = QHBoxLayout(wrap)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(slider, 1)
            row.addWidget(label)
            return wrap

        def _sync(self, *_a):
            self._lbl_b.setText("%+.2f" % (self._bright.value() / 100.0))
            self._lbl_c.setText("%.2f" % (self._contrast.value() / 100.0))
            self._lbl_s.setText("%.2f" % (self._sat.value() / 100.0))

        def reset(self):
            self._bright.setValue(0)
            self._contrast.setValue(100)
            self._sat.setValue(100)
            self.cmb_lut.setCurrentIndex(0)

        def values(self) -> tuple[float, float, float, str]:
            return (self._bright.value() / 100.0,
                    self._contrast.value() / 100.0,
                    self._sat.value() / 100.0,
                    self.cmb_lut.currentData() or "")

        @classmethod
        def get_values(cls, parent, clip: Clip, lut_dir: str = ""):
            dlg = cls(parent, clip, lut_dir)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            return dlg.values()

    class NumberDialog(FramelessDialog):
        """要一个数字的输入框（改每张图显示多久用）。"""

        def __init__(self, parent, title: str, label: str, value: float,
                     minimum: float, maximum: float, decimals: int = 1,
                     suffix: str = ""):
            super().__init__(parent, title)
            lab = QLabel(label)
            self.body.addWidget(lab)
            self.spin = QDoubleSpinBox()
            self.spin.setRange(minimum, maximum)
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(0.5)
            self.spin.setSuffix(suffix or tr(" 秒"))
            self.spin.setValue(value)
            self.spin.selectAll()
            self.body.addWidget(self.spin)
            row = QHBoxLayout()
            row.addStretch(1)
            ok = QPushButton(tr("好"))
            ok.setDefault(True)
            ok.clicked.connect(self.accept)
            cancel = QPushButton(tr("取消"))
            cancel.clicked.connect(self.reject)
            row.addWidget(ok)
            row.addWidget(cancel)
            self.body.addLayout(row)
            self.setMinimumWidth(340)

        @classmethod
        def get_double(cls, parent, title: str, label: str, value: float,
                       minimum: float, maximum: float,
                       decimals: int = 1, suffix: str = ""):
            dlg = cls(parent, title, label, value, minimum, maximum, decimals,
                      suffix)
            ok = dlg.exec() == QDialog.DialogCode.Accepted
            return float(dlg.spin.value()), bool(ok)

    class ProgressDialog(FramelessDialog):
        """下载进度框（自绘边框，带取消）。"""

        canceled = pyqtSignal()

        def __init__(self, parent, title: str, label: str, cancel_text: str):
            super().__init__(parent, title)
            self.lbl = QLabel(label)
            self.lbl.setWordWrap(True)
            self.body.addWidget(self.lbl)
            self.bar = QProgressBar()
            self.bar.setRange(0, 100)
            self.body.addWidget(self.bar)
            row = QHBoxLayout()
            row.addStretch(1)
            self.btn = QPushButton(cancel_text)
            self.btn.clicked.connect(self.canceled.emit)
            row.addWidget(self.btn)
            self.body.addLayout(row)
            self.setMinimumWidth(380)

        def set_label(self, text: str):
            self.lbl.setText(text)

        def set_value(self, value: int):
            self.bar.setValue(max(0, min(100, int(value))))

        def reset(self):
            pass

    def _arrow_icon(kind: str, color: str, size: int = 18) -> QIcon:
        """文件框工具栏上的三角图标自己画：系统那套在深色底上几乎看不见。"""
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        if kind == "back":
            pts = [QPointF(size * 0.68, size * 0.16),
                   QPointF(size * 0.68, size * 0.84),
                   QPointF(size * 0.28, size * 0.5)]
        elif kind == "forward":
            pts = [QPointF(size * 0.32, size * 0.16),
                   QPointF(size * 0.32, size * 0.84),
                   QPointF(size * 0.72, size * 0.5)]
        else:
            pts = [QPointF(size * 0.16, size * 0.64),
                   QPointF(size * 0.84, size * 0.64),
                   QPointF(size * 0.5, size * 0.24)]
        painter.drawPolygon(QPolygonF(pts))
        painter.end()
        return QIcon(pm)

    class FileDialog:
        """系统原生文件框：有快速访问、最近位置，键盘操作和系统一致。"""

        @classmethod
        def get_open_files(cls, parent, caption: str, directory: str = "",
                           filters: str = "", multi: bool = False):
            if multi:
                files, _ = QFileDialog.getOpenFileNames(
                    parent, caption, directory, filters)
                return [p for p in files if p]
            one, _ = QFileDialog.getOpenFileName(
                parent, caption, directory, filters)
            return [one] if one else []

        @classmethod
        def get_save_file(cls, parent, caption: str, path: str = "",
                          filters: str = "", suffix: str = "mp4") -> str:
            name, _ = QFileDialog.getSaveFileName(parent, caption, path, filters)
            if not name:
                return ""
            if suffix and not name.lower().endswith("." + suffix.lower()):
                name += "." + suffix
            return name

        @classmethod
        def get_dir(cls, parent, caption: str, directory: str = "") -> str:
            return QFileDialog.getExistingDirectory(
                parent, caption, directory) or ""

    class FetchWorker(QThread):
        """取音源：B 站走自带解析，其它平台走 yt-dlp，本地文件直接用。"""

        ok = pyqtSignal(str, float)
        bad = pyqtSignal(str)
        note = pyqtSignal(str)          # 进度文案（"正在解析链接…"）

        def __init__(self, url, out_dir, parent=None):
            super().__init__(parent)
            self.url = url
            self.out_dir = out_dir

        def run(self):
            # 文件名带链接指纹：换一个链接不会把上一条覆盖掉，
            # 老工程里登记的音乐也还在。
            stamp = hashlib.sha1(
                self.url.encode("utf-8", "replace")).hexdigest()[:10]
            try:
                path, _kind = fetch_audio(
                    self.url, os.path.join(self.out_dir, "bgm_%s.m4a" % stamp),
                    progress=self.note.emit)
            except SourceError as e:
                self.bad.emit(e.message)
            except BiliError as e:
                self.bad.emit(bili_message(e))
            except Exception as e:  # noqa: BLE001
                self.bad.emit(f"{e}")
            else:
                self.ok.emit(path, probe_duration(path))

    class ComponentWorker(QThread):
        """在后台下载一个组件（ffmpeg / yt-dlp），下完就能用，也能随时删。"""
        done = pyqtSignal(str)
        bad = pyqtSignal(str)
        note = pyqtSignal(str)

        def __init__(self, key: str, parent=None):
            super().__init__(parent)
            self.key = key

        def run(self):
            def note(text: str, pct: float = 0.0):
                if pct <= 0.0 or pct >= 0.99:
                    self.note.emit(text)
                else:
                    self.note.emit("%s %d%%" % (text, int(pct * 100)))

            try:
                ver = components.install(self.key, progress=note)
            except components.ComponentError as e:
                self.bad.emit(e.message)
            except Exception as e:  # noqa: BLE001
                self.bad.emit(f"{e}")
            else:
                components.prepend_path()
                self.done.emit(ver)

    class RenderWorker(QThread):
        progress = pyqtSignal(float)
        ok = pyqtSignal(str)
        bad = pyqtSignal(str)
        stopped = pyqtSignal()

        def __init__(self, clips, counts, audio, out, opts=None, preview=False,
                     parent=None):
            super().__init__(parent)
            self.clips, self.counts = clips, counts
            self.audio, self.out = audio, out
            self.opts = opts or {}
            self.preview = preview
            self._proc: dict = {}
            self._canceled = False

        def cancel(self):
            """点「取消」时调：停掉 ffmpeg，run() 会走 stopped 分支。"""
            self._canceled = True
            proc = self._proc.get("proc")
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass

        def run(self):
            try:
                if self.preview:
                    render(self.clips, self.counts, self.audio, self.out,
                           crf=28, preset="ultrafast", **self.opts,
                           on_progress=self.progress.emit,
                           proc_holder=self._proc)
                else:
                    render(self.clips, self.counts, self.audio, self.out,
                           **self.opts, on_progress=self.progress.emit,
                           proc_holder=self._proc)
            except RenderError as e:
                if self._canceled:
                    self.stopped.emit()
                else:
                    self.bad.emit(str(e))
            except Exception as e:  # noqa: BLE001
                if self._canceled:
                    self.stopped.emit()
                else:
                    self.bad.emit(f"{e}")
            else:
                if self._canceled:
                    self.stopped.emit()
                else:
                    self.ok.emit(self.out)

    class AudioMixWorker(QThread):
        """只混音轨：预览的主时钟。画面由 Scene 现画，不用等视频编码。"""

        ok = pyqtSignal(str, str)
        bad = pyqtSignal(str)

        def __init__(self, clips, counts, audio, out, opts=None, sig="",
                     parent=None):
            super().__init__(parent)
            self.clips, self.counts = clips, counts
            self.audio, self.out = audio, out
            self.opts = opts or {}
            self.sig = sig

        def run(self):
            try:
                render_audio_mix(
                    self.clips, self.counts, self.audio, self.out,
                    fps=int(self.opts.get("fps", FPS)),
                    volume=float(self.opts.get("volume", 1.0)),
                    fade_in=float(self.opts.get("fade_in", FADE_IN)),
                    fade_out=float(self.opts.get("fade_out", FADE_OUT)),
                    music_start=float(self.opts.get("music_start", 0.0)),
                    xfade_sec=float(self.opts.get("xfade_sec", 0.3)),
                    ab=str(self.opts.get("ab", "192k")))
            except Exception as e:  # noqa: BLE001
                self.bad.emit("%s" % e)
            else:
                self.ok.emit(self.out, self.sig)


    class TtsWorker(QThread):
        """后台合成旁白：按段调 tts_engine，进度往界面回，能中途取消。"""

        progress = pyqtSignal(int, int, int)     # 已完成段数, 总段数, 命中缓存段数
        ok = pyqtSignal(dict)
        bad = pyqtSignal(str)
        stopped = pyqtSignal()

        def __init__(self, plan, kwargs=None, parent=None):
            super().__init__(parent)
            self.plan = plan
            self.kwargs = kwargs or {}
            # tts_engine 要的是一个 Event（它只看 is_set），不是回调
            self._stop = threading.Event()

        def cancel(self):
            self._stop.set()

        def canceled(self) -> bool:
            return self._stop.is_set()

        def run(self):
            try:
                res = tts_engine.synth_plan(
                    self.plan,
                    on_progress=lambda done, total, cached: self.progress.emit(
                        done, total, cached),
                    cancel=self._stop, **self.kwargs)
            except tts_engine.TtsError as e:
                if self.canceled():
                    self.stopped.emit()
                else:
                    self.bad.emit(tts_engine.tts_error_text(e))
            except Exception as e:  # noqa: BLE001
                if self.canceled():
                    self.stopped.emit()
                else:
                    self.bad.emit("%s" % e)
            else:
                if self.canceled():
                    self.stopped.emit()
                else:
                    self.ok.emit(res)

    class ImageList(QListWidget):
        """素材列表。

        内部拖动只改顺序（internal move 不一定触发 rowsMoved，所以自己发信号）；
        从外面拖图片进来按落点分三段：左三分之一插在前面、中间替换这一张、
        右三分之一插在后面。
        """

        order_changed = pyqtSignal()
        drop_files = pyqtSignal(list, int, str)   # 路径, 行号, before/replace/after

        def __init__(self, parent=None):
            super().__init__(parent)
            self._zone = None        # (行号, 段)
            self.setAcceptDrops(True)

        def _external(self, mime) -> list:
            """外面拖进来的图片（或文件夹）才算，内部拖动返回空。"""
            if not mime.hasUrls():
                return []
            out = []
            for u in mime.urls():
                if not u.isLocalFile():
                    continue
                p = os.path.normpath(u.toLocalFile())
                ext = os.path.splitext(p)[1].lower()
                if ext in IMG_EXTS or os.path.isdir(p):
                    out.append(p)
            return out

        def _hit(self, pos):
            """落点 → (行号, 段)。空处算追加到末尾。"""
            item = self.itemAt(pos)
            if item is None:
                return -1, "after"
            rect = self.visualItemRect(item)
            frac = (pos.x() - rect.left()) / max(1, rect.width())
            row = self.row(item)
            if frac < 1.0 / 3:
                return row, "before"
            if frac > 2.0 / 3:
                return row, "after"
            return row, "replace"

        def dragEnterEvent(self, ev):
            if self._external(ev.mimeData()):
                self._zone = self._hit(ev.position().toPoint())
                ev.acceptProposedAction()
                self.viewport().update()
                return
            self._zone = None
            super().dragEnterEvent(ev)

        def dragMoveEvent(self, ev):
            if self._external(ev.mimeData()):
                self._zone = self._hit(ev.position().toPoint())
                ev.acceptProposedAction()
                self.viewport().update()
                return
            self._zone = None
            super().dragMoveEvent(ev)

        def dragLeaveEvent(self, ev):
            self._zone = None
            self.viewport().update()
            super().dragLeaveEvent(ev)

        def dropEvent(self, ev):
            paths = self._external(ev.mimeData())
            if paths:
                row, zone = self._zone or self._hit(ev.position().toPoint())
                self._zone = None
                self.viewport().update()
                self.drop_files.emit(paths, row, zone)
                ev.acceptProposedAction()
                return
            self._zone = None
            super().dropEvent(ev)
            self.order_changed.emit()

        def paintEvent(self, ev):
            super().paintEvent(ev)
            if not self._zone:
                return
            row, zone = self._zone
            item = self.item(row) if row >= 0 else None
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            col = self.palette().color(QPalette.ColorRole.Highlight)
            p.setPen(QPen(col, 3))
            if item is None:
                y = self.viewport().height() - 2
                p.drawLine(0, y, self.viewport().width(), y)
            else:
                rect = self.visualItemRect(item)
                if zone == "replace":
                    p.setBrush(QBrush(QColor(col.red(), col.green(),
                                           col.blue(), 40)))
                    p.drawRect(rect.adjusted(1, 1, -1, -1))
                else:
                    x = rect.left() if zone == "before" else rect.right()
                    p.drawLine(x, rect.top(), x, rect.bottom())
            # 光有高亮看不出「会替换还是插入」，直接把动作写在落点旁边
            if item is None:
                text = tr("加到末尾")
            else:
                kind = item.data(Qt.ItemDataRole.UserRole + 3) or "image"
                n = self.row(item) + 1
                if zone == "replace" and kind == "image":
                    text = tr("替换第 {n} 段", n=n)
                elif zone == "before":
                    text = tr("插到第 {n} 段前面", n=n)
                else:
                    # 视频不能替换，中间落点会退化成插到它后面
                    text = tr("插到第 {n} 段后面", n=n)
            font = QFont(self.font())
            font.setPointSize(9)
            p.setFont(font)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(text) + 16
            th = fm.height() + 6
            if item is None:
                bx = 8
                by = max(4, self.viewport().height() - th - 8)
            else:
                # 贴在缩略图下沿，别压住上面那行文字
                bx = max(6, rect.left() + 8)
                by = max(2, rect.bottom() - th - 6)
            box = QRect(bx, by, tw, th)
            p.setPen(QPen(col, 1))
            p.setBrush(QBrush(self.palette().color(
                QPalette.ColorRole.Base)))
            p.drawRoundedRect(box, 5, 5)
            p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            p.drawText(box.adjusted(8, 0, -8, 0),
                       Qt.AlignmentFlag.AlignVCenter
                       | Qt.AlignmentFlag.AlignLeft, text)
            p.end()

    class Timeline(QWidget):
        """双轨时间线：上面视频元素，下面音频元素。

        能做的：点一下跳转、拖播放头擦洗、拖色块换位置、拖右边界改时长、
        Shift/Ctrl 多选、音频块滚轮调音量、右键弹出对应元素的菜单。
        """

        seeked = pyqtSignal(float)
        picked = pyqtSignal(int)
        resized = pyqtSignal(int, float)
        trimmed = pyqtSignal(int, str, float)
        moved = pyqtSignal(int, float)
        audioMoved = pyqtSignal(int, float)
        audioVolume = pyqtSignal(int, float)
        envelopeChanged = pyqtSignal(int)
        overlayMoved = pyqtSignal(int, float)
        cut = pyqtSignal(str, int, float)          # 刀片：("video"/"audio", 序号, 时刻)
        audioTrimmed = pyqtSignal(int, str, float)  # 拖音频左右边：裁头/裁尾
        audioTrimDone = pyqtSignal(int)             # 裁完松手（这时才记一步历史）
        menu = pyqtSignal(str, int, object)
        viewChanged = pyqtSignal(float, float)       # 缩放/滚动变了（起点, 窗口长）
        blockMoved = pyqtSignal(object, int)         # 整块拖动（行号, 落位）
        audioGroupMoved = pyqtSignal(object)         # 多条音频一起挪 [(序号, 起点)]
        subtitleMoved = pyqtSignal(int, float)       # 字幕整条挪（序号, 新起点）

        DEFAULT_COLORS = {"bg": "#1f2329", "empty": "#9ca3af",
                          "piece_a": "#b86b6b", "piece_b": "#b8a05f",
                          "piece_text": "#111827", "playhead": "#f59e0b",
                          "selected_edge": "#3b82f6", "audio": "#2f6f5e",
                          "audio_sel": "#3f9d84", "overlay": "#566bb0",
                          "overlay_sel": "#7c8fe0", "border": "#343a46",
                          "sub": "#6d5f8f", "sub_sel": "#a78bfa"}

        def __init__(self, project, parent=None):
            super().__init__(parent)
            self.project = project
            self.selected: list[int] = []
            self.sel_audio: list[int] = []
            self.sel_overlay: list[int] = []
            self.sel_subs: list[int] = []
            self.sel_env = (-1, -1)
            self._waves = {}
            self.play_pos = 0.0
            self.snap_on = True
            self.razor = False
            self.snap_guide = None
            self._drag = None
            self._press = 0.0
            self.zoom = 1.0          # 1.0 = 整条片子铺满，越大看得越细
            self.view_start = 0.0    # 可见窗口从第几秒开始
            self.show_overlay = True # 简洁模式下没画中画就不占这一条
            self.show_subs = True    # 没有字幕就不占这一条
            self.show_audio = True   # 没有音频同理（配乐是核心，默认给）
            self.drop_guide = None   # 整块拖动时画一条落点虚线
            self._marquee = None     # 框选矩形（QRectF），没框选时为 None
            self.c = dict(self.DEFAULT_COLORS)
            self.setMinimumHeight(150)
            self.setMouseTracking(True)
            self.setToolTip(tr("时间线：空白处拖一下可以框选多个片段；拖选中的一段就是整块挪动"))
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # -- 几何 ------------------------------------------------------
        def _video_rect(self):
            h = self.height()
            # 顶部留一条时间刻度，画面块从它下面开始
            return TIMELINE_RULER_H, max(TIMELINE_RULER_H + 26, int(h * 0.46))

        def _overlay_rect(self):
            """画中画轨：夹在画面块和音频块中间；不需要时高度为零。"""
            if not self.show_overlay:
                edge = self._video_rect()[1]
                return edge, edge
            top = self._video_rect()[1] + 12
            return top, top + max(14, int(self.height() * 0.14))

        def _subs_rect(self):
            """字幕轨：夹在画中画和音频中间；没有字幕时高度为零。"""
            if not self.show_subs:
                edge = self._overlay_rect()[1]
                return edge, edge
            top = self._overlay_rect()[1] + (8 if self.show_overlay else 4)
            return top, top + max(12, int(self.height() * 0.09))

        def _audio_rect(self):
            top = self._subs_rect()[1]
            top += 6 if self.show_subs else (
                8 if self.show_overlay else 6)
            if not self.show_audio:
                return top, top
            return top, max(top + 16, self.height() - 4)

        def set_rows(self, show_overlay: bool = True,
                     show_audio: bool = True, show_subs: bool = True):
            """按界面档位/内容决定画中画、字幕、音频轨要不要出现。"""
            show_overlay = bool(show_overlay)
            show_audio = bool(show_audio)
            show_subs = bool(show_subs)
            if (show_overlay, show_audio, show_subs) == (
                    self.show_overlay, self.show_audio, self.show_subs):
                return
            self.show_overlay, self.show_audio = show_overlay, show_audio
            self.show_subs = show_subs
            if not show_overlay:
                self.sel_overlay = []
            if not show_audio:
                self.sel_audio = []
            if not show_subs:
                self.sel_subs = []
            self.update()

        def _total(self) -> float:
            return sum(c.span for c in self.project.clips) or 1.0

        # -- 缩放 / 横向滚动 ------------------------------------------
        def _view_span(self) -> float:
            """当前看得见的一段有多长（秒）。"""
            return timeline_span(self._total(), self.zoom)

        def clamp_view(self):
            """段数/总长变了以后，把窗口拉回片子范围内。"""
            self.zoom = max(TIMELINE_ZOOM_MIN,
                            min(TIMELINE_ZOOM_MAX, float(self.zoom or 1.0)))
            self.view_start = timeline_clamp_start(
                self.view_start, self._total(), self._view_span())

        def reset_view(self):
            self.zoom = 1.0
            self.view_start = 0.0
            self.update()
            self.viewChanged.emit(self.view_start, self._view_span())

        def set_zoom(self, zoom: float, anchor: float | None = None):
            """缩放；anchor（默认播放头）在屏幕上的位置尽量不动。"""
            anchor_t = self.play_pos if anchor is None else float(anchor)
            old_span = self._view_span()
            self.zoom = max(TIMELINE_ZOOM_MIN,
                            min(TIMELINE_ZOOM_MAX, float(zoom or 1.0)))
            new_span = self._view_span()
            self.view_start = timeline_zoom_start(
                self.view_start, old_span, anchor_t, new_span, self._total())
            self.update()
            self.viewChanged.emit(self.view_start, new_span)

        def set_view_start(self, start: float):
            self.view_start = timeline_clamp_start(
                start, self._total(), self._view_span())
            self.update()
            self.viewChanged.emit(self.view_start, self._view_span())

        def scroll_by(self, seconds: float):
            self.set_view_start(self.view_start + float(seconds))

        def zoom_at(self, x: float, up: bool = True):
            """Ctrl+滚轮：以鼠标那一刻为中心放大/缩小。"""
            factor = 1.25 if up else 1.0 / 1.25
            self.set_zoom(self.zoom * factor, self._time_at(x))

        def ensure_visible(self, t: float):
            """播放头走出窗口就把窗口跟着挪（只在播放时叫）。"""
            total = self._total()
            span = self._view_span()
            t = max(0.0, min(total, float(t)))
            if t < self.view_start:
                self.set_view_start(t)
            elif t > self.view_start + span:
                # 留 15% 的余量，别让播放头顶在右边缘
                self.set_view_start(t - span * 0.85)

        def _spans(self):
            total = self._total()
            span = self._view_span()
            start = self.view_start
            x, spans = 0.0, []
            for d in (c.span for c in self.project.clips):
                x0 = timeline_x(x, start, span, self.width())
                x1 = timeline_x(x + d, start, span, self.width())
                spans.append((x0, x1, d))
                x += d
            return spans, total

        def _audio_spans(self):
            total = self._total()
            span = self._view_span()
            start = self.view_start
            out = []
            for a in self.project.audios:
                x0 = timeline_x(max(0.0, a.start), start, span, self.width())
                x1 = timeline_x(min(total, a.start + a.dur), start, span,
                                self.width())
                out.append((x0, max(x0 + 3, x1), a))
            return out

        def _overlay_spans(self):
            total = self._total()
            span = self._view_span()
            start = self.view_start
            out = []
            for o in self.project.overlays:
                x0 = timeline_x(max(0.0, o.start), start, span, self.width())
                x1 = timeline_x(min(total, o.end), start, span, self.width())
                out.append((x0, max(x0 + 3, x1), o))
            return out

        def _sub_spans(self):
            total = self._total()
            span = self._view_span()
            start = self.view_start
            out = []
            for cue in self.project.cues:
                t0 = max(0.0, _doc_num(cue.get("start"), 0.0))
                t1 = min(total, _doc_num(cue.get("end"), t0))
                x0 = timeline_x(t0, start, span, self.width())
                x1 = timeline_x(t1, start, span, self.width())
                out.append((x0, max(x0 + 3, x1), cue))
            return out

        def _x_at(self, t: float) -> float:
            return timeline_x(t, self.view_start, self._view_span(),
                              self.width())

        def _wave(self, path: str):
            """波形峰值：内存里按「路径+指纹」缓一份，画的时候只查表。"""
            key = (path, wave_key(path))
            if key not in self._waves:
                if len(self._waves) > 8:
                    self._waves.pop(next(iter(self._waves)))
                self._waves[key] = audio_peaks(path)
            return self._waves[key]

        def _env_xy(self, k: int, j: int):
            """第 k 条音频第 j 个包络点画在哪。"""
            spans = self._audio_spans()
            rows = self._audio_rows(len(spans))
            x0, x1, a = spans[k]
            ry0, ry1 = rows[k]
            t, g = a.points[j]
            x = self._x_at(a.start + t)
            span = max(4.0, ry1 - ry0 - 4)
            y = ry1 - 2 - g * span
            return x, y

        def _env_from_pos(self, k: int, x: float, y: float):
            """鼠标位置 → 这条音频的相对时间与增益。"""
            spans = self._audio_spans()
            rows = self._audio_rows(len(spans))
            x0, x1, a = spans[k]
            ry0, ry1 = rows[k]
            t = (x - x0) / max(1.0, x1 - x0) * max(0.0, a.dur)
            span = max(4.0, ry1 - ry0 - 4)
            g = (ry1 - 2 - y) / span
            return max(0.0, min(a.dur, t)), max(0.0, min(1.0, g))

        def _hit_env(self, x, y):
            for k in range(len(self.project.audios)):
                a = self.project.audios[k]
                for j in range(len(a.points)):
                    ex, ey = self._env_xy(k, j)
                    if abs(ex - x) <= 6 and abs(ey - y) <= 6:
                        return k, j
            return -1, -1

        def _audio_rows(self, count: int):
            """多条音频各自一行，能并排看到叠在一起的效果。"""
            top, bottom = self._audio_rect()
            n = max(1, count)
            row = max(10.0, (bottom - top) / n)
            return [(top + i * row, top + (i + 1) * row - 2) for i in range(n)]

        # -- 磁吸 / 刀片 ----------------------------------------------
        def set_snap(self, on: bool):
            self.snap_on = bool(on)
            self.snap_guide = None
            self.update()

        def set_razor(self, on: bool):
            self.razor = bool(on)
            self.snap_guide = None
            self.setCursor(Qt.CursorShape.CrossCursor if self.razor
                           else Qt.CursorShape.PointingHandCursor)
            self.update()

        def _snap_tol(self) -> float:
            return SNAP_PX * self._view_span() / max(1, self.width())

        def _snap_time(self, t: float, alt: bool = False):
            """把时刻吸到附近的边；按住 Alt 当场不吸，guide 记下贴到了哪。"""
            if not self.snap_on or alt:
                self.snap_guide = None
                return t
            value, hit = snap_value(t, snap_candidates(self.project,
                                                       self.play_pos),
                                    self._snap_tol())
            self.snap_guide = hit
            return value

        def _hit_audio_edge(self, x, y):
            """音频块的左右边：拖它就是裁头 / 裁尾。"""
            top, bottom = self._audio_rect()
            if y < top - 3 or y > bottom + 3:
                return -1, ""
            spans = self._audio_spans()
            rows = self._audio_rows(len(spans))
            for k, (x0, x1, a) in enumerate(spans):
                if a.gap:
                    continue
                ry0, ry1 = rows[k]
                if y < ry0 - 2 or y > ry1 + 2:
                    continue
                if abs(x - x0) <= 5:
                    return k, "left"
                if abs(x - x1) <= 5:
                    return k, "right"
            return -1, ""

        def set_colors(self, colors):
            """主题换了就换色带配色（时间线自己画，不吃 QSS）。"""
            self.c = dict(self.DEFAULT_COLORS)
            self.c.update({k: v for k, v in colors.items() if k in self.c})
            self.update()

        def set_selection(self, rows):
            self.selected = [r for r in rows if 0 <= r < len(self.project.clips)]
            self.update()

        def set_audio_selection(self, rows):
            self.sel_audio = [r for r in rows if 0 <= r < len(self.project.audios)]
            self.update()

        # -- 画 --------------------------------------------------------
        def paintEvent(self, ev):
            self.clamp_view()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(self.rect(), QColor(self.c["bg"]))
            spans, total = self._spans()
            if not spans:
                p.setPen(QColor(self.c["empty"]))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           tr("把图片或视频拖进来，或点「加素材」"))
                return
            font = QFont(self.font())
            font.setPointSize(9)
            p.setFont(font)
            top, vh = self._video_rect()
            # 顶部时间刻度：每格至少 64px，缩放/滚动都跟着重算
            ticks = timeline_ticks(self.view_start, self._view_span(),
                                   self.width())
            p.setPen(QPen(QColor(self.c["border"]), 1))
            p.drawLine(0, TIMELINE_RULER_H - 1, self.width(),
                       TIMELINE_RULER_H - 1)
            for _t, _x, _label in ticks:
                xi = int(_x)
                if xi < -2 or xi > self.width() + 2:
                    continue
                p.setPen(QPen(QColor(self.c["empty"]), 1))
                p.drawLine(xi, TIMELINE_RULER_H - 6, xi, TIMELINE_RULER_H - 1)
                p.setPen(QColor(self.c["empty"]))
                p.drawText(xi + 4, TIMELINE_RULER_H - 5, _label)
                # 一条很淡的竖线，方便把块和刻度对上
                p.setPen(QPen(QColor(self.c["border"]), 1,
                              Qt.PenStyle.DotLine))
                p.drawLine(xi, TIMELINE_RULER_H, xi, self.height() - 2)
            base = QColor(self.c["piece_a"])
            dark_bg = self.c.get("theme") == "dark"
            crosses = 0.0
            for i, (x0, x1, d) in enumerate(spans):
                clip = self.project.clips[i]
                col = QColor(base)
                if i % 2:
                    col = col.lighter(116) if dark_bg else col.darker(108)
                if i in self.selected:
                    col = col.lighter(128)
                p.fillRect(int(x0) + 1, top, max(1, int(x1 - x0) - 2),
                           vh - top - 2, col)
                if i in self.selected:
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 2))
                    p.drawRect(int(x0) + 1, top, max(1, int(x1 - x0) - 2),
                               vh - top - 3)
                if x1 - x0 > 26 and x1 > 0 and x0 < self.width():
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    name = {"intro": tr("片头"), "outro": tr("片尾")}.get(
                        clip.kind, f"{i + 1}")
                    marks = ""
                    if clip.fade:
                        marks += "◐"
                    if clip.xfade:
                        marks += "⇄"
                    text = f"{name} · {d:.1f}s {marks}"
                    base = os.path.basename(clip.path or "")
                    if base and x1 - x0 > 80:
                        # 块够宽就把文件名也写上，几张相似的图才分得清
                        text = f"{name} · {base} · {d:.1f}s {marks}"
                    # 放大后块的左端可能跑到窗口外，文字贴到可见区左边
                    tx = max(int(x0) + 6, 6)
                    avail = int(min(x1, self.width() - 4)) - tx
                    if avail > 24:
                        text = p.fontMetrics().elidedText(
                            text, Qt.TextElideMode.ElideRight, avail)
                        p.drawText(tx, top + 15, text)
                if clip.xfade and i < len(spans) - 1:
                    crosses += 1
                    p.setPen(QPen(QColor(self.c["playhead"]), 1,
                                  Qt.PenStyle.DotLine))
                    p.drawLine(int(x1), top, int(x1), top + vh - top)

            # 画中画轨（这一档/这段没有画中画就不出现）
            otop, obottom = self._overlay_rect()
            if self.show_overlay:
                p.setPen(QColor(self.c["empty"]))
                p.drawText(4, otop - 4, tr("画中画"))
            for k, (x0, x1, ov) in enumerate(
                    self._overlay_spans() if self.show_overlay else []):
                col = QColor(self.c["overlay_sel"] if k in self.sel_overlay
                             else self.c["overlay"])
                p.fillRect(int(x0), otop, max(2, int(x1 - x0)),
                           max(4, obottom - otop - 1), col)
                if k in self.sel_overlay:
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 2))
                    p.drawRect(int(x0), otop, max(2, int(x1 - x0)),
                               max(4, obottom - otop - 1))
                if x1 - x0 > 34 and x1 > 0 and x0 < self.width():
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    tx = max(int(x0) + 5, 5)
                    avail = int(min(x1, self.width() - 4)) - tx
                    if avail > 24:
                        p.drawText(tx, otop + 12, p.fontMetrics().elidedText(
                            f"{os.path.basename(ov.path)[:16]}  "
                            f"{int(round(ov.scale))}%",
                            Qt.TextElideMode.ElideRight, avail))

            # 字幕轨（一条字幕一个块，宽度就是它显示的那一段）
            stop, sbot = self._subs_rect()
            if self.show_subs:
                p.setPen(QColor(self.c["empty"]))
                p.drawText(4, stop - 4, tr("字幕"))
            for k, (x0, x1, cue) in enumerate(
                    self._sub_spans() if self.show_subs else []):
                col = QColor(self.c["sub_sel"] if k in self.sel_subs
                             else self.c["sub"])
                p.fillRect(int(x0) + 1, stop + 1, max(2, int(x1 - x0) - 2),
                           max(4, sbot - stop - 2), col)
                if k in self.sel_subs:
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 2))
                    p.drawRect(int(x0) + 1, stop + 1,
                               max(2, int(x1 - x0) - 2),
                               max(4, sbot - stop - 3))
                if x1 - x0 > 26 and x1 > 0 and x0 < self.width():
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    tx = max(int(x0) + 5, 5)
                    avail = int(min(x1, self.width() - 4)) - tx
                    if avail > 20:
                        p.drawText(tx, stop + 12,
                                   p.fontMetrics().elidedText(
                                       str(cue.get("text") or "").replace(
                                           "\n", " "),
                                       Qt.TextElideMode.ElideRight, avail))

            # 音频轨
            aspans = self._audio_spans()
            rows = self._audio_rows(len(aspans))
            atrack_top, atrack_bottom = self._audio_rect()
            if self.show_audio:
                p.setPen(QColor(self.c["empty"]))
                p.drawText(4, atrack_top - 4, tr("音频"))
            for k, (x0, x1, a) in enumerate(
                    aspans if self.show_audio else []):
                ry0, ry1 = rows[k]
                col = QColor(self.c["audio_sel"] if k in self.sel_audio
                             else self.c["audio"])
                if a.gap:
                    col = QColor(self.c["piece_b"])
                p.fillRect(int(x0), int(ry0) + 1, max(2, int(x1 - x0)),
                           max(4, int(ry1 - ry0) - 1), col)
                # 波形：每个像素列画一根竖线，高度取最近 20 ms 的峰
                peaks = self._wave(a.path) if a.path else []
                if peaks and x1 - x0 > 4:
                    # 按这一条自己的最大峰归一化：安静的素材也看得出起伏
                    wmax = max(peaks) or 1.0
                    mid = (ry0 + ry1) / 2.0
                    half = max(2.0, (ry1 - ry0) / 2.0 - 3)
                    p.setPen(QPen(QColor(255, 255, 255, 120), 1))
                    for xi in range(int(x0), int(x1)):
                        t = self._time_at(xi) - a.start
                        idx = int(t * WAVE_RATE)
                        if 0 <= idx < len(peaks):
                            v = peaks[idx] / wmax * half
                            p.drawLine(xi, int(mid - v), xi, int(mid + v))
                if x1 - x0 > 34 and x1 > 0 and x0 < self.width():
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    gain = (f"  {a.gain_db:+.1f} dB"
                            if abs(a.gain_db) > 0.04 else "")
                    tx = max(int(x0) + 5, 5)
                    avail = int(min(x1, self.width() - 4)) - tx
                    if avail > 24:
                        p.drawText(tx, int(ry0) + 12,
                                   p.fontMetrics().elidedText(
                                       f"{os.path.basename(a.path)[:18]}  "
                                       f"{int(round(a.volume * 100))}%{gain}",
                                       Qt.TextElideMode.ElideRight, avail))
                if a.points:
                    p.setPen(QPen(QColor(self.c["playhead"]), 1.5))
                    poly = [QPointF(self._env_xy(k, j)[0],
                                    self._env_xy(k, j)[1])
                            for j in range(len(a.points))]
                    p.drawPolyline(QPolygonF(poly))
                    for j in range(len(a.points)):
                        ex, ey = self._env_xy(k, j)
                        rad = 4 if (k, j) == self.sel_env else 2.5
                        p.setBrush(QColor(self.c["playhead"]))
                        p.drawEllipse(QPointF(ex, ey), rad, rad)
                    p.setBrush(Qt.BrushStyle.NoBrush)

            px = int(self._x_at(self.play_pos))
            if 0 <= px <= self.width():
                p.setPen(QPen(QColor(self.c["playhead"]), 2))
                p.drawLine(px, 0, px, self.height())

            if self.drop_guide is not None:
                gx = int(self._x_at(self.drop_guide))
                if 0 <= gx <= self.width():
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 2,
                                  Qt.PenStyle.DashLine))
                    p.drawLine(gx, TIMELINE_RULER_H, gx, self.height())

            if self.snap_guide is not None:
                gx = int(self._x_at(self.snap_guide))
                if 0 <= gx <= self.width():
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 1,
                                  Qt.PenStyle.DashLine))
                    p.drawLine(gx, 0, gx, self.height())

            if self._marquee is not None:
                fill = QColor(self.c["selected_edge"])
                fill.setAlpha(40)
                p.fillRect(self._marquee, fill)
                pen = QColor(self.c["selected_edge"])
                pen.setAlpha(180)
                p.setPen(QPen(pen, 1, Qt.PenStyle.DashLine))
                p.drawRect(self._marquee)

        # -- 命中 ------------------------------------------------------
        def _hit_video(self, x, y):
            _t, vh = self._video_rect()
            if y < TIMELINE_RULER_H or y > vh + 2:
                return -1, -1.0
            spans, _ = self._spans()
            for i, (x0, x1, _d) in enumerate(spans):
                if x0 - 3 <= x <= x1 + 3:
                    return i, x1
            return -1, 0.0

        def _hit_overlay(self, x, y):
            if not self.show_overlay:
                return -1
            top, bottom = self._overlay_rect()
            if y < top - 3 or y > bottom + 3:
                return -1
            for k, (x0, x1, _o) in enumerate(self._overlay_spans()):
                if x0 - 3 <= x <= x1 + 3:
                    return k
            return -1

        def _hit_sub(self, x, y):
            if not self.show_subs:
                return -1
            top, bottom = self._subs_rect()
            if y < top - 3 or y > bottom + 3:
                return -1
            for k, (x0, x1, _cue) in enumerate(self._sub_spans()):
                if x0 - 3 <= x <= x1 + 3:
                    return k
            return -1

        def _hit_audio(self, x, y):
            if not self.show_audio:
                return -1
            top, bottom = self._audio_rect()
            if y < top - 3 or y > bottom + 3:
                return -1
            for k, (x0, x1, _a) in enumerate(self._audio_spans()):
                if x0 - 3 <= x <= x1 + 3:
                    return k
            return -1

        # -- 鼠标 ------------------------------------------------------
        def mousePressEvent(self, ev):
            x, y = ev.position().x(), ev.position().y()
            self._press = x
            if ev.button() == Qt.MouseButton.RightButton:
                ek, ej = self._hit_env(x, y)
                if ek >= 0:
                    a = self.project.audios[ek]
                    if 0 <= ej < len(a.points):
                        a.points.pop(ej)
                    self.sel_env = (-1, -1)
                    self.update()
                    self.envelopeChanged.emit(ek)
                    return
                i, _ = self._hit_video(x, y)
                if i >= 0:
                    if i not in self.selected:
                        self.selected = [i]
                    self.picked.emit(i)
                    self.update()
                    self.menu.emit("video", i, ev.globalPosition().toPoint())
                    return
                k = self._hit_overlay(x, y)
                if k >= 0:
                    if k not in self.sel_overlay:
                        self.sel_overlay = [k]
                    self.update()
                    self.menu.emit("overlay", k,
                                   ev.globalPosition().toPoint())
                    return
                _otop, _obottom = self._overlay_rect()
                if self.show_overlay and _otop - 3 <= y <= _obottom + 3:
                    self.menu.emit("overlay_empty", -1,
                                   ev.globalPosition().toPoint())
                    return
                k = self._hit_audio(x, y)
                if k >= 0:
                    if k not in self.sel_audio:
                        self.sel_audio = [k]
                    self.update()
                    self.menu.emit("audio", k, ev.globalPosition().toPoint())
                    return
                self.menu.emit("empty", -1, ev.globalPosition().toPoint())
                return
            if ev.button() != Qt.MouseButton.LeftButton:
                return

            if self.razor:
                # 刀片模式：点哪块切哪块，不进入拖动
                i, _edge = self._hit_video(x, y)
                if i >= 0:
                    self.cut.emit("video", i, self._time_at(x))
                    return
                k = self._hit_audio(x, y)
                if k >= 0 and not self.project.audios[k].gap:
                    self.cut.emit("audio", k, self._time_at(x))
                return

            # Alt + 点音频块：在这一刻加一个包络点
            if ev.modifiers() & Qt.KeyboardModifier.AltModifier:
                k = self._hit_audio(x, y)
                if k >= 0:
                    a = self.project.audios[k]
                    t, g = self._env_from_pos(k, x, y)
                    a.points = clean_env_points(list(a.points) + [(t, g)])
                    idx = min(range(len(a.points)),
                              key=lambda j: abs(a.points[j][0] - t))
                    self.sel_env = (k, idx)
                    self._drag = ("env", k, idx, x)
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                    self.update()
                    self.envelopeChanged.emit(k)
                    return

            i, edge_x = self._hit_video(x, y)
            if i >= 0:
                spans, total = self._spans()
                x0, x1, d = spans[i]
                clip = self.project.clips[i]
                near_left = abs(x - x0) <= 5
                near_right = abs(x - x1) <= 5
                if near_left and near_right:      # 段太窄，离哪边近算哪边
                    near_left = (x - x0) < (x1 - x)
                    near_right = not near_left
                if near_left:
                    self._drag = ("trimL", i, x, clip.span)
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    return
                if near_right:
                    if not clip.is_video:
                        self._drag = ("resize", i, x, d)
                    else:
                        self._drag = ("trimR", i, x, clip.span)
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    return
                multi = bool(ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                               | Qt.KeyboardModifier.ControlModifier))
                if multi:
                    if i in self.selected:
                        self.selected.remove(i)
                    else:
                        self.selected.append(i)
                    self.selected.sort()
                elif not (i in self.selected and len(self.selected) > 1):
                    # 点在已选中的一段上、又是多选状态：留着整块，方便一起拖
                    self.selected = [i]
                self.picked.emit(i)
                start = sum(c.span for c in self.project.clips[:i])
                self.seeked.emit(start)
                self.play_pos = start
                block = timeline_block_rows(self.selected)
                if block is not None and len(self.selected) > 1:
                    lo, _hi = block
                    block_t = sum(c.span for c in self.project.clips[:lo])
                    self._drag = ("clips", i, x, block_t)
                else:
                    self._drag = ("clip", i, x, x0)
                self.update()
                return

            k = self._hit_overlay(x, y)
            if k >= 0:
                ov = self.project.overlays[k]
                self.sel_overlay = [k]
                self._drag = ("overlay", k, x, ov.start)
                self.play_pos = self._time_at(x)
                self.seeked.emit(self.play_pos)
                self.update()
                return

            k = self._hit_sub(x, y)
            if k >= 0:
                self.sel_subs = [k]
                cue = self.project.cues[k]
                start = max(0.0, _doc_num(cue.get("start"), 0.0))
                self.play_pos = start
                self.seeked.emit(start)
                self._drag = ("subtitle", k, x, start)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.update()
                return

            ek, eedge = self._hit_audio_edge(x, y)
            if ek >= 0:
                self.sel_audio = [ek]
                self._drag = ("atrimL" if eedge == "left" else "atrimR",
                              ek, x, 0.0)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.update()
                return

            k = self._hit_audio(x, y)
            if k >= 0:
                a = self.project.audios[k]
                multi = bool(ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                               | Qt.KeyboardModifier.ControlModifier))
                if multi:
                    if k in self.sel_audio:
                        self.sel_audio.remove(k)
                    else:
                        self.sel_audio.append(k)
                    self.sel_audio.sort()
                elif not (k in self.sel_audio and len(self.sel_audio) > 1):
                    self.sel_audio = [k]
                if len(self.sel_audio) > 1 and k in self.sel_audio:
                    base = [(idx, self.project.audios[idx].start)
                            for idx in self.sel_audio]
                    self._drag = ("audios", k, x, a.start, base)
                else:
                    self._drag = ("audio", k, x, a.start)
                self.update()
                return

            if y < TIMELINE_RULER_H:
                # 刻度条上拖：正常擦洗播放头
                self._drag = ("scrub", -1, x, 0.0)
                self.play_pos = self._time_at(x)
                self.seeked.emit(self.play_pos)
                self.update()
                return
            # 空白处：单击＝拖播放头，拖出一块＝框选（超过 5px 才切换）
            self._drag = ("marquee", x, y, x, y,
                          list(self.selected), list(self.sel_audio),
                          list(self.sel_overlay),
                          bool(ev.modifiers() & (
                              Qt.KeyboardModifier.ShiftModifier
                              | Qt.KeyboardModifier.ControlModifier)))
            self.play_pos = self._time_at(x)
            self.seeked.emit(self.play_pos)
            self.update()

        def mouseMoveEvent(self, ev):
            x, y = ev.position().x(), ev.position().y()
            if not self._drag:
                if self.razor:
                    self.setCursor(Qt.CursorShape.CrossCursor)
                    return
                i, _edge_x = self._hit_video(x, y)
                near = False
                if i >= 0:
                    spans, _t = self._spans()
                    x0, x1, _d = spans[i]
                    near = min(abs(x - x0), abs(x - x1)) <= 5
                if not near:
                    near = (self._hit_audio_edge(x, y)[0] >= 0
                            or self._hit_sub(x, y) >= 0)
                self.setCursor(Qt.CursorShape.SizeHorCursor if near
                               else Qt.CursorShape.PointingHandCursor)
                return
            kind = self._drag[0]
            if kind == "marquee":
                x0, y0 = self._drag[1], self._drag[2]
                if (self._marquee is None and abs(x - x0) < 5
                        and abs(y - y0) < 5):
                    return
                base_v, base_a, base_o = self._drag[5:8]
                add = bool(self._drag[8])
                rect = QRectF(QPointF(min(x, x0), min(y, y0)),
                              QPointF(max(x, x0), max(y, y0)))
                self._marquee = rect
                sel_v = list(base_v) if add else []
                sel_a = list(base_a) if add else []
                sel_o = list(base_o) if add else []
                vt, vb = self._video_rect()
                if QRectF(0, vt, self.width(), vb - vt).intersects(rect):
                    for i, (sx0, sx1, _d) in enumerate(self._spans()[0]):
                        if sx1 >= rect.left() and sx0 <= rect.right():
                            sel_v.append(i)
                ot, ob = self._overlay_rect()
                if (self.show_overlay
                        and QRectF(0, ot, self.width(), ob - ot).intersects(rect)):
                    for k, (sx0, sx1, _o) in enumerate(self._overlay_spans()):
                        if sx1 >= rect.left() and sx0 <= rect.right():
                            sel_o.append(k)
                at, ab = self._audio_rect()
                if (self.show_audio
                        and QRectF(0, at, self.width(), ab - at).intersects(rect)):
                    for k, (sx0, sx1, _a) in enumerate(self._audio_spans()):
                        if sx1 >= rect.left() and sx0 <= rect.right():
                            sel_a.append(k)
                self.selected = sorted(set(sel_v))
                self.sel_audio = sorted(set(sel_a))
                self.sel_overlay = sorted(set(sel_o))
                self.update()
                return
            if kind == "clips":
                _i, _i2, x0, _bt = self._drag
                rows = self.selected or [self._drag[1]]
                durations = [c.span for c in self.project.clips]
                dest = timeline_block_dest(durations, rows, self._time_at(x))
                block = timeline_block_rows(rows)
                if block is not None:
                    lo, hi = block
                    rest = [d for j, d in enumerate(durations)
                            if not (lo <= j <= hi)]
                    self.drop_guide = sum(rest[:dest])
                self.play_pos = self._time_at(x)
                self.update()
                return
            if kind == "subtitle":
                _tag, k, x0, start0 = self._drag
                if 0 <= k < len(self.project.cues):
                    cue = self.project.cues[k]
                    dur = max(0.0, _doc_num(cue.get("end"), 0.0)
                              - _doc_num(cue.get("start"), 0.0))
                    delta = (x - x0) * self._view_span() / max(1, self.width())
                    total = self._total()
                    new = max(0.0, start0 + delta)
                    new = min(new, max(0.0, total - dur))
                    cue["start"] = round(new, 3)
                    cue["end"] = round(new + dur, 3)
                self.update()
                return
            if kind == "audios":
                self.play_pos = self._time_at(x)
                self.update()
                return
            if kind == "scrub":
                self.play_pos = self._time_at(x)
                self.seeked.emit(self.play_pos)
                self.update()
                return
            if kind in ("trimL", "trimR"):
                _k, i, x0, _span0 = self._drag
                _spans, _total = self._spans()
                delta = (x - x0) * self._view_span() / max(1, self.width())
                alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                xs0, xs1, _d = _spans[i]
                edge = self._raw_time(xs0 if kind == "trimL" else xs1)
                snapped = self._snap_time(edge + delta, alt)
                self.trimmed.emit(i, "left" if kind == "trimL" else "right",
                                  snapped - edge)
                self._drag = (kind, i, x, _span0)   # 增量式：基准跟着鼠标走
                self.update()
                return
            if kind == "resize":
                _k, i, x0, d0 = self._drag
                _spans, _total = self._spans()
                start_t = sum(c.span for c in self.project.clips[:i])
                d = d0 + (x - x0) * self._view_span() / max(1, self.width())
                alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                end_t = self._snap_time(start_t + d, alt)
                self.resized.emit(i, max(0.05, end_t - start_t))
                self.update()
                return
            if kind in ("atrimL", "atrimR"):
                _k, idx, x0, _v = self._drag
                _spans, _total = self._spans()
                a = self.project.audios[idx]
                delta = (x - x0) * self._view_span() / max(1, self.width())
                alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                edge = a.start if kind == "atrimL" else a.start + a.dur
                snapped = self._snap_time(edge + delta, alt)
                self.audioTrimmed.emit(idx,
                                       "left" if kind == "atrimL" else "right",
                                       snapped - edge)
                self._drag = (kind, idx, x, 0.0)
                self.update()
                return
            if kind == "overlay":
                self.play_pos = self._time_at(x)
                self.update()
                return
            if kind == "env":
                _t, k, j, x0 = self._drag
                a = self.project.audios[k]
                if 0 <= j < len(a.points):
                    t, g = self._env_from_pos(k, x, y)
                    a.points[j] = (t, g)
                    a.points = clean_env_points(a.points)
                    idx = min(range(len(a.points)),
                              key=lambda q: abs(a.points[q][0] - t))
                    self.sel_env = (k, idx)
                    self._drag = ("env", k, idx, x0)
                self.update()
                return
            if kind == "audio":
                _k, idx, x0, start0 = self._drag
                self.play_pos = self._time_at(x)
                self.update()
                return
            if kind == "clip":
                self.play_pos = self._time_at(x)
                self.update()

        def _raw_time(self, x: float) -> float:
            """像素 → 时间，不夹范围（窗口外也能算出准确的时刻）。"""
            return self.view_start + float(x) * self._view_span() / max(
                1.0, float(self.width()))

        def _time_at(self, x: float) -> float:
            return timeline_time(x, self.view_start, self._view_span(),
                                 self.width(), self._total())

        def mouseReleaseEvent(self, ev):
            drag, self._drag = self._drag, None
            self.snap_guide = None
            self.drop_guide = None
            marquee, self._marquee = self._marquee, None
            self.update()
            self.setCursor(Qt.CursorShape.CrossCursor if self.razor
                           else Qt.CursorShape.PointingHandCursor)
            if not drag:
                return
            kind = drag[0]
            x = ev.position().x()
            if kind == "marquee":
                if marquee is not None and self.selected:
                    self.picked.emit(self.selected[0])
                return
            if kind == "clips":
                i = drag[1]
                rows = self.selected or [i]
                if abs(x - drag[2]) <= 6:
                    self.selected = [i]
                    self.picked.emit(i)
                    self.update()
                    return
                durations = [c.span for c in self.project.clips]
                dest = timeline_block_dest(durations, rows, self._time_at(x))
                self.blockMoved.emit(rows, dest)
                return
            if kind == "subtitle":
                _tag, k, x0, start0 = drag
                if abs(x - x0) > 6 and 0 <= k < len(self.project.cues):
                    self.subtitleMoved.emit(
                        k, float(self.project.cues[k].get("start") or 0.0))
                return
            if kind == "audios":
                _tag, idx, x0, start0, base = drag
                if abs(x - x0) <= 6:
                    return
                delta = (x - x0) * self._view_span() / max(1, self.width())
                a = self.project.audios[idx]
                start_t = start0 + delta
                alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                if self.snap_on and not alt:
                    cand = snap_candidates(self.project, self.play_pos)
                    tol = self._snap_tol()
                    head, head_hit = snap_value(start_t, cand, tol)
                    tail, tail_hit = snap_value(start_t + a.dur, cand, tol)
                    if head_hit is not None and (
                            tail_hit is None
                            or abs(head_hit - start_t) <= abs(
                                tail_hit - (start_t + a.dur))):
                        start_t = head
                    elif tail_hit is not None:
                        start_t = tail - a.dur
                delta = start_t - start0
                floor = min((s for _k, s in base), default=0.0)
                delta = max(delta, -floor)
                items = [(k, max(0.0, s + delta)) for k, s in base]
                self.audioGroupMoved.emit(items)
                return
            if kind == "clip":
                i = drag[1]
                if abs(x - drag[2]) > 6:
                    self.moved.emit(i, self._time_at(x))
                return
            if kind == "env":
                self.envelopeChanged.emit(drag[1])
                return
            if kind in ("atrimL", "atrimR"):
                self.audioTrimDone.emit(drag[1])
                return
            if kind == "overlay":
                _k, idx, x0, start0 = drag
                if abs(x - x0) <= 6:
                    return
                delta = (x - x0) * self._view_span() / max(1, self.width())
                self.overlayMoved.emit(idx, max(0.0, start0 + delta))
                return
            if kind == "audio":
                _k, idx, x0, start0 = drag
                if abs(x - x0) <= 6:
                    return
                delta = (x - x0) * self._view_span() / max(1, self.width())
                a = self.project.audios[idx]
                start_t = max(0.0, start0 + delta)
                alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                if self.snap_on and not alt:
                    # 头尾哪边离边更近就吸哪边
                    cand = snap_candidates(self.project, self.play_pos)
                    tol = self._snap_tol()
                    head, head_hit = snap_value(start_t, cand, tol)
                    tail, tail_hit = snap_value(start_t + a.dur, cand, tol)
                    if head_hit is not None and (
                            tail_hit is None
                            or abs(head_hit - start_t) <= abs(
                                tail_hit - (start_t + a.dur))):
                        start_t = max(0.0, head)
                    elif tail_hit is not None:
                        start_t = max(0.0, tail - a.dur)
                self.audioMoved.emit(idx, start_t)
                return

        def wheelEvent(self, ev):
            x = ev.position().x()
            dy = ev.angleDelta().y()
            mods = ev.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                # Ctrl+滚轮：以鼠标位置为中心缩放
                if dy:
                    self.zoom_at(x, dy > 0)
                ev.accept()
                return
            k = self._hit_audio(x, ev.position().y())
            if k >= 0 and not (mods & Qt.KeyboardModifier.ShiftModifier):
                step = 0.05 if dy > 0 else -0.05
                self.audioVolume.emit(k, step)
                ev.accept()
                return
            # 空白处或 Shift+滚轮：左右滚时间线
            if dy:
                self.scroll_by(-dy / 120.0 * self._view_span() * 0.12)
            ev.accept()


    class ColorWheel(QWidget):
        """色环（色相）+ 中间的饱和度/明度方块，按住就能拖。

        用户要求"选颜色别再填颜色代码，从色彩环里选"，就是它。
        """

        changed = pyqtSignal(str)      # #rrggbb

        def __init__(self, color="#3b82f6", parent=None):
            super().__init__(parent)
            self._h, self._s, self._v = theme._to_hsv(color or "#3b82f6")
            self.setMinimumSize(184, 184)
            self.setCursor(Qt.CursorShape.CrossCursor)

        # -- 颜色 ------------------------------------------------------
        def color(self) -> str:
            return theme._from_hsv(self._h, self._s, self._v)

        def set_color(self, value: str):
            if not is_hex_color(value):
                return
            self._h, self._s, self._v = theme._to_hsv(value)
            self.update()

        # -- 几何 ------------------------------------------------------
        def _geom(self):
            side = min(self.width(), self.height())
            cx, cy = self.width() / 2.0, self.height() / 2.0
            outer = side / 2.0 - 2
            inner = outer * 0.64
            sq = inner * 1.32
            return cx, cy, outer, inner, sq

        def paintEvent(self, ev):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            cx, cy, outer, inner, sq = self._geom()
            # 色环：0° 红 / 120° 绿 / 240° 蓝
            grad = QConicalGradient(cx, cy, 90)
            for i in range(37):
                grad.setColorAt(i / 36.0, QColor.fromHsvF((1 - i / 36.0) % 1.0,
                                                         1.0, 1.0))
            ring = QPainterPath()
            ring.setFillRule(Qt.FillRule.OddEvenFill)
            ring.addEllipse(cx - outer, cy - outer, outer * 2, outer * 2)
            ring.addEllipse(cx - inner, cy - inner, inner * 2, inner * 2)
            p.fillPath(ring, grad)
            # 中间的饱和度 × 明度方块（横 = 饱和度，竖 = 明度）
            x0, y0 = cx - sq / 2.0, cy - sq / 2.0
            base = QColor.fromHsvF(self._h / 360.0, 1.0, 1.0)
            gx = QLinearGradient(x0, 0, x0 + sq, 0)
            gx.setColorAt(0.0, QColor("#ffffff"))
            gx.setColorAt(1.0, base)
            p.fillRect(int(x0), int(y0), int(sq), int(sq), gx)
            gy = QLinearGradient(0, y0, 0, y0 + sq)
            gy.setColorAt(0.0, QColor(0, 0, 0, 0))
            gy.setColorAt(1.0, QColor(0, 0, 0, 255))
            p.fillRect(int(x0), int(y0), int(sq), int(sq), gy)
            p.setPen(QPen(QColor("#00000055"), 1))
            p.drawRect(int(x0), int(y0), int(sq), int(sq))
            # 色环上的手柄
            import math
            ang = math.radians(self._h)
            r = (outer + inner) / 2.0
            hx, hy = cx + r * math.cos(ang), cy - r * math.sin(ang)
            p.setPen(QPen(QColor("#ffffff"), 3))
            p.drawEllipse(int(hx) - 5, int(hy) - 5, 10, 10)
            p.setPen(QPen(QColor("#00000090"), 1))
            p.drawEllipse(int(hx) - 6, int(hy) - 6, 12, 12)
            # 方块里的手柄
            sx = x0 + sq * self._s
            sy = y0 + sq * (1 - self._v)
            p.setPen(QPen(QColor("#ffffff"), 2))
            p.drawEllipse(int(sx) - 5, int(sy) - 5, 10, 10)
            p.setPen(QPen(QColor("#00000090"), 1))
            p.drawEllipse(int(sx) - 6, int(sy) - 6, 12, 12)

        def _pick(self, pos):
            import math
            cx, cy, outer, inner, sq = self._geom()
            dx, dy = pos.x() - cx, pos.y() - cy
            dist = math.hypot(dx, dy)
            if dist >= inner:                       # 色环：改色相
                ang = math.degrees(math.atan2(-dy, dx)) % 360
                self._h = ang
            else:                                   # 方块：改饱和度 / 明度
                x0, y0 = cx - sq / 2.0, cy - sq / 2.0
                self._s = max(0.0, min(1.0, (pos.x() - x0) / sq))
                self._v = max(0.0, min(1.0, 1.0 - (pos.y() - y0) / sq))
            self.update()
            self.changed.emit(self.color())

        def mousePressEvent(self, ev):
            self._pick(ev.position())

        def mouseMoveEvent(self, ev):
            if ev.buttons() & Qt.MouseButton.LeftButton:
                self._pick(ev.position())

    class ColorWheelDialog(FramelessDialog):
        """色环取色 + 自动对撞色预览（留白颜色、主色都用它）。"""

        def __init__(self, color: str, title: str, parent=None):
            super().__init__(parent, title)
            self.setMinimumWidth(380)
            v = self.body
            self.wheel = ColorWheel(color, self)
            v.addWidget(self.wheel)
            row = QHBoxLayout()
            self.swatch = QLabel()
            self.swatch.setFixedSize(56, 32)
            self.lbl_hex = QLabel()
            self.lbl_hex.setStyleSheet("font-family:Consolas,monospace")
            row.addWidget(QLabel(tr("当前颜色")))
            row.addWidget(self.swatch)
            row.addWidget(self.lbl_hex)
            row.addStretch(1)
            v.addLayout(row)
            row2 = QHBoxLayout()
            self.swatch2 = QLabel()
            self.swatch2.setFixedSize(56, 32)
            self.lbl_hex2 = QLabel()
            row2.addWidget(QLabel(tr("自动对撞色")))
            row2.addWidget(self.swatch2)
            row2.addWidget(self.lbl_hex2)
            row2.addStretch(1)
            v.addLayout(row2)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(
                QDialogButtonBox.StandardButton.Ok).setText(tr("好"))
            buttons.button(
                QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            v.addWidget(buttons)
            self.wheel.changed.connect(self._sync)
            self._sync(self.wheel.color())

        def _sync(self, value):
            other = theme.complementary(value)
            self.swatch.setStyleSheet(
                f"background:{value};border:1px solid #808080;border-radius:6px")
            self.swatch2.setStyleSheet(
                f"background:{other};border:1px solid #808080;border-radius:6px")
            self.lbl_hex.setText(value)
            self.lbl_hex2.setText(other)

        def value(self) -> str:
            return self.wheel.color()

    def device_text() -> str:
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            return tr("未检测到输出设备")
        return dev.description() + tr("（跟随 Windows 默认，换设备自动跟）")

    class SettingsDialog(FramelessDialog):
        """设置：导出目录、画面规格、铺底、节奏默认值、声音与转场。

        预览音频不在这里选：它始终跟随 Windows 当前默认输出设备，
        系统里换耳机/扬声器时自动跟着换（见 Window._follow_audio）。
        """

        def __init__(self, settings: Settings, parent=None, project=None):
            super().__init__(parent, tr("设置"))
            self.s = settings
            self.project = project
            self.setMinimumWidth(620)
            self.tier = ui_tier(settings["ui"])
            self.pro = self.tier == "pro"
            self.daily = self.tier != "simple"   # 标准 / 专业：日常剪辑项都露出来
            # 要按档位藏的控件：pro 只给专业，normal 标准以上才给（标签和控件一起藏）
            self._hide_fields = {"normal": [], "pro": []}
            self._groups = {}       # 组名 -> QFormLayout
            self._order = []
            self._group = ""
            self._group_tier = {}

            def add_row(text, field, pro=False, normal=False):
                form = self._form()
                form.addRow(tr(text), field)
                gate = "pro" if pro else ("normal" if normal else "")
                if gate:
                    lab = form.labelForField(field)
                    if lab is not None:
                        self._hide_fields[gate].append(lab)
                    self._hide_fields[gate].append(field)
                return field

            def add_sep(text, pro=False, normal=False):
                """开始新的一组：专业模式一组一页，其余档位用细线连成一列。"""
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                form.setFieldGrowthPolicy(
                    QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                form.setContentsMargins(2, 10, 2, 4)
                self._order.append(text)
                self._group_tier[text] = "pro" if pro else (
                    "normal" if normal else "")
                self._groups[text] = form
                self._group = text
                if not self.pro and len(self._order) > 1:
                    line = QFrame()
                    line.setObjectName("sep")
                    line.setFrameShape(QFrame.Shape.HLine)
                    line.setFrameShadow(QFrame.Shadow.Plain)
                    lab = QLabel(tr(text))
                    lab.setObjectName("sectitle")
                    box = QWidget()
                    v = QVBoxLayout(box)
                    v.setContentsMargins(0, 9, 0, 3)
                    v.setSpacing(3)
                    v.addWidget(lab)
                    v.addWidget(line)
                    form.addRow(box)
                return form

            # 外观 / 导出 / 画面 / 音乐 / 节奏 / 其它：每组一条分隔线
            add_sep("外观")
            self.cmb_lang = QComboBox()
            self.cmb_lang.addItem(language_label(SYSTEM), SYSTEM)
            for code in LOCALES:
                self.cmb_lang.addItem(LOCALE_LABELS[code], code)
            idx = self.cmb_lang.findData(settings["lang"])
            self.cmb_lang.setCurrentIndex(idx if idx >= 0 else 0)
            add_row("界面语言", self.cmb_lang)

            self.cmb_theme = QComboBox()
            for k in theme.THEMES:
                self.cmb_theme.addItem(tr(theme.THEME_LABELS[k]), k)
            self.cmb_theme.setCurrentIndex(
                max(0, list(theme.THEMES).index(settings["theme"])))
            add_row("界面主题", self.cmb_theme)

            self._accent = settings["accent"]
            self.btn_accent = QPushButton()
            self.btn_accent.setToolTip(tr("点开从色环里选主色，对撞色自动算"))
            self.btn_accent.clicked.connect(self._pick_accent)
            self.cmb_preset = QComboBox()
            self.cmb_preset.setToolTip(tr("照各家视频站的品牌色来，也能自己调"))
            self.cmb_preset.addItem(tr("自定义"), "")
            for _name, _hex in theme.PRESETS:
                self.cmb_preset.addItem(tr(_name), _hex)
            self.cmb_preset.activated.connect(self._on_preset)
            row_accent = QHBoxLayout()
            row_accent.setContentsMargins(0, 0, 0, 0)
            row_accent.addWidget(self.cmb_preset, 2)
            row_accent.addWidget(self.btn_accent, 3)
            wrap_accent = QWidget()
            wrap_accent.setLayout(row_accent)
            add_row("主色", wrap_accent)
            self._sync_accent()

            add_sep("导出")
            self.edit_dir = QLineEdit(settings["export_dir"])
            self.edit_dir.setPlaceholderText(
                tr("留空 = 每段素材所在目录（关掉「先问我」时就用这里）"))
            browse = QPushButton(tr("浏览…"))
            browse.clicked.connect(self._browse_dir)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(self.edit_dir, 1)
            row.addWidget(browse)
            wrap = QWidget()
            wrap.setLayout(row)
            add_row("默认导出目录", wrap)

            self.chk_ask = QCheckBox(tr("导出时先问我保存到哪（关掉就直接存默认目录）"))
            self.chk_ask.setChecked(bool(settings["ask_export_dir"]))
            self._form().addRow(self.chk_ask)   # 自带文字，不需要左边标签

            self.chk_open = QCheckBox(tr("导出完自动打开所在文件夹"))
            self.chk_open.setChecked(bool(settings["open_after_export"]))
            self._form().addRow(self.chk_open)   # 自带文字，不需要左边标签

            self.cmb_win = QComboBox()
            for k in WIN_MODES:
                self.cmb_win.addItem(tr(WIN_LABELS[k]), k)
            self.cmb_win.setCurrentIndex(
                max(0, list(WIN_MODES).index(settings["win_mode"])))
            self.cmb_win.activated.connect(self._sync_win_mode)
            add_row("启动时窗口", self.cmb_win, normal=True)
            self.spin_win_w = QSpinBox()
            self.spin_win_w.setRange(800, 8000)
            self.spin_win_w.setSingleStep(20)
            self.spin_win_w.setValue(int(settings["win_fixed_w"]))
            self.spin_win_h = QSpinBox()
            self.spin_win_h.setRange(560, 8000)
            self.spin_win_h.setSingleStep(20)
            self.spin_win_h.setValue(int(settings["win_fixed_h"]))
            row_win = QHBoxLayout()
            row_win.setContentsMargins(0, 0, 0, 0)
            row_win.addWidget(self.spin_win_w, 1)
            row_win.addWidget(QLabel("x"))
            row_win.addWidget(self.spin_win_h, 1)
            wrap_win = QWidget()
            wrap_win.setLayout(row_win)
            add_row("固定大小", wrap_win, normal=True)
            self._sync_win_mode()

            add_sep("画面")
            self._syncing = True
            self.cmb_ratio = QComboBox()
            for r in RATIOS:
                self.cmb_ratio.addItem(tr(r), r)
            self.cmb_ratio.setCurrentIndex(max(0, RATIOS.index(settings["ratio"])))
            self.cmb_ratio.activated.connect(self._apply_ratio)
            add_row("画面比例", self.cmb_ratio)

            self.spin_w = QSpinBox()
            self.spin_h = QSpinBox()
            for sp in (self.spin_w, self.spin_h):
                sp.setRange(16, 4096)
                sp.setSingleStep(2)
                sp.setAccelerated(True)
                sp.valueChanged.connect(self._on_size_edit)
            self.spin_w.setValue(even(settings["width"]))
            self.spin_h.setValue(even(settings["height"]))
            row_size = QHBoxLayout()
            row_size.setContentsMargins(0, 0, 0, 0)
            row_size.addWidget(self.spin_w)
            row_size.addWidget(QLabel("×"))
            row_size.addWidget(self.spin_h)
            hint = QLabel(tr("（选比例会自动填，也可手改）"))
            self._hide_fields["normal"].append(hint)
            row_size.addWidget(hint)
            row_size.addStretch(1)
            wrap_size = QWidget()
            wrap_size.setLayout(row_size)
            add_row("分辨率（宽×高）", wrap_size, normal=True)

            self.lbl_size = QLabel()
            add_row("成片尺寸", self.lbl_size)
            self._syncing = False

            self.cmb_fps = QComboBox()
            self.cmb_fps.addItems(["30", "60"])
            self.cmb_fps.setCurrentText(str(settings["fps"]))
            add_row("帧率", self.cmb_fps, normal=True)

            self.cmb_quality = QComboBox()
            for k in QUALITY:
                self.cmb_quality.addItem(tr(k), k)
            self.cmb_quality.setCurrentIndex(max(0, list(QUALITY).index(settings["quality"])))
            add_row("画质", self.cmb_quality)

            self.cmb_fit = QComboBox()
            for k in FIT_MODES:
                self.cmb_fit.addItem(fit_label(k), k)
            self.cmb_fit.setCurrentIndex(list(FIT_MODES).index(settings["fit"]))
            self.cmb_fit.currentIndexChanged.connect(self._sync_fit)
            add_row("画面铺底", self.cmb_fit)

            self.edit_intro = QLineEdit(settings["intro_path"])
            self.edit_intro.setPlaceholderText(
                tr("视频文件（留空 = 不加片头）"))
            btn_intro = QPushButton(tr("浏览…"))
            btn_intro.clicked.connect(lambda: self._browse_clip(self.edit_intro))
            wrap_intro = QWidget()
            row_intro = QHBoxLayout()
            row_intro.setContentsMargins(0, 0, 0, 0)
            row_intro.addWidget(self.edit_intro, 1)
            row_intro.addWidget(btn_intro)
            wrap_intro.setLayout(row_intro)
            add_row("默认片头视频", wrap_intro, normal=True)

            self.edit_outro = QLineEdit(settings["outro_path"])
            self.edit_outro.setPlaceholderText(
                tr("视频文件（留空 = 不加片尾）"))
            btn_outro = QPushButton(tr("浏览…"))
            btn_outro.clicked.connect(lambda: self._browse_clip(self.edit_outro))
            wrap_outro = QWidget()
            row_outro = QHBoxLayout()
            row_outro.setContentsMargins(0, 0, 0, 0)
            row_outro.addWidget(self.edit_outro, 1)
            row_outro.addWidget(btn_outro)
            wrap_outro.setLayout(row_outro)
            add_row("默认片尾视频", wrap_outro, normal=True)

            self._color = settings["pad_color"]
            self.btn_color = QPushButton(self._color)
            self.btn_color.setToolTip(tr("纯色留白用的底色"))
            self.btn_color.clicked.connect(self._pick_color)
            add_row("留白颜色", self.btn_color, normal=True)
            self._sync_color_btn()

            self.edit_bg = QLineEdit(settings["bg_path"])
            self.edit_bg.setPlaceholderText(
                tr("背景图片或视频（「自定义背景」才用）"))
            btn_bg = QPushButton(tr("浏览…"))
            btn_bg.clicked.connect(self._browse_bg)
            row_bg = QHBoxLayout()
            row_bg.setContentsMargins(0, 0, 0, 0)
            row_bg.addWidget(self.edit_bg, 1)
            row_bg.addWidget(btn_bg)
            wrap_bg = QWidget()
            wrap_bg.setLayout(row_bg)
            add_row("背景文件", wrap_bg, normal=True)

            self.chk_bg_blur = QCheckBox(
                tr("背景模糊（原图铺底与自定义背景都适用）"))
            self.chk_bg_blur.setChecked(bool(settings["bg_blur"]))
            add_row("背景处理", self.chk_bg_blur, normal=True)

            self.cmb_crop = QComboBox()
            for key in CROP_FOCUS:
                self.cmb_crop.addItem(tr(CROP_LABELS[key]), key)
            self.cmb_crop.setCurrentIndex(
                max(0, list(CROP_FOCUS).index(settings["crop_focus"])))
            add_row("裁切位置", self.cmb_crop, normal=True)

            self.edit_lut = QLineEdit(settings["lut_dir"])
            self.edit_lut.setPlaceholderText(
                tr("放着滤镜文件（.cube）的目录（右键「调色…」时从这个目录里列）"))
            btn_lut = QPushButton(tr("浏览…"))
            btn_lut.clicked.connect(self._browse_lut)
            row_lut = QHBoxLayout()
            row_lut.setContentsMargins(0, 0, 0, 0)
            row_lut.addWidget(self.edit_lut, 1)
            row_lut.addWidget(btn_lut)
            wrap_lut = QWidget()
            wrap_lut.setLayout(row_lut)
            add_row("滤镜目录", wrap_lut, normal=True)

            self.cmb_sub_font = QFontComboBox()
            self.cmb_sub_font.setCurrentFont(QFont(str(settings["subtitle_font"])))
            self.cmb_sub_font.setToolTip(
                tr("烧进成片的字幕用这个字体；缺字体就换成别的"))
            add_row("字幕字体", self.cmb_sub_font, normal=True)

            self.spin_sub_size = QSpinBox()
            self.spin_sub_size.setRange(16, 120)
            self.spin_sub_size.setValue(int(settings["subtitle_size"]))
            self.spin_sub_size.setSuffix(tr(" 像素"))
            add_row("字幕字号", self.spin_sub_size, normal=True)

            add_sep("配音", normal=True)
            keys = settings["tts_keys"] or {}

            def key_row(text, name, hint):
                ed_ = QLineEdit(str(keys.get(name, "")))
                ed_.setEchoMode(QLineEdit.EchoMode.Password)
                ed_.setPlaceholderText(tr(hint))
                add_row(text, ed_, normal=True)
                return ed_

            self.lbl_tts_note = QLabel(tr(
                "Edge 配音免费、不用密钥；Gemini / Azure / OpenAI 兼容端点要填自己的密钥。"
                "密钥明文存在本机设置文件里，不会上传到别处。"))
            self.lbl_tts_note.setObjectName("hint")
            self.lbl_tts_note.setWordWrap(True)
            self._form().addRow(self.lbl_tts_note)
            self._hide_fields["normal"].append(self.lbl_tts_note)
            self.edit_key_gemini = key_row(
                "Gemini key", "gemini", "用 Gemini 配音时填这个")
            self.edit_key_azure = key_row(
                "Azure key", "azure", "用 Azure 语音时填这个")
            self.edit_key_openai = key_row(
                "OpenAI 兼容 key", "openai", "用 OpenAI 兼容端点配音时填这个")
            self.edit_azure_region = QLineEdit(settings["tts_azure_region"])
            self.edit_azure_region.setPlaceholderText(tr("例如 eastasia"))
            add_row("Azure 区域", self.edit_azure_region, normal=True)
            self.edit_tts_base = QLineEdit(settings["tts_base_url"])
            self.edit_tts_base.setPlaceholderText(
                tr("例如 https://api.siliconflow.cn/v1（留空 = 官方地址）"))
            add_row("OpenAI 兼容地址", self.edit_tts_base, normal=True)
            self.edit_tts_model = QLineEdit(settings["tts_model"])
            self.edit_tts_model.setPlaceholderText(
                tr("例如 FunAudioLLM/CosyVoice2-0.5B（留空 = tts-1）"))
            add_row("模型名", self.edit_tts_model, normal=True)
            self.edit_tts_proxy = QLineEdit(settings["tts_proxy"])
            self.edit_tts_proxy.setPlaceholderText(
                tr("例如 http://127.0.0.1:7890（留空 = 直连）"))
            add_row("配音代理", self.edit_tts_proxy, normal=True)
            self.spin_tts_gap = QDoubleSpinBox()
            self.spin_tts_gap.setRange(0.0, 5.0)
            self.spin_tts_gap.setDecimals(1)
            self.spin_tts_gap.setSingleStep(0.1)
            self.spin_tts_gap.setSuffix(tr(" 秒"))
            self.spin_tts_gap.setValue(float(settings["tts_gap"]))
            add_row("段间停顿", self.spin_tts_gap, normal=True)
            self.chk_tts_cleanup = QCheckBox(
                tr("合成完顺手清一下音频（去咔哒、去噗声、剪掉多余静音）"))
            self.chk_tts_cleanup.setChecked(bool(settings["tts_cleanup"]))
            self._form().addRow(self.chk_tts_cleanup)
            self._hide_fields["normal"].append(self.chk_tts_cleanup)

            add_sep("资源包", pro=True)
            self.list_assets = QListWidget()
            self.list_assets.setMinimumHeight(96)
            add_row("已装资源包", self.list_assets, pro=True)
            self.lbl_assets = QLabel(tr(
                "字体包、滤镜包、转场预设包都能从 zip 装进来。"
                "字体装好后能在这里的「字幕字体」里选，滤镜会出现在右键「调色…」里，"
                "转场预设会出现在右键「转场」里。"))
            self.lbl_assets.setObjectName("hint")
            self.lbl_assets.setWordWrap(True)
            self.lbl_assets.setMinimumWidth(360)
            row_asset = QHBoxLayout()
            row_asset.setContentsMargins(0, 0, 0, 0)
            self.btn_asset_add = QPushButton(tr("导入 zip…"))
            self.btn_asset_add.clicked.connect(self._import_asset)
            self.btn_asset_del = QPushButton(tr("删除所选"))
            self.btn_asset_del.clicked.connect(self._remove_asset)
            self.btn_asset_open = QPushButton(tr("打开资源文件夹"))
            self.btn_asset_open.setToolTip(
                tr("在资源管理器里看这些包装在哪"))
            self.btn_asset_open.clicked.connect(self._open_assets)
            row_asset.addWidget(self.btn_asset_add)
            row_asset.addWidget(self.btn_asset_del)
            row_asset.addWidget(self.btn_asset_open)
            row_asset.addStretch(1)
            wrap_asset = QWidget()
            wrap_asset.setLayout(row_asset)
            add_row("导入 / 删除", wrap_asset, pro=True)
            self._form().addRow(self.lbl_assets)
            self._hide_fields["pro"].append(self.lbl_assets)
            self._refresh_assets()

            add_sep("音乐", normal=True)
            self.spin_mstart = QDoubleSpinBox()
            self.spin_mstart.setRange(0.0, 36000.0)
            self.spin_mstart.setDecimals(1)
            self.spin_mstart.setSingleStep(1.0)
            self.spin_mstart.setSuffix(tr(" 秒"))
            self.spin_mstart.setValue(float(settings["music_start"]))
            add_row("音乐起点", self.spin_mstart, normal=True)

            self.spin_mlen = QDoubleSpinBox()
            self.spin_mlen.setRange(0.0, 36000.0)
            self.spin_mlen.setDecimals(1)
            self.spin_mlen.setSingleStep(1.0)
            self.spin_mlen.setSuffix(tr(" 秒"))
            self.spin_mlen.setSpecialValueText(tr("整首"))
            self.spin_mlen.setValue(float(settings["music_len"]))
            add_row("音乐长度", self.spin_mlen, normal=True)

            self.spin_fin = QDoubleSpinBox()
            self.spin_fin.setRange(0.0, 10.0)
            self.spin_fin.setDecimals(1)
            self.spin_fin.setSingleStep(0.1)
            self.spin_fin.setSuffix(tr(" 秒"))
            self.spin_fin.setValue(float(settings["fade_in"]))
            add_row("音乐淡入", self.spin_fin, normal=True)

            self.spin_fout = QDoubleSpinBox()
            self.spin_fout.setRange(0.0, 30.0)
            self.spin_fout.setDecimals(1)
            self.spin_fout.setSingleStep(0.1)
            self.spin_fout.setSuffix(tr(" 秒"))
            self.spin_fout.setValue(float(settings["fade_out"]))
            add_row("音乐淡出", self.spin_fout, normal=True)

            self.spin_vol = QSpinBox()
            self.spin_vol.setRange(0, 200)
            self.spin_vol.setSingleStep(5)
            self.spin_vol.setSuffix("%")
            self.spin_vol.setValue(int(round(float(settings["music_volume"]) * 100)))
            add_row("音乐音量", self.spin_vol, normal=True)

            add_sep("节奏与动效", normal=True)
            self.cmb_mode = QComboBox()
            for k in ("bgm", "total", "free"):
                self.cmb_mode.addItem(mode_label(k), k)
            self.cmb_mode.setCurrentIndex(("bgm", "total", "free").index(settings["mode"]))
            add_row("默认节奏", self.cmb_mode, normal=True)

            self.spin_per_image = QDoubleSpinBox()
            self.spin_per_image.setRange(MIN_DUR, 3600.0)
            self.spin_per_image.setDecimals(1)
            self.spin_per_image.setSingleStep(0.5)
            self.spin_per_image.setSuffix(tr(" 秒"))
            self.spin_per_image.setValue(float(settings["per_image"]))
            add_row("逐张模式默认", self.spin_per_image, normal=True)

            self.cmb_motion = QComboBox()
            for key in MOTIONS:
                self.cmb_motion.addItem(tr(MOTION_LABELS[key]), key)
            self.cmb_motion.setCurrentIndex(
                max(0, MOTIONS.index(settings["motion"])))
            add_row("画面动效", self.cmb_motion, normal=True)

            self.cmb_trans = QComboBox()
            self.cmb_trans.addItem(tr("硬切"), "none")
            self.cmb_trans.addItem(tr("淡入淡出（每段首尾淡黑）"), "fade")
            self.cmb_trans.setCurrentIndex(1 if settings["transition"] == "fade" else 0)
            self.cmb_trans.currentIndexChanged.connect(self._sync_trans)
            add_row("片段转场", self.cmb_trans, normal=True)

            self.spin_trans = QDoubleSpinBox()
            self.spin_trans.setRange(0.0, 2.0)
            self.spin_trans.setDecimals(1)
            self.spin_trans.setSingleStep(0.1)
            self.spin_trans.setSuffix(tr(" 秒"))
            self.spin_trans.setValue(float(settings["transition_sec"]))
            add_row("转场时长", self.spin_trans, normal=True)

            add_sep("其它", pro=True)
            # 组件：出片引擎 ffmpeg、音源 yt-dlp —— 按需下载、按需删除
            self._workers = {}
            self._comp = {}
            for key in components.ORDER:
                spec = components.COMPONENTS[key]
                lbl = QLabel()
                lbl.setWordWrap(True)
                btn = QPushButton(tr("下载"))
                btn.clicked.connect(
                    lambda _c=False, k=key: self._toggle_component(k))
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(lbl, 1)
                row.addWidget(btn)
                wrap = QWidget()
                wrap.setLayout(row)
                add_row(spec["label"], wrap, pro=True)
                self._comp[key] = (lbl, btn)
                self._sync_component(key)

            # 音频设备（只读，自动跟随）
            self.lbl_audio = QLabel(device_text())
            self.lbl_audio.setStyleSheet("color:#9ca3af")
            add_row("预览音频输出", self.lbl_audio, pro=True)

            # 错误日志：闪退了、导出失败了，先看这份记录
            self.lbl_log = QLabel(tr("闪退 / 导出失败时先看这份记录：{path}",
                                     path=log_path()))
            self.lbl_log.setWordWrap(True)
            self.lbl_log.setStyleSheet("color:#9ca3af")
            btn_log = QPushButton(tr("打开日志"))
            btn_log.clicked.connect(self._open_log)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(self.lbl_log, 1)
            row.addWidget(btn_log)
            wrap = QWidget()
            wrap.setLayout(row)
            add_row("错误日志", wrap, pro=True)
            add_row("版本", QLabel(APP_VERSION), pro=True)

            add_sep("快捷键", pro=True)
            self._seq = {}
            for key in SHORTCUT_ORDER:
                ed = QKeySequenceEdit(QKeySequence(
                    (settings["shortcuts"] or {}).get(key,
                                                      SHORTCUT_DEFAULTS[key])))
                ed.setMaximumSequenceLength(1)
                self._seq[key] = ed
                add_row(SHORTCUT_LABELS[key], ed, pro=True)
            self.btn_sc_reset = QPushButton(tr("恢复默认"))
            self.btn_sc_reset.clicked.connect(self._reset_shortcuts)
            self.lbl_sc_hint = QLabel(tr("默认照剪辑软件的惯例；点一下再按键就能改"))
            self.lbl_sc_hint.setStyleSheet("color:#9ca3af")
            self.lbl_sc_hint.setWordWrap(True)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(self.lbl_sc_hint, 1)
            row.addWidget(self.btn_sc_reset)
            wrap = QWidget()
            wrap.setLayout(row)
            add_row("快捷键默认", wrap, pro=True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(
                QDialogButtonBox.StandardButton.Ok).setText(tr("好"))
            buttons.button(
                QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            outer = self.body
            if self.pro:                      # 专业：一组一页，省得竖着太长
                tabs = QTabWidget()
                for name in self._order:
                    page = QWidget()
                    page.setLayout(self._groups[name])
                    tabs.addTab(page, tr(name))
                outer.addWidget(tabs, 1)
            else:                             # 简洁 / 标准：一列，按档位藏
                hide = set(self._hide_fields["pro"])
                if not self.daily:
                    hide.update(self._hide_fields["normal"])
                for w_ in hide:
                    w_.setVisible(False)
                holder = QWidget()
                v = QVBoxLayout(holder)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(4)
                for name in self._order:
                    form = self._groups[name]
                    g = self._group_tier.get(name) or ""
                    if g == "pro" or (g == "normal" and not self.daily):
                        page = QWidget(self)   # 专业组的控件得有父，挂到隐藏页上
                        page.setLayout(form)
                        page.hide()
                        continue
                    v.addLayout(form)
                outer.addWidget(holder, 1)
            outer.addWidget(buttons)

            self._sync_size_label()
            self._sync_fit()
            self._sync_trans()

        def _form(self) -> QFormLayout:
            """当前这一组的表单（add_row / 直接 form.addRow 都走它）。"""
            return self._groups[self._group]

        def _apply_ratio(self, *_a):
            size = preset_size(self.cmb_ratio.currentData())
            if size:
                self._syncing = True
                self.spin_w.setValue(size[0])
                self.spin_h.setValue(size[1])
                self._syncing = False
            self._sync_size_label()

        def _on_size_edit(self, *_a):
            if self._syncing or not hasattr(self, "lbl_size"):
                return
            if self.cmb_ratio.currentData() != "自定义":
                self._syncing = True
                self.cmb_ratio.setCurrentIndex(RATIOS.index("自定义"))
                self._syncing = False
            self._sync_size_label()

        def _sync_size_label(self, *_a):
            w, h = even(self.spin_w.value()), even(self.spin_h.value())
            self.lbl_size.setText(
                tr("{w} x {h}（{ratio}）", w=w, h=h,
                   ratio=self.cmb_ratio.currentText()))

        def _sync_fit(self, *_a):
            fit = self.cmb_fit.currentData()
            self.btn_color.setEnabled(fit == "color")
            self.edit_bg.setEnabled(fit in BG_MODES)

        @staticmethod
        def _swatch(color: str, size: int = 14) -> QIcon:
            pm = QPixmap(size, size)
            pm.fill(QColor(color))
            return QIcon(pm)

        def _sync_color_btn(self):
            self.btn_color.setText(self._color)
            self.btn_color.setIcon(self._swatch(self._color))

        def _pick_color(self):
            dlg = ColorWheelDialog(self._color, tr("选留白颜色"), self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._color = dlg.value()
                self._sync_color_btn()

        def _sync_accent(self):
            acc = self._accent
            acc2 = theme.complementary(acc)
            self.btn_accent.setText(tr("{a}（对撞色 {b}）", a=acc, b=acc2))
            self.btn_accent.setIcon(self._swatch(acc))
            idx = self.cmb_preset.findData(acc.lower())
            self.cmb_preset.setCurrentIndex(idx if idx > 0 else 0)

        def _on_preset(self, index: int):
            hex_value = self.cmb_preset.itemData(index) or ""
            if not hex_value:            # 「自定义」= 直接开色环
                self._pick_accent()
                return
            self._accent = hex_value
            self._sync_accent()

        def _pick_accent(self):
            dlg = ColorWheelDialog(self._accent, tr("选主色"), self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._accent = dlg.value()
                self._sync_accent()

        def _browse_clip(self, edit):
            picked = FileDialog.get_open_files(
                self, tr("选视频"), edit.text() or "",
                tr("视频 (*.mp4 *.mov *.mkv *.webm *.avi)"))
            if picked:
                edit.setText(picked[0])

        def _sync_win_mode(self, *_a):
            fixed = self.cmb_win.currentData() == "fixed"
            self.spin_win_w.setEnabled(fixed)
            self.spin_win_h.setEnabled(fixed)

        def _browse_bg(self):
            video = self.cmb_fit.currentData() == "video"
            cap = tr("选背景视频") if video else tr("选背景图片")
            flt = (tr("视频 (*.mp4 *.mov *.mkv *.webm)") if video
                   else tr("图片或视频 (*.jpg *.jpeg *.png *.bmp *.webp *.mp4 *.mov *.mkv *.webm *.avi)"))
            picked = FileDialog.get_open_files(
                self, cap, self.edit_bg.text() or "", flt)
            if picked:
                self.edit_bg.setText(picked[0])

        def _browse_lut(self):
            picked = FileDialog.get_dir(
                self, tr("选 滤镜目录"), self.edit_lut.text().strip() or "")
            if picked:
                self.edit_lut.setText(picked)

        def _sync_trans(self, *_a):
            self.spin_trans.setEnabled(self.cmb_trans.currentData() == "fade")

        # -- 组件（ffmpeg / yt-dlp）：下载与删除 ---------------------------
        def _open_log(self):
            """把日志选中打开：反馈问题时让用户直接把这份发过来。"""
            path = log_path()
            try:
                if not os.path.exists(path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    open(path, "a", encoding="utf-8").close()
                subprocess.Popen(["explorer", "/select,",
                                  os.path.normpath(path)])
            except OSError:
                pass

        def _sync_component(self, key: str):
            lbl, btn = self._comp[key]
            spec = components.COMPONENTS[key]
            btn.setEnabled(True)
            if components.have(key):
                lbl.setText(tr("已安装 {v} · {mb} MB（占地方可以随时删）",
                               v=components.version(key) or spec["label"],
                               mb=components.size_mb(key)))
                btn.setText(tr("删除"))
                return
            found = components.exe(key)
            if found:
                short = found if len(found) <= 52 else "…" + found[-51:]
                lbl.setText(tr("先用系统里这份：{path}（想随身带就在这下一份）",
                               path=short))
            else:
                lbl.setText(tr(spec["hint"]))
            btn.setText(tr("下载"))

        def _toggle_component(self, key: str):
            w = self._workers.get(key)
            if w is not None and w.isRunning():
                return
            lbl, btn = self._comp[key]
            if components.have(key):
                components.remove(key)
                components.prepend_path()
                self._sync_component(key)
                return
            btn.setEnabled(False)
            lbl.setText(tr("正在下载…"))
            w = ComponentWorker(key, self)
            self._workers[key] = w
            w.note.connect(lbl.setText)
            w.done.connect(lambda _v, k=key: self._on_component_done(k))
            w.bad.connect(lambda m, k=key: self._on_component_bad(k, m))
            w.start()

        def _on_component_done(self, key: str):
            self._sync_component(key)

        def _on_component_bad(self, key: str, msg: str):
            self._sync_component(key)
            MsgBox.warn(self, components.COMPONENTS[key]["label"], msg)

        def _browse_dir(self):
            d = FileDialog.get_dir(self, tr("选默认导出目录"),
                                   self.edit_dir.text() or "")
            if d:
                self.edit_dir.setText(d)

        # ---------------- 资源包
        def _refresh_assets(self):
            self.list_assets.clear()
            pkgs = installed_asset_packages()
            for pkg in pkgs:
                ver = (" v" + pkg["version"]) if pkg["version"] else ""
                item = QListWidgetItem(
                    tr("{kind} · {name}{ver}",
                       kind=tr(ASSET_KIND_LABELS[pkg["kind"]]),
                       name=pkg["name"], ver=ver))
                item.setData(Qt.ItemDataRole.UserRole, pkg)
                self.list_assets.addItem(item)
            self.btn_asset_del.setEnabled(bool(pkgs))

        def _pick_asset(self):
            item = self.list_assets.currentItem()
            if item is None:
                return None
            data = item.data(Qt.ItemDataRole.UserRole)
            return data if isinstance(data, dict) else None

        def _import_asset(self):
            files = FileDialog.get_open_files(
                self, tr("选资源包"), "", tr("资源包 (*.zip)"))
            if not files:
                return
            try:
                pkg = install_asset_zip(files[0])
            except AssetError as exc:
                MsgBox.warn(self, tr("资源包没装上"), asset_error_text(exc))
                return
            if pkg["kind"] == "font":
                register_asset_fonts()
            self._refresh_assets()
            self.list_assets.setCurrentRow(self.list_assets.count() - 1)
            MsgBox.info(self, tr("资源包装好了"),
                        tr("{kind}「{name}」已经装进资源目录：\n{path}",
                           kind=tr(ASSET_KIND_LABELS[pkg["kind"]]),
                           name=pkg["name"],
                           path=asset_dir(pkg["kind"])))

        def _open_assets(self):
            """资源包装在哪，让用户自己看一眼。"""
            folder = assets_root()
            try:
                os.makedirs(folder, exist_ok=True)
                subprocess.Popen(["explorer", os.path.normpath(folder)])
            except OSError:
                pass

        def _remove_asset(self):
            pkg = self._pick_asset()
            if not pkg:
                return
            if not MsgBox.ask(
                    self, tr("删掉资源包"),
                    tr("确定删掉「{name}」吗？正在用它的工程会找不到文件。",
                       name=pkg["name"]),
                    tr("删掉"), tr("算了"), danger=True):
                return
            used = asset_usage(self.project, pkg,
                               self.cmb_sub_font.currentFont().family())
            if not remove_asset_package(pkg):
                MsgBox.warn(self, tr("删不掉"),
                            tr("这个包的文件删不掉，可能被别的程序占着。"))
                return
            self._refresh_assets()
            if used:
                MsgBox.warn(
                    self, tr("资源包已经删掉"),
                    tr("当前工程里还有地方在用它：{what}。渲染前先把这些地方换掉。",
                       what="、".join(used)))

        def _reset_shortcuts(self):
            for key, ed in self._seq.items():
                ed.setKeySequence(QKeySequence(SHORTCUT_DEFAULTS[key]))

        def _shortcut_values(self) -> dict:
            return {k: (ed.keySequence().toString().strip()
                        or SHORTCUT_DEFAULTS[k])
                    for k, ed in self._seq.items()}

        def accept(self):
            seqs = self._shortcut_values()
            seen = {}
            for k in SHORTCUT_ORDER:
                seq = seqs[k]
                if seq in seen:
                    MsgBox.warn(
                        self, tr("快捷键冲突"),
                        tr("「{a}」和「{b}」都设成了 {key}，改一个再来。",
                           a=tr(SHORTCUT_LABELS[seen[seq]]),
                           b=tr(SHORTCUT_LABELS[k]), key=seq))
                    return
                seen[seq] = k
            self.s.update(
                shortcuts=seqs,
                lang=self.cmb_lang.currentData(),
                theme=self.cmb_theme.currentData(),
                accent=self._accent,
                export_dir=self.edit_dir.text().strip(),
                open_after_export=bool(self.chk_open.isChecked()),
                ask_export_dir=bool(self.chk_ask.isChecked()),
                ratio=self.cmb_ratio.currentData(),
                width=even(self.spin_w.value()),
                height=even(self.spin_h.value()),
                bg_path=self.edit_bg.text().strip(),
                lut_dir=self.edit_lut.text().strip(),
                pad_color=self._color,
                bg_blur=bool(self.chk_bg_blur.isChecked()),
                fps=int(self.cmb_fps.currentText()),
                quality=self.cmb_quality.currentData(),
                fit=self.cmb_fit.currentData(),
                mode=self.cmb_mode.currentData(),
                per_image=float(self.spin_per_image.value()),
                fade_in=float(self.spin_fin.value()),
                fade_out=float(self.spin_fout.value()),
                transition=self.cmb_trans.currentData(),
                motion=self.cmb_motion.currentData(),
                music_volume=float(self.spin_vol.value()) / 100.0,
                music_start=float(self.spin_mstart.value()),
                music_len=float(self.spin_mlen.value()),
                crop_focus=self.cmb_crop.currentData(),
                subtitle_font=self.cmb_sub_font.currentFont().family(),
                subtitle_size=int(self.spin_sub_size.value()),
                transition_sec=float(self.spin_trans.value()),
                win_mode=self.cmb_win.currentData(),
                win_fixed_w=int(self.spin_win_w.value()),
                win_fixed_h=int(self.spin_win_h.value()),
                intro_path=self.edit_intro.text().strip(),
                outro_path=self.edit_outro.text().strip(),
                tts_keys={
                    "gemini": self.edit_key_gemini.text().strip(),
                    "azure": self.edit_key_azure.text().strip(),
                    "openai": self.edit_key_openai.text().strip(),
                },
                tts_azure_region=self.edit_azure_region.text().strip() or "eastasia",
                tts_base_url=self.edit_tts_base.text().strip(),
                tts_model=self.edit_tts_model.text().strip(),
                tts_proxy=self.edit_tts_proxy.text().strip(),
                tts_gap=float(self.spin_tts_gap.value()),
                tts_cleanup=bool(self.chk_tts_cleanup.isChecked()),
            )
            super().accept()

    def app_palette(pal) -> QPalette:
        """给 Qt 自己画的部件（文件框里的导航图标等）也配一套颜色。"""
        role = QPalette.ColorRole
        p = QPalette()
        p.setColor(role.Window, QColor(pal["window"]))
        p.setColor(role.WindowText, QColor(pal["text"]))
        p.setColor(role.Base, QColor(pal["field"]))
        p.setColor(role.AlternateBase, QColor(pal["row_alt"]))
        p.setColor(role.Text, QColor(pal["text"]))
        p.setColor(role.Button, QColor(pal["panel"]))
        p.setColor(role.ButtonText, QColor(pal["text"]))
        p.setColor(role.BrightText, QColor(pal["accent_fg"]))
        p.setColor(role.Highlight, QColor(pal["accent"]))
        p.setColor(role.HighlightedText, QColor(pal["accent_fg"]))
        p.setColor(role.ToolTipBase, QColor(pal["panel"]))
        p.setColor(role.ToolTipText, QColor(pal["text"]))
        p.setColor(role.PlaceholderText, QColor(pal["muted"]))
        p.setColor(role.Mid, QColor(pal["border"]))
        p.setColor(role.Dark, QColor(pal["border"]))
        p.setColor(role.Light, QColor(pal["hover"]))
        return p

    class SeekSlider(QSlider):
        """预览进度条：点滑轨任意位置滑块就过去，拖滑块照旧。

        Qt 的 QSlider 点槽只翻一页（pageStep），pageStep=0 时完全没有反应，
        所以按下/拖动/松开都自己按滑轨几何算数值。
        """

        def _value_at(self, x: float) -> int:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            groove = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderGroove, self)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, opt,
                QStyle.SubControl.SC_SliderHandle, self)
            return slider_value_from_x(
                x, groove.x(), groove.width(), handle.width(),
                self.minimum(), self.maximum(), opt.upsideDown)

        def mousePressEvent(self, ev):
            if ev.button() != Qt.MouseButton.LeftButton:
                return super().mousePressEvent(ev)
            # setSliderDown 自己会发 sliderPressed，别重复发
            self.setValue(self._value_at(ev.position().x()))
            self.setSliderDown(True)
            self.sliderMoved.emit(self.value())
            ev.accept()

        def mouseMoveEvent(self, ev):
            if self.isSliderDown():
                self.setValue(self._value_at(ev.position().x()))
                self.sliderMoved.emit(self.value())
                ev.accept()
            else:
                super().mouseMoveEvent(ev)

        def mouseReleaseEvent(self, ev):
            if not self.isSliderDown():
                return super().mouseReleaseEvent(ev)
            self.setValue(self._value_at(ev.position().x()))
            self.sliderMoved.emit(self.value())
            self.setSliderDown(False)   # 自己会发 sliderReleased
            ev.accept()

    class PreviewCanvas(QWidget):
        """预览画面：自己把 Scene 画出来的 QImage 贴上去（不再是 QVideoWidget）。"""

        clicked = pyqtSignal()     # 点画面＝播放 / 暂停

        def __init__(self, parent=None):
            super().__init__(parent)
            self._img = None
            self._press = None
            self.setMinimumSize(480, 270)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setAutoFillBackground(True)
            pal = self.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#000000"))
            self.setPalette(pal)
            self.setSizePolicy(QSizePolicy.Policy.Expanding,
                               QSizePolicy.Policy.Expanding)

        def set_frame(self, img):
            self._img = img
            self.update()

        def mousePressEvent(self, ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                self._press = ev.position()
            super().mousePressEvent(ev)

        def mouseReleaseEvent(self, ev):
            # 按下又松开都在画面里、且没拖远才算「点了一下」
            if (ev.button() == Qt.MouseButton.LeftButton
                    and self._press is not None):
                d = ev.position() - self._press
                if abs(d.x()) < 6 and abs(d.y()) < 6:
                    self.clicked.emit()
            self._press = None
            super().mouseReleaseEvent(ev)

        def paintEvent(self, _ev):
            qp = QPainter(self)
            qp.fillRect(self.rect(), QColor("#000000"))
            img = self._img
            if img is None or img.isNull() or img.width() <= 0:
                qp.end()
                return
            k = min(self.width() / float(img.width()),
                    self.height() / float(img.height()))
            w = max(1, int(img.width() * k))
            h = max(1, int(img.height() * k))
            x = (self.width() - w) // 2
            y = (self.height() - h) // 2
            qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            qp.drawImage(QRect(x, y, w, h), img)
            qp.end()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowType.FramelessWindowHint)
            self.setWindowIcon(_win_icon(32))
            self.setWindowTitle(tr("Pic2Video 出片 —— 图片 / 视频 + 站内音乐 → 视频"))
            self.setObjectName("frame")         # 配 QSS 那一圈细边
            self.resize(1180, 800)
            self.setAcceptDrops(True)
            self.settings = Settings()
            set_locale(self.settings["lang"])
            apply_qt_translator(current_locale())
            self.preview_name = ""
            self.project = Project(fps=int(self.settings["fps"]))
            self._recent_menus = []
            self.project.mode = self.settings["mode"]
            self.tmpdir = tempfile.mkdtemp(prefix="pic2video_")
            self._scrubbing = False        # 正在拖进度条：别让播放位置抢它
            self._resume_at = 0.0          # 重渲后要跳回的位置
            self.preview_stale = True
            self.worker = None
            self.fetch_worker = None
            self._rebuilding = False
            self._render_kind = "export"
            self._render_t0 = 0.0
            self._maxed = False
            self._geo_before_max = None
            self.history = []          # 撤回栈：每步一份项目 + 设置快照
            self.hist_index = -1
            self._hist_lock = True     # 建界面时不要记步骤
            self.project_path = ""     # 当前工程文件；空 = 还没存过
            self._dirty = False        # 有没有没存的改动
            self._cover_plan = None    # 这次导出要不要顺带截封面
            self.autosaver = AutoSaver(   # 崩溃/断电后救命的那份
                os.path.join(components.data_dir(), "自动保存.p2v"), 15.0)

            # 实时预览：画面由 Scene 现画，音轨单独混成一条当主时钟
            self.scene = None
            self._audio_worker = None
            self._audio_sig = None
            self._audio_seq = 0
            self._audio_pending = False
            self._play_after_mix = False
            # 配音页：后台合成线程、试听用的第二个播放器
            self._tts_worker = None
            self._tts_audition = False
            self._audition_player = None
            self._audition_audio = None
            self._mix_timer = QTimer(self)
            self._mix_timer.setSingleShot(True)
            self._mix_timer.setInterval(250)
            self._mix_timer.timeout.connect(self._start_audio_mix)
            self._frame_timer = QTimer(self)
            self._frame_timer.setInterval(16)
            self._frame_timer.timeout.connect(self._tick_frame)
            self._frame_timer.start()
            # 方向键 / Home / End 全局步进播放头（输入框里不抢）
            QApplication.instance().installEventFilter(self)

            self.player = QMediaPlayer(self)
            self.audio_out = QAudioOutput(self)
            self.audio_out.setVolume(0.7)
            self.player.setAudioOutput(self.audio_out)

            self._build_ui()
            self._bind_shortcuts()
            self._restore_geometry()
            self.retranslate()
            self.apply_theme()
            self._follow_audio()
            try:
                QMediaDevices.audioOutputsChanged.connect(self._follow_audio)
            except Exception:  # noqa: BLE001
                pass
            try:   # Windows 切深色/浅色时，主题设成"跟随系统"的窗口跟着变
                QApplication.instance().styleHints().colorSchemeChanged.connect(
                    self._on_system_theme)
            except Exception:  # noqa: BLE001
                pass
            self._refresh_all()
            self._history_reset(tr("打开"))
            self._hist_lock = False
            QTimer.singleShot(0, self._maybe_restore_autosave)

        def _bind_shortcuts(self):
            """按设置绑快捷键（默认照剪辑软件惯例，设置里能逐条改）。"""
            for sc in getattr(self, "_shortcuts", []):
                sc.setEnabled(False)
                sc.deleteLater()
            self._shortcuts = []
            bound = dict(SHORTCUT_DEFAULTS)
            bound.update(self.settings["shortcuts"] or {})
            slots = {"add": self.on_add, "export": self.on_export,
                     "undo": self.on_undo, "redo": self.on_redo,
                     "play": self.toggle_play, "remove": self.on_remove,
                     "preview": self.on_preview}
            for key in SHORTCUT_ORDER:
                seq = (bound.get(key) or SHORTCUT_DEFAULTS[key]).strip()
                slot = slots.get(key)
                if not seq or slot is None:
                    continue
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(slot)
                self._shortcuts.append(sc)
            # 工程文件这几个不进设置表：剪辑软件的习惯，固定住
            for seq, slot in (("Ctrl+N", self.on_new_project),
                              ("Ctrl+O", self.on_open_project),
                              ("Ctrl+S", self.on_save_project),
                              ("Ctrl+Shift+S", self.on_save_project_as),
                              ("I", self.on_mark_in),
                              ("O", self.on_mark_out),
                              ("Ctrl+K", self.on_split_at)):
                sc = QShortcut(QKeySequence(seq), self)
                sc.activated.connect(slot)
                self._shortcuts.append(sc)

        # ---------------- 工程文件
        def _project_name(self) -> str:
            return (os.path.basename(self.project_path) if self.project_path
                    else tr("未命名工程"))

        def _project_dir(self) -> str:
            if self.project_path:
                return os.path.dirname(self.project_path)
            return (self.settings["last_export_dir"]
                    or os.path.expanduser("~"))

        def _refresh_title(self):
            """标题栏：工程名 + 有改动的小圆点。"""
            star = " ●" if self._dirty else ""
            text = f"{self._project_name()}{star} —— Pic2Video"
            self.setWindowTitle(text)
            self.titlebar.set_title(text)

        def _mark_dirty(self):
            """改了一步：标题点上圆点，自动保存排上队。"""
            if not self._dirty:
                self._dirty = True
                self._refresh_title()
            self.autosaver.mark_dirty()

        def _autosave_tick(self):
            if self._dirty and self.autosaver.tick(self.project):
                log("autosave -> %s" % self.autosaver.path)

        def _fill_project_actions(self, menu: QMenu):
            """工程那几项：工程按钮和简洁模式的「⋯」共用一套。"""
            act = menu.addAction(tr("新建工程"))
            act.triggered.connect(self.on_new_project)
            act = menu.addAction(tr("打开工程…"))
            act.triggered.connect(self.on_open_project)
            recent = menu.addMenu(tr("最近打开"))
            recent.aboutToShow.connect(self._refresh_recent_menu)
            self.menu_recent = recent
            self._recent_menus.append(recent)
            menu.addSeparator()
            act = menu.addAction(tr("保存工程"))
            act.triggered.connect(self.on_save_project)
            act = menu.addAction(tr("另存为…"))
            act.triggered.connect(self.on_save_project_as)

        def _build_project_menu(self) -> QMenu:
            menu = QMenu(self)
            self._fill_project_actions(menu)
            return menu

        def _build_more_menu(self) -> QMenu:
            """简洁模式的「⋯」：工程 + 设置，别让用户找不到工程文件。"""
            menu = QMenu(self)
            self._fill_project_actions(menu)
            menu.addSeparator()
            act = menu.addAction(tr("设置"))
            act.triggered.connect(self.on_settings)
            return menu

        def _build_ui_menu(self) -> QMenu:
            """三档模式：简洁 / 标准 / 专业，勾当前那档。"""
            menu = QMenu(self)
            group = QActionGroup(menu)
            group.setExclusive(True)
            self._ui_actions = {}
            for tier in UI_TIERS:
                act = menu.addAction(tr(UI_TIER_LABELS[tier]))
                act.setCheckable(True)
                act.setActionGroup(group)
                act.triggered.connect(
                    lambda _checked=False, t=tier: self.on_set_ui(t))
                self._ui_actions[tier] = act
            return menu

        def _refresh_recent_menu(self):
            recent = self.settings.recent()
            for menu in getattr(self, "_recent_menus", []):
                menu.clear()
                if not recent:
                    act = menu.addAction(tr("（还没有）"))
                    act.setEnabled(False)
                    continue
                for path in recent:
                    act = menu.addAction(os.path.basename(path))
                    act.setToolTip(path)
                    act.triggered.connect(
                        lambda _checked=False, _p=path:
                        self.on_open_recent(_p))

        def _confirm_discard(self) -> bool:
            """有没存的改动先问一句。返回 False = 用户取消，整个动作别继续。"""
            if not self._dirty:
                return True
            picked = MsgBox.choose(
                self, tr("工程还没存"),
                tr("「%s」有改动还没保存，先存下来吗？") % self._project_name(),
                [(tr("保存"), "primary"), (tr("不保存"), "plain"),
                 (tr("取消"), "plain")], "ask")
            if picked == 0:
                return self.on_save_project()
            return picked == 1

        def on_new_project(self, _checked=False):
            if not self._confirm_discard():
                return
            project = Project(fps=int(self.settings["fps"]))
            project.mode = self.settings["mode"]
            self.project = project
            self.project_path = ""
            self._dirty = False
            self.autosaver.clear()
            self._refresh_all()
            self._history_reset(tr("新建工程"))
            self._refresh_title()

        def on_open_project(self, _checked=False):
            if not self._confirm_discard():
                return
            path, _f = QFileDialog.getOpenFileName(
                self, tr("打开工程"), self._project_dir(), PROJECT_FILTER)
            if path:
                self._open_project_file(path)

        def on_open_recent(self, path: str):
            if not os.path.isfile(path):
                MsgBox.warn(self, tr("打不开"),
                            tr("这个工程文件已经不在了：\n%s") % path)
                keep = [p for p in self.settings.data.get("recent_projects", [])
                        if p != path]
                self.settings.data["recent_projects"] = keep
                self.settings.save()
                return
            if self._confirm_discard():
                self._open_project_file(path)

        def _open_project_file(self, path: str) -> bool:
            try:
                project = load_project(path)
            except ProjectFileError as exc:
                MsgBox.warn(self, tr("打不开这个工程"), project_error_text(exc))
                return False
            self.project = project
            self.settings.data["fps"] = int(project.fps)
            self.settings.data["mode"] = project.mode
            self.settings.save()
            self.project_path = path
            self._dirty = False
            self.autosaver.clear()
            self.settings.push_recent(path)
            self._refresh_all()
            self._history_reset(tr("打开工程"))
            self._refresh_title()
            missing = self.project.missing_media()
            if missing:
                self._warn_missing(missing)
            return True

        def _warn_missing(self, missing: list[str]):
            names = "、".join(os.path.basename(p) for p in missing[:6])
            more = (tr(" 等 %d 个") % len(missing)) if len(missing) > 6 else ""
            picked = MsgBox.choose(
                self, tr("有素材找不到了"),
                tr("下面这些素材不在了，成片里会缺画面：\n%s%s\n"
                   "把它们放回原来的位置，或者重新导入。") % (names, more),
                [(tr("重新定位…"), "primary"), (tr("先不管"), "plain")], "warn")
            if picked == 0:
                self._relink_missing(missing)

        def _relink_from_folder(self, missing: list[str]) -> set:
            """整个文件夹搬走了：选一次文件夹，按文件名自动对上。"""
            if not missing:
                return set()
            start = os.path.dirname(missing[0]) or self._project_dir()
            folder = QFileDialog.getExistingDirectory(
                self, tr("选这些素材现在所在的文件夹"), start)
            if not folder:
                return set()
            done = set()
            for old in missing:
                if os.path.exists(old):
                    continue
                cand = os.path.join(folder, os.path.basename(old))
                if os.path.isfile(cand) and self.project.relink_media(old, cand):
                    done.add(old)
                    log("relink %s -> %s" % (old, cand))
            return done

        def _relink_missing(self, missing: list[str]):
            """挨个问新位置；同一条素材被引用几次就一起换掉。"""
            changed = False
            todo = [p for p in missing if not os.path.exists(p)]
            done = set()
            if len(todo) >= 2:
                picked = MsgBox.choose(
                    self, tr("一起重新定位"),
                    tr("有 {n} 个素材不见了。要是它们还在同一个文件夹里，"
                       "可以一次选文件夹，按文件名自动对上。", n=len(todo)),
                    [(tr("选文件夹…"), "primary"), (tr("一个个来"), "plain")],
                    "ask")
                if picked == 0:
                    done = self._relink_from_folder(todo)
                    if done:
                        changed = True
            for old in todo:
                if old in done or os.path.exists(old):
                    continue
                ext = os.path.splitext(old)[1].lower()
                filt = tr("同一类文件 (*%s)") % ext if ext else ""
                new, _f = QFileDialog.getOpenFileName(
                    self, tr("重新定位：%s") % os.path.basename(old),
                    os.path.dirname(old) or self._project_dir(), filt)
                if not new:
                    continue          # 这一个先跳过，条目原样留着
                if self.project.relink_media(old, new):
                    changed = True
            if changed:
                self._mark_dirty()
                self._refresh_all()
            left = self.project.missing_media()
            if left:
                self._warn_missing(left)

        def on_save_project(self, _checked=False) -> bool:
            if not self.project_path:
                return self.on_save_project_as()
            return self._write_project(self.project_path)

        def on_save_project_as(self, _checked=False) -> bool:
            default = self.project_path or os.path.join(
                self._project_dir(), tr("未命名工程") + PROJECT_SUFFIX)
            path, _f = QFileDialog.getSaveFileName(
                self, tr("工程另存为"), default, PROJECT_FILTER)
            if not path:
                return False
            if not path.lower().endswith(PROJECT_SUFFIX):
                path += PROJECT_SUFFIX
            return self._write_project(path)

        def _write_project(self, path: str) -> bool:
            try:
                save_project(self.project, path)
            except ProjectFileError as exc:
                MsgBox.warn(self, tr("保存失败"), project_error_text(exc))
                return False
            self.project_path = path
            self._dirty = False
            self.autosaver.clear()
            self.settings.push_recent(path)
            self._refresh_title()
            if hasattr(self, "lbl_status"):
                self.lbl_status.setText(tr("工程已保存：{path}", path=path))
            return True

        def _maybe_restore_autosave(self):
            """上次没正常退出？把自动保存捞回来。"""
            if self.project_path or os.environ.get("PIC2VIDEO_SHOT"):
                return
            path = self.autosaver.path
            if not AutoSaver.restorable(path):
                return
            try:
                when = time.strftime("%m-%d %H:%M",
                                     time.localtime(os.path.getmtime(path)))
            except OSError:
                when = ""
            picked = MsgBox.choose(
                self, tr("上次好像没正常退出"),
                tr("发现一份自动保存的工程（%s）。恢复它吗？") % when,
                [(tr("恢复"), "primary"), (tr("不要"), "plain")], "ask")
            if picked != 0:
                self.autosaver.clear()
                return
            try:
                project = load_project(path)
            except ProjectFileError as exc:
                MsgBox.warn(self, tr("恢复失败"), project_error_text(exc))
                self.autosaver.clear()
                return
            self.project = project
            self.project_path = ""
            self.settings.data["fps"] = int(project.fps)
            self.settings.save()
            self._dirty = True
            self._refresh_all()
            self._history_reset(tr("恢复自动保存"))
            self._refresh_title()
            missing = self.project.missing_media()
            if missing:
                self._warn_missing(missing)

        # ---------------- 撤回 / 还原
        _HISTORY_MAX = 120

        def _state_now(self) -> dict:
            """当前状态：项目全量 + 设置（窗口几何与上次导出目录不算操作）。"""
            keep = {k: v for k, v in self.settings.data.items()
                    if k not in ("win_x", "win_y", "win_w", "win_h",
                                 "last_export_dir")}
            return {"project": self.project.to_state(), "settings": keep}

        def _history_reset(self, label: str):
            self.history = [{"label": label, "t": time.time(),
                             "state": self._state_now()}]
            self.hist_index = 0
            self._refresh_history()

        def _history_push(self, label: str):
            """记一步。拖时长会连发几十次，1.2 秒内的同类操作合并成一步。"""
            if self._hist_lock:
                return
            self._mark_dirty()      # 有编辑就有没存的改动，自动保存排上队
            now = time.time()
            self.history = self.history[:self.hist_index + 1]
            last = self.history[-1] if self.history else None
            if (last is not None and last["label"] == label
                    and now - last["t"] < 1.2):
                last["state"] = self._state_now()
                last["t"] = now
                self._refresh_history()
                return
            self.history.append({"label": label, "t": now,
                                 "state": self._state_now()})
            if len(self.history) > self._HISTORY_MAX:
                self.history.pop(0)
            self.hist_index = len(self.history) - 1
            self._refresh_history()

        def _refresh_history(self):
            lst = getattr(self, "hist_list", None)
            if lst is None:
                return
            lst.blockSignals(True)
            lst.clear()
            for i, e in enumerate(self.history):
                lst.addItem(f"{i + 1}. {e['label']}")
            if 0 <= self.hist_index < lst.count():
                lst.setCurrentRow(self.hist_index)
            lst.blockSignals(False)
            can_undo = self.hist_index > 0
            can_redo = self.hist_index < len(self.history) - 1
            for b, on in ((getattr(self, "btn_undo", None), can_undo),
                          (getattr(self, "btn_redo", None), can_redo),
                          (getattr(self, "btn_undo_bar", None), can_undo),
                          (getattr(self, "btn_redo_bar", None), can_redo)):
                if b is not None:
                    b.setEnabled(on)

        def _history_apply(self, index: int):
            if not (0 <= index < len(self.history)):
                return
            st = self.history[index]["state"]
            self.hist_index = index
            self._hist_lock = True
            try:
                self.project.load_state(st["project"])
                self.settings.data.update(st["settings"])
                self.settings.save()
                set_locale(self.settings["lang"])
                apply_qt_translator(current_locale())
                self.apply_theme()
                self.retranslate()
                self._bind_shortcuts()   # 撤回可能把快捷键改回去了
                self._refresh_history()
            finally:
                self._hist_lock = False
            self._mark_stale()

        def on_undo(self):
            if self.hist_index > 0:
                log("undo -> %d" % (self.hist_index - 1))
                self._history_apply(self.hist_index - 1)

        def on_redo(self):
            if self.hist_index < len(self.history) - 1:
                log("redo -> %d" % (self.hist_index + 1))
                self._history_apply(self.hist_index + 1)

        def on_history_click(self, item):
            row = self.hist_list.row(item)
            log("history click -> %d" % row)
            self._history_apply(row)

        # ---------------- 窗口大小 / 位置
        def _restore_geometry(self):
            """启动窗口：按设置记住上次 / 直接最大化 / 固定宽高。"""
            s = self.settings
            mode = s["win_mode"]
            if mode == "max":
                self.maximize_to_screen()
                return
            if mode == "fixed":
                w = max(800, int(s["win_fixed_w"]))
                h = max(560, int(s["win_fixed_h"]))
                self.resize(w, h)
                scr = QApplication.primaryScreen()
                if scr is not None:
                    a = scr.availableGeometry()
                    self.move(a.x() + max(0, (a.width() - w) // 2),
                              a.y() + max(0, (a.height() - h) // 2))
                return
            w, h = int(s["win_w"]), int(s["win_h"])
            x, y = int(s["win_x"]), int(s["win_y"])
            if w >= 800 and h >= 560:
                self.resize(w, h)
            if x < 0 or y < 0:
                return
            for scr in QApplication.screens():
                a = scr.availableGeometry()
                if (a.x() - 200 < x < a.x() + a.width() - 120
                        and a.y() - 100 < y < a.y() + a.height() - 80):
                    self.move(x, y)
                    return

        def is_maxed(self):
            """自绘窗口的「最大化」：Qt 的 isMaximized() 对无边框窗口不可靠。"""
            return self._maxed

        def maximize_to_screen(self):
            if not self._maxed:
                self._geo_before_max = self.geometry()
            scr = self.screen() or QApplication.primaryScreen()
            if scr is not None:
                self.setGeometry(scr.availableGeometry())
            self._maxed = True
            self.titlebar.sync_max()

        def restore_down(self):
            self._maxed = False
            if self._geo_before_max is not None:
                self.setGeometry(self._geo_before_max)
            self.titlebar.sync_max()

        def toggle_maximum(self):
            if self._maxed:
                self.restore_down()
            else:
                self.maximize_to_screen()

        def _save_geometry(self):
            s = self.settings
            if self._maxed and self._geo_before_max is not None:
                g = self._geo_before_max
            else:
                g = self.geometry()
            s["win_w"], s["win_h"] = g.width(), g.height()
            s["win_x"], s["win_y"] = g.x(), g.y()
            s.save()

        # ---------------- UI
        def _build_ui(self):
            root = QWidget()
            outer = QVBoxLayout(root)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(0)
            self.titlebar = TitleBar(
                self, tr("Pic2Video 出片 —— 图片 / 视频 + 站内音乐 → 视频"), maximize=True)
            outer.addWidget(self.titlebar)
            body = QWidget()
            v = QVBoxLayout(body)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(8)
            outer.addWidget(body, 1)

            bar = QHBoxLayout()
            self.btn_project = QToolButton()
            self.btn_project.setPopupMode(
                QToolButton.ToolButtonPopupMode.InstantPopup)
            self.btn_project.setMenu(self._build_project_menu())
            bar.addWidget(self.btn_project)
            def btn(text, slot, tip="", signal="clicked"):
                b = QPushButton(text)
                getattr(b, signal).connect(slot)
                if tip:
                    b.setToolTip(tip)
                bar.addWidget(b)
                return b
            self.btn_add = btn(tr("加素材"), self.on_add)
            self.btn_add.setMinimumWidth(78)
            self.btn_overlay = btn(
                tr("画中画"), self.on_add_overlay,
                tr("压一层画面在成片上：图片或视频都行"))
            self.btn_up = btn(tr("上移"), lambda: self.on_move(-1))
            self.btn_down = btn(tr("下移"), lambda: self.on_move(1))
            self.btn_remove = btn(tr("删除"), self.on_remove)
            self.btn_snap = btn(tr("磁吸"), self.on_toggle_snap,
                                tr("吸附：拖动时贴住附近的边（Alt 临时关）"),
                                "toggled")
            self.btn_snap.setCheckable(True)
            self.btn_snap.setChecked(bool(self.settings["snap_on"]))
            self.btn_razor = btn(tr("切割"), self.on_toggle_razor,
                                 tr("切割模式：点哪块切哪块（Esc 退出）"),
                                 "toggled")
            self.btn_razor.setCheckable(True)
            self.btn_undo_bar = btn(tr("撤回"), self.on_undo, tr("撤销上一步（Ctrl+Z）"))
            self.btn_redo_bar = btn(tr("还原"), self.on_redo, tr("重做（Ctrl+Shift+Z）"))
            self.btn_settings = btn(
                tr("设置"), self.on_settings,
                tr("导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏"))
            bar.addSpacing(12)
            self.cmb_rhythm = QComboBox()
            self.cmb_rhythm.addItem(tr(MODE_LABELS["bgm"]), "bgm")
            self.cmb_rhythm.addItem(tr(MODE_LABELS["material"]), "material")
            self.cmb_rhythm.activated.connect(
                lambda _i: self.on_mode(self.cmb_rhythm.currentData()))
            self.radio_total = QRadioButton(tr("自设总长"))
            self.radio_free = QRadioButton(tr("逐段秒数"))
            self.radio_voice = QRadioButton(tr("跟旁白"))
            self.radio_voice.setToolTip(
                tr("按配音页生成的旁白长度铺满画面，画面不会比声音长"))
            bar.addWidget(self.cmb_rhythm)
            group = QButtonGroup(self)
            for r in (self.radio_total, self.radio_free, self.radio_voice):
                group.addButton(r)
                bar.addWidget(r)
            self.spin_total = QDoubleSpinBox()
            self.spin_total.setRange(0.5, 3600.0)
            self.spin_total.setSingleStep(0.5)
            self.spin_total.setSuffix(tr(" 秒"))
            self.spin_total.setDecimals(1)
            self.spin_total.setValue(10.0)
            self.spin_total.setEnabled(False)
            self.spin_total.setFixedWidth(96)
            bar.addWidget(self.spin_total)
            # 这一行字解释当前时长是按谁算的，省得四个控件互相打架
            self.lbl_mode_hint = QLabel()
            self.lbl_mode_hint.setObjectName("hint")
            bar.addWidget(self.lbl_mode_hint)
            bar.addStretch(1)
            self.btn_refresh = QPushButton(tr("刷新预览"))
            self.btn_refresh.clicked.connect(self.on_preview)
            bar.addWidget(self.btn_refresh)
            self.btn_export = QPushButton(tr("导出视频"))
            self.btn_export.setDefault(True)
            self.btn_export.setStyleSheet("QPushButton{padding:4px 14px;font-weight:600}")
            self.btn_export.clicked.connect(self.on_export)
            bar.addWidget(self.btn_export)
            bar.addSpacing(6)
            # 三档：简洁 / 标准 / 专业。点开是菜单，选哪档当场切
            self.btn_mode = QToolButton()
            self.btn_mode.setPopupMode(
                QToolButton.ToolButtonPopupMode.InstantPopup)
            self.btn_mode.setMenu(self._build_ui_menu())
            self.btn_mode.setStyleSheet("QToolButton{padding:3px 10px}")
            bar.addWidget(self.btn_mode)
            # 简洁模式的入口：右上角「⋯」里放工程与设置
            self.btn_more = QToolButton()
            self.btn_more.setText("⋯")
            self.btn_more.setFixedWidth(34)
            self.btn_more.setPopupMode(
                QToolButton.ToolButtonPopupMode.InstantPopup)
            self.btn_more.setMenu(self._build_more_menu())
            self.btn_more.setToolTip(tr("更多：工程与设置"))
            bar.addWidget(self.btn_more)
            v.addLayout(bar)

            split = QSplitter(Qt.Orientation.Horizontal)
            self.list = ImageList()
            self.list.setIconSize(QSize(96, 54))
            self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.list.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection)
            self.list.setMinimumWidth(300)
            self.list.setStyleSheet("QListWidget::item{padding:4px 2px;}")
            self.list.order_changed.connect(self.on_rows_moved)
            self.list.drop_files.connect(self.on_drop_into_list)
            self.list.itemDoubleClicked.connect(self.on_edit_dur)
            self.list.currentRowChanged.connect(self.on_row_changed)
            self.list.itemSelectionChanged.connect(self.on_selection_changed)
            self.list.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.on_list_menu)
            self.left_tabs = QTabWidget()
            self.left_tabs.addTab(self.list, tr("素材"))
            hist_page = QWidget()
            hvp = QVBoxLayout(hist_page)
            hvp.setContentsMargins(0, 0, 0, 0)
            hvp.setSpacing(6)
            self.hist_list = QListWidget()
            self.hist_list.setAlternatingRowColors(True)
            self.hist_list.itemClicked.connect(self.on_history_click)
            hvp.addWidget(self.hist_list, 1)
            hb = QHBoxLayout()
            self.btn_undo = QPushButton(tr("撤回"))
            self.btn_undo.clicked.connect(self.on_undo)
            self.btn_redo = QPushButton(tr("还原"))
            self.btn_redo.clicked.connect(self.on_redo)
            hb.addWidget(self.btn_undo)
            hb.addWidget(self.btn_redo)
            hb.addStretch(1)
            hvp.addLayout(hb)
            self.left_tabs.addTab(hist_page, tr("历史"))
            sub_page = QWidget()
            svp = QVBoxLayout(sub_page)
            svp.setContentsMargins(0, 0, 0, 0)
            svp.setSpacing(6)
            self.lbl_subs = QLabel(tr("还没有字幕"))
            self.lbl_subs.setWordWrap(True)
            svp.addWidget(self.lbl_subs)
            self.sub_list = QListWidget()
            self.sub_list.setAlternatingRowColors(True)
            self.sub_list.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection)
            svp.addWidget(self.sub_list, 1)
            sb = QHBoxLayout()
            self.btn_sub_add = QPushButton(tr("导入字幕…"))
            self.btn_sub_add.setToolTip(tr("支持 .srt 与 .ass，烧进成片"))
            self.btn_sub_add.clicked.connect(self.on_import_subtitle)
            self.btn_sub_del = QPushButton(tr("移除字幕"))
            self.btn_sub_del.clicked.connect(self.on_clear_subtitle)
            self.btn_sub_save = QPushButton(tr("另存 .srt…"))
            self.btn_sub_save.setToolTip(
                tr("把当前字幕存成 .srt：时间轴按成片时间算，能直接贴到别处"))
            self.btn_sub_save.clicked.connect(self.on_save_subtitle)
            sb.addWidget(self.btn_sub_add)
            sb.addWidget(self.btn_sub_del)
            sb.addWidget(self.btn_sub_save)
            sb.addStretch(1)
            svp.addLayout(sb)
            self.left_tabs.addTab(sub_page, tr("字幕"))
            # 配音页：文稿 -> TTS -> 旁白 + 字幕，一条龙在这个页里做完
            voice_page = QWidget()
            vvp = QVBoxLayout(voice_page)
            vvp.setContentsMargins(0, 0, 0, 0)
            vvp.setSpacing(6)
            self.lbl_script = QLabel(tr("把文稿贴进来：一句一行、或者整段都行，会自动切段"))
            self.lbl_script.setWordWrap(True)
            vvp.addWidget(self.lbl_script)
            self.edit_script = QPlainTextEdit()
            self.edit_script.setPlaceholderText(tr("在这里贴文稿……"))
            self.edit_script.setPlainText(str(self.settings["tts_script"] or ""))
            self.edit_script.textChanged.connect(self.on_script_changed)
            vvp.addWidget(self.edit_script, 1)
            srow = QHBoxLayout()
            self.btn_script_open = QPushButton(tr("打开文稿…"))
            self.btn_script_open.clicked.connect(self.on_open_script)
            self.btn_script_clear = QPushButton(tr("清空"))
            self.btn_script_clear.clicked.connect(self.on_clear_script)
            srow.addWidget(self.btn_script_open)
            srow.addWidget(self.btn_script_clear)
            srow.addStretch(1)
            vvp.addLayout(srow)
            self.lbl_plan = QLabel(tr("还没有文稿"))
            self.lbl_plan.setWordWrap(True)
            vvp.addWidget(self.lbl_plan)
            vform = QFormLayout()
            vform.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            vform.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self.cmb_tts_provider = QComboBox()
            for _key in tts_engine.TTS_PROVIDER_ORDER:
                self.cmb_tts_provider.addItem(
                    tr(tts_engine.TTS_PROVIDERS[_key]["label"]), _key)
            _idx = self.cmb_tts_provider.findData(
                str(self.settings["tts_provider"]))
            self.cmb_tts_provider.setCurrentIndex(max(0, _idx))
            self.cmb_tts_provider.currentIndexChanged.connect(
                lambda _i: self.on_tts_provider_changed())
            vform.addRow(tr("配音服务"), self.cmb_tts_provider)
            self.cmb_tts_voice = QComboBox()
            self.cmb_tts_voice.setEditable(True)
            self.cmb_tts_voice.setToolTip(
                tr("下拉里是常用音色，也能直接敲服务商的音色名"))
            self._fill_voices(str(self.settings["tts_provider"]),
                              str(self.settings["tts_voice"]))
            vform.addRow(tr("音色"), self.cmb_tts_voice)
            self.cmb_tts_voice_b = QComboBox()
            self.cmb_tts_voice_b.setEditable(True)
            self.cmb_tts_voice_b.setToolTip(
                tr("对话稿里「听友」用这个音色"))
            self._fill_voices(str(self.settings["tts_provider"]),
                              str(self.settings["tts_voice_b"]),
                              self.cmb_tts_voice_b)
            vform.addRow(tr("音色 B"), self.cmb_tts_voice_b)
            self.spin_tts_rate = QSpinBox()
            self.spin_tts_rate.setRange(tts_engine.TTS_MIN_RATE,
                                        tts_engine.TTS_MAX_RATE)
            self.spin_tts_rate.setSingleStep(5)
            self.spin_tts_rate.setSuffix("%")
            self.spin_tts_rate.setValue(int(self.settings["tts_rate"]))
            self.spin_tts_rate.setToolTip(tr("0 是原速；正数更快，负数更慢"))
            vform.addRow(tr("语速"), self.spin_tts_rate)
            self._tts_form = vform
            vvp.addLayout(vform)
            self.progress_tts = QProgressBar()
            self.progress_tts.setVisible(False)
            self.progress_tts.setFormat(tr("正在合成 %v/%m 段"))
            vvp.addWidget(self.progress_tts)
            self.lbl_tts_status = QLabel(tr("贴好文稿，选个音色，就能合成"))
            self.lbl_tts_status.setWordWrap(True)
            vvp.addWidget(self.lbl_tts_status)
            trow = QHBoxLayout()
            self.btn_tts_start = QPushButton(tr("开始合成"))
            self.btn_tts_start.clicked.connect(self.on_tts_start)
            self.btn_tts_cancel = QPushButton(tr("取消"))
            self.btn_tts_cancel.setEnabled(False)
            self.btn_tts_cancel.clicked.connect(self.on_tts_cancel)
            self.btn_tts_audition = QPushButton(tr("试听"))
            self.btn_tts_audition.setToolTip(tr("只合成头一段，听听音色合不合适"))
            self.btn_tts_audition.clicked.connect(self.on_tts_audition)
            trow.addWidget(self.btn_tts_start)
            trow.addWidget(self.btn_tts_cancel)
            trow.addWidget(self.btn_tts_audition)
            trow.addStretch(1)
            vvp.addLayout(trow)
            self.chk_tts_prefix = QCheckBox(tr("字幕带「主讲：」这样的前缀"))
            self.chk_tts_prefix.setChecked(bool(self.settings["tts_prefix"]))
            self.chk_tts_prefix.setToolTip(
                tr("勾上以后字幕会写成「主讲：……」，不勾只写台词"))
            vform.addRow(tr("字幕前缀"), self.chk_tts_prefix)
            self._show_dialogue(False)
            vvp.addSpacing(4)
            self.lbl_narration = QLabel(tr("还没有旁白"))
            self.lbl_narration.setWordWrap(True)
            self.lbl_narration.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            vvp.addWidget(self.lbl_narration)
            nrow = QHBoxLayout()
            self.btn_narr_open = QPushButton(tr("打开旁白文件夹"))
            self.btn_narr_open.setEnabled(False)
            self.btn_narr_open.setToolTip(tr("在资源管理器里定位这个旁白文件"))
            self.btn_narr_open.clicked.connect(self.on_open_narration)
            self.btn_narr_save = QPushButton(tr("旁白另存为…"))
            self.btn_narr_save.setEnabled(False)
            self.btn_narr_save.setToolTip(
                tr("把旁白存到你自己选的地方，工程跟着改用这份"))
            self.btn_narr_save.clicked.connect(self.on_save_narration_as)
            nrow.addWidget(self.btn_narr_open)
            nrow.addWidget(self.btn_narr_save)
            nrow.addStretch(1)
            vvp.addLayout(nrow)
            self.left_tabs.addTab(voice_page, tr("配音"))
            split.addWidget(self.left_tabs)

            right = QWidget()
            rv = QVBoxLayout(right)
            rv.setContentsMargins(0, 0, 0, 0)
            self.video = PreviewCanvas()
            self.video.clicked.connect(self.toggle_play)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            self.player.positionChanged.connect(self.on_position)
            self.player.durationChanged.connect(self.on_duration)
            self.player.playbackStateChanged.connect(self.on_play_state)
            self.player.errorOccurred.connect(self.on_player_error)
            rv.addWidget(self.video, 1)

            # 预览控制条：播放/暂停、±5 秒、进度条、倍速、样片清晰度
            pbar = QHBoxLayout()
            pbar.setContentsMargins(0, 2, 0, 0)
            pbar.setSpacing(6)
            self.btn_play = QPushButton("▶")
            self.btn_play.setFixedSize(34, 26)
            self.btn_play.setToolTip(tr("播放 / 暂停（空格）"))
            self.btn_play.clicked.connect(self.toggle_play)
            pbar.addWidget(self.btn_play)
            self.btn_back = QPushButton("−5s")
            self.btn_back.setFixedSize(46, 26)
            self.btn_back.setToolTip(tr("后退 5 秒"))
            self.btn_back.clicked.connect(lambda: self.on_skip(-5.0))
            pbar.addWidget(self.btn_back)
            self.btn_fwd = QPushButton("+5s")
            self.btn_fwd.setFixedSize(46, 26)
            self.btn_fwd.setToolTip(tr("前进 5 秒"))
            self.btn_fwd.clicked.connect(lambda: self.on_skip(5.0))
            pbar.addWidget(self.btn_fwd)
            self.slider_pos = SeekSlider(Qt.Orientation.Horizontal)
            self.slider_pos.setRange(0, 0)
            self.slider_pos.setPageStep(0)     # 翻页关掉，点击由 SeekSlider 自己算
            self.slider_pos.setSingleStep(1)
            self.slider_pos.setEnabled(False)
            self.slider_pos.setToolTip(tr("拖动跳到任意位置"))
            self.slider_pos.sliderPressed.connect(self.on_scrub_start)
            self.slider_pos.sliderMoved.connect(self.on_scrub_move)
            self.slider_pos.sliderReleased.connect(self.on_scrub_end)
            pbar.addWidget(self.slider_pos, 1)
            self.lbl_playtime = QLabel("0:00 / 0:00")
            self.lbl_playtime.setMinimumWidth(88)
            self.lbl_playtime.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            pbar.addWidget(self.lbl_playtime)
            self.cmb_speed = QComboBox()
            for _rate in PREVIEW_SPEEDS:
                self.cmb_speed.addItem("%g×" % _rate, _rate)
            try:
                self.cmb_speed.setCurrentIndex(
                    PREVIEW_SPEEDS.index(float(self.settings["preview_speed"])))
            except ValueError:
                pass
            self.cmb_speed.setFixedWidth(72)
            self.cmb_speed.setToolTip(tr("预览倍速：只影响播放，不影响成片"))
            self.cmb_speed.currentIndexChanged.connect(self.on_speed_changed)
            pbar.addWidget(self.cmb_speed)
            self.cmb_quality = QComboBox()
            for _q in PREVIEW_QUALITY_ORDER:
                self.cmb_quality.addItem(tr(PREVIEW_QUALITY_LABELS[_q]), _q)
            try:
                self.cmb_quality.setCurrentIndex(
                    PREVIEW_QUALITY_ORDER.index(self.settings["preview_quality"]))
            except ValueError:
                pass
            self.cmb_quality.setFixedWidth(96)
            self.cmb_quality.setToolTip(tr("预览清晰度：只影响样片，不影响成片"))
            self.cmb_quality.currentIndexChanged.connect(self.on_quality_changed)
            pbar.addWidget(self.cmb_quality)
            rv.addLayout(pbar)

            self.lbl_preview = QLabel(tr("预览：实时合成"))
            self.lbl_preview.setEnabled(False)
            rv.addWidget(self.lbl_preview)
            split.addWidget(right)
            split.setStretchFactor(0, 0)
            split.setStretchFactor(1, 1)
            v.addWidget(split, 1)

            bgm = QHBoxLayout()
            self.lbl_bgm = QLabel(tr("音乐链接"))
            bgm.addWidget(self.lbl_bgm)
            self.edit_bgm = QLineEdit()
            self.edit_bgm.setPlaceholderText(
                tr("B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨"))
            self.edit_bgm.textEdited.connect(self.on_bgm_typed)
            self.edit_bgm.returnPressed.connect(self.start_fetch)
            bgm.addWidget(self.edit_bgm, 1)
            self.btn_retry = QPushButton(tr("重试"))
            self.btn_retry.setEnabled(False)
            self.btn_retry.clicked.connect(self.start_fetch)
            bgm.addWidget(self.btn_retry)
            v.addLayout(bgm)

            self.timeline = Timeline(self.project)
            self.timeline.seeked.connect(self.seek)
            self.timeline.picked.connect(self.on_timeline_pick)
            self.timeline.resized.connect(self.on_timeline_resize)
            self.timeline.trimmed.connect(self.on_timeline_trim)
            self.timeline.moved.connect(self.on_timeline_move)
            self.timeline.audioMoved.connect(self.on_audio_move)
            self.timeline.audioGroupMoved.connect(self.on_audio_group_move)
            self.timeline.blockMoved.connect(self.on_timeline_block_move)
            self.timeline.subtitleMoved.connect(self.on_subtitle_move)
            self.timeline.audioVolume.connect(self.on_audio_volume)
            self.timeline.envelopeChanged.connect(self.on_envelope_changed)
            self.timeline.overlayMoved.connect(self.on_overlay_moved)
            self.timeline.audioTrimmed.connect(self.on_audio_trim)
            self.timeline.audioTrimDone.connect(self.on_audio_trim_done)
            self.timeline.cut.connect(self.on_cut)
            self.timeline.menu.connect(self.on_timeline_menu)
            self.timeline.set_snap(bool(self.settings["snap_on"]))
            self.timeline.viewChanged.connect(self._on_tl_view)
            v.addWidget(self.timeline)

            tlbar = QHBoxLayout()
            tlbar.setContentsMargins(0, 0, 0, 0)
            tlbar.setSpacing(6)
            self.tl_scroll = QScrollBar(Qt.Orientation.Horizontal)
            self.tl_scroll.setToolTip(
                tr("时间线横向滚动：拖这里看后半段；Ctrl+滚轮缩放"))
            self.tl_scroll.valueChanged.connect(self.on_tl_scroll)
            tlbar.addWidget(self.tl_scroll, 1)
            self.lbl_zoom = QLabel(tr("全片"))
            self.lbl_zoom.setMinimumWidth(48)
            self.lbl_zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.lbl_zoom.setToolTip(tr("时间线缩放：Ctrl+滚轮，Ctrl+0 回到全片"))
            tlbar.addWidget(self.lbl_zoom)
            self.btn_fit = QPushButton(tr("适应窗口"))
            self.btn_fit.setToolTip(tr("时间线缩回全片（Ctrl+0）"))
            self.btn_fit.clicked.connect(self.on_tl_fit)
            tlbar.addWidget(self.btn_fit)
            v.addLayout(tlbar)

            foot = QHBoxLayout()
            self.lbl_status = QLabel(tr("拖图片或视频进来开始"))
            foot.addWidget(self.lbl_status, 1)
            # 右键功能藏在菜单里，底栏常驻一句提示，别让人到处找
            self.lbl_tl_tip = QLabel(tr("右键片段：改时长 / 转场 / 调色"))
            self.lbl_tl_tip.setObjectName("hint")
            foot.addWidget(self.lbl_tl_tip)
            self.lbl_audio = QLabel()
            self.lbl_audio.setObjectName("hint")
            foot.addWidget(self.lbl_audio)
            self.progress = QProgressBar()
            self.progress.setRange(0, 1000)
            self.progress.setFixedWidth(220)
            self.progress.setVisible(False)
            foot.addWidget(self.progress)
            self.btn_abort = QPushButton(tr("取消"))
            self.btn_abort.setFixedWidth(64)
            self.btn_abort.setVisible(False)
            self.btn_abort.clicked.connect(self.on_abort)
            foot.addWidget(self.btn_abort)
            v.addLayout(foot)

            self.setCentralWidget(root)
            self.timer = QTimer(self)
            self.timer.setSingleShot(True)
            self.timer.setInterval(700)
            self.timer.timeout.connect(self.start_fetch)
            # 自动保存：每 5 秒问一次要不要写，真正落盘由 AutoSaver 判间隔
            self.autosave_timer = QTimer(self)
            self.autosave_timer.setInterval(5000)
            self.autosave_timer.timeout.connect(self._autosave_tick)
            self.autosave_timer.start()

        # ---------------- 语言 / 模式
        def retranslate(self):
            """换语言或换模式后重设文案，不重建窗口。"""
            self._refresh_title()
            self.btn_project.setText(tr("工程"))
            self.btn_project.setToolTip(tr("新建 / 打开 / 保存工程（Ctrl+S 保存）"))
            self.btn_add.setText(tr("加素材"))
            self.btn_overlay.setText(tr("画中画"))
            self.btn_overlay.setToolTip(
                tr("压一层画面在成片上：图片或视频都行"))
            self.btn_up.setText(tr("上移"))
            self.btn_down.setText(tr("下移"))
            self.btn_remove.setText(tr("删除"))
            self.btn_snap.setText(tr("磁吸"))
            self.btn_snap.setToolTip(tr("吸附：拖动时贴住附近的边（Alt 临时关）"))
            self.btn_razor.setText(tr("切割"))
            self.btn_razor.setToolTip(tr("切割模式：点哪块切哪块（Esc 退出）"))
            self.btn_undo_bar.setText(tr("撤回"))
            self.btn_undo_bar.setToolTip(tr("撤销上一步（Ctrl+Z）"))
            self.btn_redo_bar.setText(tr("还原"))
            self.btn_redo_bar.setToolTip(tr("重做（Ctrl+Shift+Z）"))
            self.video.setToolTip(tr("点画面：播放 / 暂停"))
            self.lbl_tl_tip.setText(tr("右键片段：改时长 / 转场 / 调色"))
            self.btn_fit.setText(tr("适应窗口"))
            self.btn_fit.setToolTip(tr("时间线缩回全片（Ctrl+0）"))
            self.tl_scroll.setToolTip(
                tr("时间线横向滚动：拖这里看后半段；Ctrl+滚轮缩放"))
            self.lbl_zoom.setToolTip(tr("时间线缩放：Ctrl+滚轮，Ctrl+0 回到全片"))
            self.btn_undo.setText(tr("撤回"))
            self.btn_redo.setText(tr("还原"))
            self.left_tabs.setTabText(0, tr("素材"))
            self.left_tabs.setTabText(1, tr("历史"))
            self.left_tabs.setTabText(2, tr("字幕"))
            self.btn_sub_add.setText(tr("导入字幕…"))
            self.btn_sub_del.setText(tr("移除字幕"))
            self.btn_sub_save.setText(tr("另存 .srt…"))
            self.btn_sub_save.setToolTip(tr("把当前字幕存成 .srt：时间轴按成片时间算，能直接贴到别处"))
            # 配音页
            self.left_tabs.setTabText(3, tr("配音"))
            self.lbl_script.setText(
                tr("把文稿贴进来：一句一行、或者整段都行，会自动切段"))
            self.edit_script.setPlaceholderText(tr("在这里贴文稿……"))
            self.btn_script_open.setText(tr("打开文稿…"))
            self.btn_script_clear.setText(tr("清空"))
            self.cmb_tts_voice.setToolTip(
                tr("下拉里是常用音色，也能直接敲服务商的音色名"))
            self.cmb_tts_voice_b.setToolTip(
                tr("对话稿里「听友」用这个音色"))
            self.chk_tts_prefix.setText(tr("字幕带「主讲：」这样的前缀"))
            self.chk_tts_prefix.setToolTip(
                tr("勾上以后字幕会写成「主讲：……」，不勾只写台词"))
            self.spin_tts_rate.setToolTip(tr("0 是原速；正数更快，负数更慢"))
            self.progress_tts.setFormat(tr("正在合成 %v/%m 段"))
            self.btn_tts_start.setText(tr("开始合成"))
            self.btn_tts_cancel.setText(tr("取消"))
            self.btn_tts_audition.setText(tr("试听"))
            self.btn_tts_audition.setToolTip(
                tr("只合成头一段，听听音色合不合适"))
            for _i in range(self.cmb_tts_provider.count()):
                _k = self.cmb_tts_provider.itemData(_i)
                if _k in tts_engine.TTS_PROVIDERS:
                    self.cmb_tts_provider.setItemText(
                        _i, tr(tts_engine.TTS_PROVIDERS[_k]["label"]))
            for _fld, _txt in ((self.cmb_tts_provider, tr("配音服务")),
                               (self.cmb_tts_voice, tr("音色")),
                               (self.cmb_tts_voice_b, tr("音色 B")),
                               (self.spin_tts_rate, tr("语速")),
                               (self.chk_tts_prefix, tr("字幕前缀"))):
                _lab = self._tts_form.labelForField(_fld)
                if _lab is not None:
                    _lab.setText(_txt)
            self.on_script_changed()
            self.btn_settings.setText(tr("设置"))
            self.btn_settings.setToolTip(
                tr("导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏"))
            self.cmb_rhythm.setItemText(0, tr(MODE_LABELS["bgm"]))
            self.cmb_rhythm.setItemText(1, tr(MODE_LABELS["material"]))
            self.radio_total.setText(tr("自设总长"))
            self.radio_free.setText(tr("逐段秒数"))
            self.radio_voice.setText(tr("跟旁白"))
            self.spin_total.setSuffix(tr(" 秒"))
            self.lbl_bgm.setText(tr("音乐链接"))
            self.edit_bgm.setPlaceholderText(
                tr("B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨"))
            self.btn_retry.setText(tr("重试"))
            self.btn_export.setText(tr("导出视频"))
            tier = ui_tier(self.settings["ui"])
            self.btn_mode.setText(tr(UI_TIER_LABELS[tier]))
            self.btn_mode.setToolTip(
                tr("界面模式：简洁只做三件事，标准日常剪辑，专业全部参数"))
            for t, act in getattr(self, "_ui_actions", {}).items():
                act.setText(tr(UI_TIER_LABELS[t]))
                act.setChecked(t == tier)
            self.btn_more.setToolTip(tr("更多：工程与设置"))
            if self.preview_name:
                self.lbl_preview.setText(
                    tr("预览：{name}", name=self.preview_name))
            else:
                self.lbl_preview.setText(
                    tr("预览：实时合成"))
            self._player_retranslate()
            self._apply_ui_mode()
            self._refresh_all()
            self._refresh_history()
            # 英文/日文文案更长：先让按钮/标签不许缩到文字以下，再按需把窗口拉宽
            self.ensurePolished()
            for w_ in (self.btn_add, self.btn_up, self.btn_down, self.btn_remove,
                       self.btn_snap, self.btn_razor,
                       self.btn_undo_bar, self.btn_redo_bar,
                       self.btn_settings, self.btn_refresh, self.btn_export,
                       self.btn_mode, self.btn_retry, self.lbl_bgm,
                       self.btn_more, self.cmb_rhythm, self.radio_total,
                       self.radio_free, self.radio_voice):
                w_.setMinimumWidth(w_.sizeHint().width())
            need = self.minimumSizeHint().width()
            if need > self.width():
                self.resize(need, self.height())

        def _player_retranslate(self):
            """控制条的文案：按钮提示、两档下拉的条目名。"""
            self.btn_back.setToolTip(tr("后退 5 秒"))
            self.btn_fwd.setToolTip(tr("前进 5 秒"))
            self.slider_pos.setToolTip(tr("拖动跳到任意位置"))
            self.cmb_speed.setToolTip(tr("预览倍速：只影响播放，不影响成片"))
            self.cmb_quality.setToolTip(tr("预览清晰度：只影响样片，不影响成片"))
            for i in range(self.cmb_quality.count()):
                q = self.cmb_quality.itemData(i)
                self.cmb_quality.setItemText(
                    i, tr(PREVIEW_QUALITY_LABELS.get(q, "标准")))
            self._sync_play_button()

        # ---------------- 主题
        def _theme_assets(self, pal: dict) -> str:
            """QSS 里的下拉/微调箭头只能用图片，按配色生成三张小图并缓存。"""
            import hashlib
            key = hashlib.md5((pal["subtext"] + pal["panel"]
                               + pal["border"]).encode()).hexdigest()[:8]
            folder = os.path.join(tempfile.gettempdir(), "pic2video_ui_" + key)
            if os.path.isdir(folder):
                return folder
            os.makedirs(folder, exist_ok=True)
            color = QColor(pal["subtext"])

            def tri(name: str, w: int, h: int, down: bool):
                scale = 3                     # 3× 采样，高 DPI 不糊
                pm = QPixmap(w * scale, h * scale)
                pm.setDevicePixelRatio(scale)
                pm.fill(Qt.GlobalColor.transparent)
                pt = QPainter(pm)
                pt.setRenderHint(QPainter.RenderHint.Antialiasing)
                pt.setPen(Qt.PenStyle.NoPen)
                pt.setBrush(color)
                pts = ([QPointF(0, 0), QPointF(w, 0), QPointF(w / 2, h)]
                       if down else
                       [QPointF(0, h), QPointF(w, h), QPointF(w / 2, 0)])
                pt.drawPolygon(QPolygonF(pts))
                pt.end()
                pm.save(os.path.join(folder, name))

            tri("up.png", 8, 5, down=False)
            tri("down.png", 8, 5, down=True)
            tri("chevron.png", 9, 6, down=True)
            return folder

        def apply_theme(self):
            """按设置算一套配色，往整个应用上贴 QSS；时间线单独喂一份颜色。"""
            log("apply_theme %s / %s" % (self.settings["theme"],
                                         self.settings["accent"]))
            pal = theme.palette(theme.resolve(self.settings["theme"]),
                                self.settings["accent"])
            self.pal = pal
            qss = theme.build_qss(pal, assets=self._theme_assets(pal))
            app_ = QApplication.instance()
            if app_ is not None:   # 贴到应用上：连 parent 为空的对话框也同款
                app_.setStyleSheet(qss)
                app_.setPalette(app_palette(pal))
            else:
                self.setStyleSheet(qss)
            self.timeline.set_colors(theme.timeline_colors(pal))
            self.video.setStyleSheet("background:#000000;")
            self.lbl_audio.setStyleSheet(f"color:{pal['muted']};")
            self.lbl_preview.setStyleSheet(f"color:{pal['subtext']};")
            self.lbl_playtime.setStyleSheet(f"color:{pal['muted']};")
            self.lbl_status.setStyleSheet(f"color:{pal['text']};")

        def _on_system_theme(self, *_a):
            if self.settings["theme"] == "auto":
                self.apply_theme()

        def on_set_ui(self, tier: str):
            """切界面档位：立即生效，并记住上次的选择。"""
            tier = ui_tier(tier)
            self.settings.update(ui=tier)
            self.settings.save()
            self.retranslate()
            self.lbl_status.setText(
                tr("{name}模式", name=tr(UI_TIER_LABELS[tier])))

        def _apply_ui_mode(self):
            """三档可见性：简洁只做三步出片，标准日常剪辑，专业全给。

            顺手做「用到了才出现」：简洁模式下有画中画才占一条画中画轨，
            有字幕才出现「字幕」页，时间线缩放条只在标准以上露出来。
            """
            tier = ui_tier(self.settings["ui"])
            allowed = set(ui_toolbar(tier))
            widgets = {
                "project": self.btn_project,
                "add": self.btn_add,
                "overlay": self.btn_overlay,
                "up": self.btn_up,
                "down": self.btn_down,
                "remove": self.btn_remove,
                "snap": self.btn_snap,
                "razor": self.btn_razor,
                "undo": self.btn_undo_bar,
                "redo": self.btn_redo_bar,
                "settings": self.btn_settings,
                "rhythm": self.cmb_rhythm,
                "total": self.spin_total,
                "refresh": self.btn_refresh,
                "export": self.btn_export,
                "mode": self.btn_mode,
                "more": self.btn_more,
            }
            for name, w_ in widgets.items():
                w_.setVisible(name in allowed)
            # 时长三选一是「自设总长 + 逐段秒数 + 总长输入」一组
            total = "total" in allowed
            self.radio_total.setVisible(total)
            self.radio_free.setVisible(total)
            self.radio_voice.setVisible(total and bool(self.project.narration_path))
            self.lbl_mode_hint.setVisible(total)
            # 倍速 / 清晰度是剪辑时才需要调的
            advanced = ui_advanced(tier)
            self.cmb_speed.setVisible(advanced)
            self.cmb_quality.setVisible(advanced)
            self._sync_tl_bar()
            # 左栏：历史只在标准以上；字幕在标准以上，简洁模式里有字幕才露
            has_subs = bool(self.project.cues or self.project.subtitles_path)
            self.left_tabs.setTabVisible(1, advanced)
            self.left_tabs.setTabVisible(2, advanced or has_subs)
            # 配音页只在标准以上出现（简洁模式就三步出片）
            self.left_tabs.setTabVisible(3, advanced)
            # 简洁模式没画中画时，别空占一条「画中画」轨
            self.timeline.set_rows(
                advanced or bool(self.project.overlays), True,
                bool(self.project.cues))

        def _sync_tl_bar(self):
            """时间线底下的缩放条：标准以上常驻，简洁模式放大过才露。"""
            show = (ui_advanced(self.settings["ui"])
                    or self.timeline.zoom > 1.001)
            for w_ in (self.tl_scroll, self.lbl_zoom, self.btn_fit):
                w_.setVisible(show)

        def _menu_labels(self):
            """右键菜单会出现的四项（冒烟测试用来核对文案）。"""
            return [tr("上移"), tr("下移"), tr("删除"), tr("这一段显示多久")]

        def on_list_menu(self, pos):
            """列表右键菜单：上移 / 下移 / 删除 / 改这张的秒数。"""
            item = self.list.itemAt(pos)
            if item is not None:
                self.list.setCurrentItem(item)
            row = self.list.currentRow()
            menu = QMenu(self)
            act_up = menu.addAction(tr("上移"))
            act_down = menu.addAction(tr("下移"))
            act_del = menu.addAction(tr("删除"))
            menu.addSeparator()
            act_dur = menu.addAction(tr("这一段显示多久"))
            for a in (act_up, act_down, act_del, act_dur):
                a.setEnabled(row >= 0)
            picked = menu.exec(self.list.viewport().mapToGlobal(pos))
            if picked is None:
                return
            if picked is act_up:
                self.on_move(-1)
            elif picked is act_down:
                self.on_move(1)
            elif picked is act_del:
                self.on_remove()
            elif picked is act_dur and item is not None:
                self.on_edit_dur(item)

        # ---------------- 状态刷新
        # ---------------- 字幕
        def _refresh_subtitles(self):
            self.sub_list.clear()
            n = len(self.project.cues)
            path = self.project.subtitles_path
            if n:
                head = tr("已导入 {n} 条字幕", n=n)
                self.lbl_subs.setText(head + (("\n" + path) if path else ""))
            elif path:
                self.lbl_subs.setText(tr("字幕里没有一条能用的") + "\n" + path)
            else:
                self.lbl_subs.setText(tr("还没有字幕"))
            self.btn_sub_del.setEnabled(bool(n or path))
            for c in self.project.cues:
                text = str(c.get("text", "")).replace("\n", " / ")
                self.sub_list.addItem("%.1fs → %.1fs  %s"
                                      % (c["start"], c["end"], text))

        def on_import_subtitle(self):
            base = (os.path.dirname(self.project.subtitles_path)
                    or self._project_dir())
            picked = FileDialog.get_open_files(
                self, tr("选字幕文件"), base,
                tr("字幕 (*.srt *.ass)"))
            if not picked:
                return
            path = picked[0]
            try:
                cues = load_subtitles(path)
            except SubtitleError as e:
                if e.reason == "missing":
                    msg = tr("文件不在了：") + path
                else:
                    msg = tr("字幕认不出来，换一份 UTF-8 的 .srt / .ass 试试")
                MsgBox.warn(self, tr("读不了字幕"), msg)
                return
            set_subtitles(self.project, path, cues)
            self._refresh_subtitles()
            self._mark_dirty()
            self._history_push(tr("导入字幕"))
            self._mark_stale()
            self.lbl_status.setText(tr("已导入 {n} 条字幕", n=len(cues)))

        def on_clear_subtitle(self):
            if not (self.project.cues or self.project.subtitles_path):
                return
            clear_subtitles(self.project)
            self._refresh_subtitles()
            self._mark_dirty()
            self._history_push(tr("移除字幕"))
            self._mark_stale()
            self.lbl_status.setText(tr("字幕已移除"))

        def on_save_subtitle(self):
            """把当前字幕存成 .srt：时间轴按成片时间算，方便粘到别处。"""
            if not self.project.cues:
                MsgBox.warn(self, tr("还没有字幕"),
                            tr("先导入字幕，或者到「配音」页生成，再来另存。"))
                return
            base = (os.path.dirname(self.project.subtitles_path)
                    or self._project_dir())
            stem = os.path.splitext(self._project_name())[0] or "字幕"
            path = FileDialog.get_save_file(
                self, tr("字幕另存为…"),
                os.path.join(base, stem + ".srt"),
                tr("字幕 (*.srt)"), suffix="srt")
            if not path:
                return
            text = tts_engine.cues_srt(self.project.cues)
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
            except OSError as e:
                MsgBox.warn(self, tr("字幕没存下去"), str(e))
                return
            self.lbl_status.setText(tr("字幕已存到：{path}", path=path))

        # ---------------- 配音：文稿 -> TTS -> 旁白 + 字幕
        def _script_text(self) -> str:
            return self.edit_script.toPlainText().strip()

        def _tts_provider(self) -> str:
            return str(self.cmb_tts_provider.currentData() or "edge")

        def _tts_voice(self) -> str:
            """选中的音色；下拉里没的（自己敲的）直接拿文本。"""
            idx = self.cmb_tts_voice.currentIndex()
            data = self.cmb_tts_voice.itemData(idx)
            text = self.cmb_tts_voice.currentText().strip()
            if data and text == self.cmb_tts_voice.itemText(idx):
                return str(data)
            return text

        def _tts_voice_b(self) -> str:
            """「听友」的音色；空着就跟着主音色。"""
            cmb = self.cmb_tts_voice_b
            idx = cmb.currentIndex()
            data = cmb.itemData(idx)
            text = cmb.currentText().strip()
            if data and text == cmb.itemText(idx):
                return str(data)
            return text or self._tts_voice()

        def _dialogue_turns(self) -> list:
            """文稿里有没有「主讲：」「听友：」轮次。"""
            try:
                return tts_engine.parse_dialogue(self._script_text())
            except Exception:
                return []

        def _show_dialogue(self, on: bool):
            """没检测到对话稿时把 B 音色与字幕前缀藏起来。"""
            for fld in (self.cmb_tts_voice_b, self.chk_tts_prefix):
                if not hasattr(self, "_tts_form"):
                    continue
                lab = self._tts_form.labelForField(fld)
                if lab is not None:
                    lab.setVisible(bool(on))
                fld.setVisible(bool(on))

        def _fill_voices(self, provider: str, wanted: str = "",
                         combo=None):
            """按服务商换音色下拉；wanted 是上次用的，能选回来就选回来。"""
            cmb = combo if combo is not None else self.cmb_tts_voice
            voices = TTS_VOICES.get(provider) or []
            cmb.blockSignals(True)
            cmb.clear()
            for vid, name in voices:
                cmb.addItem(name, vid)
            cmb.blockSignals(False)
            hit = cmb.findData(wanted)
            # 换服务商时，上一个服务商的音色名对新服务商是无效的；
            # 只有在哪儿都查不到时才当成用户自己敲的自定义音色留着。
            known = set()
            for _v in TTS_VOICES:
                for _vid, _name in TTS_VOICES.get(_v, ()):
                    known.add(_vid)
                    known.add(_name)
            if hit >= 0:
                cmb.setCurrentIndex(hit)
            elif wanted and wanted not in known:
                cmb.setEditText(wanted)
            elif voices:
                cmb.setCurrentIndex(0)

        def _tts_running(self) -> bool:
            return (self._tts_worker is not None
                    and self._tts_worker.isRunning())

        def _refresh_voice_page(self):
            """把旁白现状写回配音页（文件名 + 时长 + 字幕条数）。"""
            if not hasattr(self, "lbl_narration"):
                return
            p = self.project
            if p.narration_path:
                self.lbl_narration.setText(tr(
                    "旁白：{name} · {dur} · {n} 条字幕　—　文件位置：{path}（它同时占着时间线下面的「音频」轨）",
                    name=os.path.basename(p.narration_path),
                    dur=human_time(p.narration_dur), n=len(p.cues),
                    path=p.narration_path))
            else:
                self.lbl_narration.setText(tr(
                    "还没有旁白。合成完它会自动放进时间线的「音频」轨，画面上也会盖字幕。"))
            self.btn_narr_open.setEnabled(bool(p.narration_path))
            self.btn_narr_save.setEnabled(bool(p.narration_path))
            busy = self._tts_running()
            self.btn_tts_start.setEnabled(bool(self._script_text()) and not busy)
            self.btn_tts_cancel.setEnabled(busy)
            self.btn_tts_audition.setEnabled(bool(self._script_text()) and not busy)

        def on_open_narration(self):
            path = str(self.project.narration_path or "")
            if not path:
                return
            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            except OSError:
                pass

        def on_save_narration_as(self):
            p = self.project
            src = str(p.narration_path or "")
            if not src or not os.path.exists(src):
                MsgBox.warn(self, tr("还没有旁白"),
                            tr("先在上面合成一段旁白，再来另存。"))
                return
            stem = os.path.splitext(os.path.basename(self._project_name()))[0]
            default = os.path.join(self._project_dir() or "",
                                   (stem or "旁白") + "-旁白.m4a")
            picked = FileDialog.get_save_file(
                self, tr("旁白另存为…"), default,
                tr("音频 (*.m4a)"), suffix="m4a")
            if not picked:
                return
            try:
                shutil.copy2(src, picked)
            except OSError as exc:
                MsgBox.warn(self, tr("旁白没存下去"), str(exc))
                return
            set_narration(self.project, picked, float(p.narration_dur),
                          [dict(c) for c in p.cues])
            self._refresh_all()
            self._mark_dirty()
            self._history_push(tr("旁白另存"))
            self.lbl_status.setText(tr("旁白已存到：{path}", path=picked))

        def on_script_changed(self):
            text = self._script_text()
            if not text:
                self.lbl_plan.setText(tr("还没有文稿"))
                self._show_dialogue(False)
            else:
                turns = self._dialogue_turns()
                if turns:
                    plan = tts_engine.dialogue_plan(text, self._tts_provider())
                    a = len([t for t in turns if t["speaker"] == "主讲"])
                    b = len(turns) - a
                    self.lbl_plan.setText(tr(
                        "对话稿：主讲 {a} 轮、听友 {b} 轮，会切成 {n} 段",
                        a=a, b=b, n=len(plan)))
                else:
                    plan = tts_engine.make_plan(text, self._tts_provider())
                    self.lbl_plan.setText(tr(
                        "会切成 {n} 段，共 {chars} 字",
                        n=len(plan), chars=sum(p["chars"] for p in plan)))
                self._show_dialogue(bool(turns))
            self._refresh_voice_page()

        def on_open_script(self):
            picked = FileDialog.get_open_files(
                self, tr("选文稿"), self._project_dir(),
                tr("文稿 (*.txt *.md)"))
            if not picked:
                return
            path = picked[0]
            text = ""
            for enc in ("utf-8-sig", "utf-8", "gbk"):
                try:
                    with open(path, encoding=enc) as f:
                        text = f.read()
                    break
                except (OSError, UnicodeDecodeError):
                    continue
            if not text.strip():
                MsgBox.warn(self, tr("读不了文稿"),
                            tr("换一个 UTF-8 的 .txt / .md 试试：") + path)
                return
            self.edit_script.setPlainText(text)
            self.lbl_status.setText(tr("已载入文稿：{name}",
                                       name=os.path.basename(path)))

        def on_clear_script(self):
            self.edit_script.setPlainText("")

        def on_tts_provider_changed(self):
            provider = self._tts_provider()
            self._fill_voices(provider, str(self.cmb_tts_voice.currentText()))
            self._fill_voices(provider, str(self.cmb_tts_voice_b.currentText()),
                              self.cmb_tts_voice_b)
            spec = tts_engine.TTS_PROVIDERS[provider]
            if spec.get("needs_key"):
                self.lbl_tts_status.setText(tr(
                    "{name} 要自己的密钥：填在「设置 → 配音」里。",
                    name=tr(spec["label"])))
            else:
                self.lbl_tts_status.setText(tr("贴好文稿，选个音色，就能合成"))
            self.spin_tts_rate.setEnabled(provider != "gemini")
            self.spin_tts_rate.setToolTip(
                tr("Gemini 没有语速参数：想快就自己改文稿")
                if provider == "gemini" else tr("语速：只影响合成，不影响画面"))
            self.on_script_changed()

        def _tts_kwargs(self) -> dict:
            s = self.settings
            provider = self._tts_provider()
            kw = dict(provider=provider,
                      root=components.data_dir(),
                      voice=self._tts_voice(),
                      rate=int(self.spin_tts_rate.value()),
                      api_key=(s["tts_keys"] or {}).get(provider, ""),
                      base_url=s["tts_base_url"],
                      proxy=s["tts_proxy"],
                      gap=float(s["tts_gap"]),
                      cleanup=bool(s["tts_cleanup"]),
                      extra={"model": str(s["tts_model"]),
                             "region": str(s["tts_azure_region"])})
            if self._dialogue_turns():
                kw["voices"] = {"主讲": self._tts_voice(),
                                "听友": self._tts_voice_b()}
                kw["gap"] = float(tts_engine.DIALOGUE_GAP)
                kw["cue_prefix"] = bool(self.chk_tts_prefix.isChecked())
            return kw

        def on_tts_start(self):
            if self._tts_running():
                return
            text = self._script_text()
            if not text:
                MsgBox.warn(self, tr("还没有文稿"),
                            tr("先把文稿贴到左边，再来合成。"))
                return
            provider = self._tts_provider()
            spec = tts_engine.TTS_PROVIDERS[provider]
            if spec.get("needs_key") and not self._tts_kwargs()["api_key"]:
                MsgBox.warn(self, tr("这家要密钥"), tr(
                    "{name} 得先在「设置 → 配音」里填自己的密钥。",
                    name=tr(spec["label"])))
                return
            plan = (tts_engine.dialogue_plan(text, provider)
                    if self._dialogue_turns()
                    else tts_engine.make_plan(text, provider))
            self._tts_audition = False
            self._tts_begin(plan, self._tts_kwargs())

        def on_tts_audition(self):
            if self._tts_running():
                return
            text = self._script_text()
            if not text:
                return
            provider = self._tts_provider()
            plan = (tts_engine.dialogue_plan(text, provider)
                    if self._dialogue_turns()
                    else tts_engine.make_plan(text, provider))[:1]
            self._tts_audition = True
            self._tts_begin(plan, self._tts_kwargs())

        def _tts_begin(self, plan, kwargs):
            self.btn_tts_start.setEnabled(False)
            self.btn_tts_audition.setEnabled(False)
            self.btn_tts_cancel.setEnabled(True)
            self.progress_tts.setRange(0, max(1, len(plan)))
            self.progress_tts.setValue(0)
            self.progress_tts.setVisible(True)
            self.lbl_tts_status.setText(tr("开始合成，共 {n} 段…", n=len(plan)))
            w = TtsWorker(plan, kwargs, parent=self)
            self._tts_worker = w
            w.progress.connect(self.on_tts_progress)
            w.ok.connect(self.on_tts_ok)
            w.bad.connect(self.on_tts_bad)
            w.stopped.connect(self.on_tts_stopped)
            w.start()

        def on_tts_progress(self, done, total, cached):
            self.progress_tts.setRange(0, max(1, total))
            self.progress_tts.setValue(done)
            if cached:
                self.lbl_tts_status.setText(tr(
                    "已合成 {done}/{total} 段（{cached} 段用缓存）",
                    done=done, total=total, cached=cached))
            else:
                self.lbl_tts_status.setText(tr(
                    "已合成 {done}/{total} 段", done=done, total=total))

        def _reset_tts_busy(self):
            self.progress_tts.setVisible(False)
            self._refresh_voice_page()

        def on_tts_ok(self, res):
            self._reset_tts_busy()
            if self._tts_audition:
                self._tts_audition = False
                self._play_audition(str(res.get("audio", "")))
                self.lbl_tts_status.setText(tr("试听中：不喜欢就换个音色"))
                return
            cues = res.get("cues") or []
            dur = float(res.get("dur") or 0.0)
            path = str(res.get("audio") or "")
            if not path or dur <= 0:
                self.on_tts_bad(tr("合成完了却没拿到可用的音频"))
                return
            set_narration(self.project, path, dur, cues)
            self.settings.update(
                tts_script=self._script_text(),
                tts_provider=self._tts_provider(),
                tts_voice=self._tts_voice(),
                tts_voice_b=self._tts_voice_b(),
                tts_prefix=bool(self.chk_tts_prefix.isChecked()),
                tts_rate=int(self.spin_tts_rate.value()))
            self.settings.save()
            self._refresh_all()
            self._mark_dirty()
            self._history_push(tr("生成旁白"))
            note = ""
            if res.get("cleaned"):
                note = tr("（已清理，响度 {lu}）",
                          lu="%.1f" % float(res.get("loudness") or 0.0))
            self.lbl_tts_status.setText(tr(
                "旁白好了：{dur}，{n} 条字幕。总长已改成「跟旁白」。文件在：{path}",
                dur=human_time(dur), n=len(self.project.cues),
                path=path) + note)
            self.lbl_status.setText(tr(
                "旁白已生成，已放进时间线「音频」轨：{name}",
                name=os.path.basename(path)))

        def _play_audition(self, path):
            if not path or not os.path.exists(path):
                return
            if self._audition_player is None:
                self._audition_player = QMediaPlayer(self)
                self._audition_audio = QAudioOutput(self)
                self._audition_player.setAudioOutput(self._audition_audio)
            self._audition_player.stop()
            self._audition_player.setSource(QUrl.fromLocalFile(path))
            self._audition_player.play()

        def on_tts_bad(self, msg):
            self._tts_audition = False
            self._reset_tts_busy()
            self.lbl_tts_status.setText(tr("配音没成：{msg}", msg=msg))
            if not os.environ.get("PIC2VIDEO_SHOT"):
                MsgBox.warn(self, tr("配音失败"), msg)

        def on_tts_stopped(self):
            self._tts_audition = False
            self._reset_tts_busy()
            self.lbl_tts_status.setText(tr("已取消配音"))

        def on_tts_cancel(self):
            if self._tts_worker is not None:
                self._tts_worker.cancel()
            self.lbl_tts_status.setText(tr("正在停…"))

        def _refresh_all(self):
            # 档位与「用到了才出现」的轨/页：工程内容一变就跟着重算
            self._apply_ui_mode()
            self._refresh_list()
            self._refresh_subtitles()
            self._refresh_voice_page()
            self.timeline.update()
            self._sync_tl_scroll()
            self._mark_stale()
            m = self.project.mode
            self.cmb_rhythm.blockSignals(True)
            self.cmb_rhythm.setCurrentIndex(1 if m == "material" else 0)
            self.cmb_rhythm.blockSignals(False)
            self.radio_total.setChecked(m == "total")
            self.radio_free.setChecked(m == "free")
            self.radio_voice.setChecked(m == "voice")
            self.spin_total.setEnabled(self.project.mode == "total")
            self._refresh_mode_hint()
            if self.project.mode == "total":
                self.spin_total.blockSignals(True)
                self.spin_total.setValue(self.project.total_setting)
                self.spin_total.blockSignals(False)
            n = self.project.image_count()
            if self.project.narration_path:
                src = tr("旁白 {name}", name=os.path.basename(
                    self.project.narration_path))
            elif self.project.audio_path:
                src = "BGM"
            else:
                src = tr("无音乐")
            extra = ""
            if self.project.intro() or self.project.outro():
                extra = tr("（含片头片尾）")
            if self.project.overlays:
                extra += tr("（{n} 层画中画）", n=len(self.project.overlays))
            self.lbl_status.setText(
                tr("{n} 段素材 · 总长 {total} · {src}", n=n,
                   total=human_time(self.project.total()), src=src) + extra)

        def _refresh_mode_hint(self):
            """四个时长控件旁边的解释：总长到底按谁算。"""
            if not hasattr(self, "lbl_mode_hint"):
                return
            mode = self.project.mode
            if mode == "total":
                text = tr("总长 = 下面这个秒数")
                tip = tr("图片段按比例铺满这个秒数")
            elif mode == "free":
                text = tr("每段按自己秒数")
                tip = tr("逐段秒数：每段自己定，总长自动算")
            elif mode == "voice":
                text = tr("总长 = 旁白长度")
                tip = tr("跟旁白：总长跟旁白一样长")
            else:
                text = tr("总长 = 音乐长度")
                tip = tr("跟音乐：总长跟音乐一样长")
            self.lbl_mode_hint.setText(text)
            self.lbl_mode_hint.setToolTip(tip)

        def _refresh_list(self):
            self._rebuilding = True
            cur = self.list.currentRow()
            self.list.clear()
            for i, c in enumerate(self.project.clips):
                it = QListWidgetItem(self._thumb(c.path, c.kind), "")
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setData(Qt.ItemDataRole.UserRole + 1, c.dur)
                # 拖拽重排时按这个号找回原来的 Clip（别把调色/转场丢了）
                it.setData(Qt.ItemDataRole.UserRole + 2, i)
                # 拖放提示要知道这一行是不是视频（视频不能被替换，只能插到后面）
                it.setData(Qt.ItemDataRole.UserRole + 3, c.kind)
                self.list.addItem(it)
            self._update_titles()
            if 0 <= cur < self.list.count():
                self.list.setCurrentRow(cur)
            self._rebuilding = False

        def _update_titles(self):
            for i, c in enumerate(self.project.clips):
                it = self.list.item(i)
                if it is None:
                    continue
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setData(Qt.ItemDataRole.UserRole + 1, c.dur)
                it.setData(Qt.ItemDataRole.UserRole + 3, c.kind)
                head = {"intro": tr("片头"),
                        "outro": tr("片尾")}.get(c.kind, f"{i + 1}.")
                it.setText(tr("{i}.  {dur} 秒   {name}", i=head,
                              dur=f"{c.span:.1f}",
                              name=os.path.basename(c.path)))

        def _thumb(self, path, kind="image"):
            if kind in ("video", "intro", "outro"):
                pm = QPixmap(96, 54)
                pm.fill(QColor(self.pal["panel"]))
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setBrush(QColor(self.pal["accent2"]))
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(QPolygonF([QPointF(40, 17), QPointF(40, 37),
                                         QPointF(60, 27)]))
                p.end()
                return QIcon(pm)
            pm = QPixmap(path)
            if pm.isNull():
                return QIcon()
            return QIcon(pm.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation))

        def _mark_stale(self):
            """任何改动：标记 + 立刻把画面换成新参数的样子（不等渲染）。"""
            self.preview_stale = True
            self.btn_refresh.setText(tr("刷新预览 ●"))
            self._refresh_scene()

        def _clean_stale(self):
            self.preview_stale = False
            self.btn_refresh.setText(tr("刷新预览"))

        # ---------------- 设置
        def on_settings(self):
            log("settings open")
            dlg = SettingsDialog(self.settings, self, self.project)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.settings.save()
            log("settings saved lang=%s ui=%s theme=%s ratio=%s size=%sx%s" % (
                self.settings["lang"], self.settings["ui"],
                self.settings["theme"], self.settings["ratio"],
                self.settings["width"], self.settings["height"]))
            set_locale(self.settings["lang"])
            apply_qt_translator(current_locale())
            self.apply_theme()
            self.project.mode = self.settings["mode"]
            self.project.fps = int(self.settings["fps"])
            self.project.audio_start = float(self.settings["music_start"])
            self.project.audio_len = float(self.settings["music_len"])
            set_mode(self.project, self.project.mode)
            self.retranslate()
            self._mark_stale()
            self._bind_shortcuts()
            w, h = self.settings.size()
            self.lbl_preview.setText(
                tr("参数已更新：{w}x{h} {fps} 帧/秒，预览已实时",
                   w=w, h=h, fps=self.settings["fps"]))
            self._follow_audio()
            self.lbl_status.setText(tr("设置已保存"))
            self._history_push(tr("改设置"))

        def _follow_audio(self):
            """预览音频永远走 Windows 当前默认输出，插拔耳机自动跟上。"""
            dev = QMediaDevices.defaultAudioOutput()
            if not dev.isNull():
                self.audio_out.setDevice(dev)
            name = (dev.description() if not dev.isNull()
                    else tr("未检测到输出设备"))
            try:
                self.lbl_audio.setText(tr("音频：{name}", name=name))
            except AttributeError:
                pass

        def _opts(self, preview=False):
            s = self.settings
            w, h = (s.preview_size(s["preview_quality"]) if preview
                    else s.size())
            return dict(w=w, h=h, fps=int(s["fps"]), fit=s["fit"],
                        bg_blur=bool(s["bg_blur"]),
                        pad_color=s["pad_color"], bg_path=s["bg_path"],
                        fade_in=float(s["fade_in"]),
                        fade_out=float(s["fade_out"]),
                        transition=s["transition"],
                        transition_sec=float(s["transition_sec"]),
                        xfade_sec=float(s["transition_sec"]),
                        motion=s["motion"],
                        volume=float(s["music_volume"]),
                        music_start=float(s["music_start"]),
                        crop_focus=s["crop_focus"],
                        subtitles=[dict(c) for c in self.project.cues],
                        subtitle_font=s["subtitle_font"],
                        subtitle_fonts=font_lookup_dir(s["subtitle_font"]),
                        subtitle_size=subtitle_px(int(s["subtitle_size"]), h),
                        overlays=[o.copy() for o in self.project.overlays])

        # ---------------- 动作
        def on_add(self):
            paths = FileDialog.get_open_files(
                self, tr("选素材"), "",
                tr("图片或视频 (*.jpg *.jpeg *.png *.bmp *.webp "
                   "*.mp4 *.mov *.mkv *.webm *.avi)"), multi=True)
            if paths:
                self.add_media(paths)

        def add_media(self, paths):
            log("add_media n=%d first=%s" % (
                len(paths), os.path.basename(paths[0]) if paths else ""))
            good = []
            for p in paths:
                if is_video_path(p):
                    try:
                        ok = probe_duration(p) > 0.05
                    except Exception:
                        ok = False
                else:
                    rd = QImageReader(p)
                    ok = rd.canRead() and rd.size().width() > 0
                if ok:
                    good.append(p)
                else:
                    self.lbl_status.setText(
                        tr("{name} 读不了，已跳过", name=os.path.basename(p)))
            if good:
                dur = (self.settings["per_image"]
                       if self.project.mode == "free" else None)
                self.project.add_media(
                    good, dur=dur,
                    intro=self.settings["intro_path"],
                    outro=self.settings["outro_path"])
                self.radio_total.setChecked(self.project.mode == "total")
                self._refresh_all()
                self.lbl_status.setText(tr("已加 {n} 段素材", n=len(good)))
                self._history_push(tr("加素材"))

        def on_drop_into_list(self, paths, row, zone):
            """从外面拖到素材列表上：左 / 中 / 右三段分别是插前 / 替换 / 插后。"""
            paths = expand_paths(paths)
            good = []
            for p in paths:
                if os.path.isdir(p):
                    continue
                if is_video_path(p):
                    try:
                        ok = probe_duration(p) > 0.05
                    except Exception:
                        ok = False
                else:
                    rd = QImageReader(p)
                    ok = rd.canRead() and rd.size().width() > 0
                if ok:
                    good.append(p)
                else:
                    self.lbl_status.setText(
                        tr("{name} 读不了，已跳过", name=os.path.basename(p)))
            if not good:
                return
            good.sort(key=lambda p: os.path.basename(p).lower())
            dur = (self.settings["per_image"]
                   if self.project.mode == "free" else None)
            if zone == "replace" and 0 <= row < len(self.project.clips):
                self.project.replace_media(row, good[0])
                if len(good) > 1:
                    self.project.insert_media(row + 1, good[1:], dur)
                label = tr("替换素材")
                self.lbl_status.setText(
                    tr("已换成 {n} 段素材", n=len(good)))
            else:
                if row < 0:
                    at = len(self.project.clips)
                elif zone == "before":
                    at = row
                else:
                    at = row + 1
                self.project.insert_media(at, good, dur)
                label = tr("加素材")
                self.lbl_status.setText(
                    tr("已插入 {n} 段素材", n=len(good)))
            self._refresh_all()
            self._history_push(label)

        def _selected_rows(self):
            """列表里选中的行；一个都没选就用当前行。"""
            rows = [self.list.row(it) for it in self.list.selectedItems()]
            if not rows:
                row = self.list.currentRow()
                rows = [row] if row >= 0 else []
            return sorted(set(rows))

        def _select_rows(self, rows):
            """按行号重设列表选中（刷新后把选中还原回去）。"""
            self.list.blockSignals(True)
            self.list.clearSelection()
            keep = []
            for r in rows:
                it = self.list.item(r) if 0 <= r < self.list.count() else None
                if it is not None:
                    it.setSelected(True)
                    keep.append(r)
            if keep:
                self.list.setCurrentItem(
                    self.list.item(keep[0]),
                    QItemSelectionModel.SelectionFlag.NoUpdate)
            self.list.blockSignals(False)
            self._sync_selection()

        def _sync_selection(self):
            """列表选中 → 时间线高亮，两边始终指同一批段。"""
            if self._rebuilding:
                return
            self.timeline.set_selection(self._selected_rows())

        def on_selection_changed(self):
            self._sync_selection()

        def on_move(self, delta):
            rows = self._selected_rows()
            if not rows:
                return
            order = rows if delta < 0 else list(reversed(rows))
            for i in order:
                self.project.move(i, delta)
            self._refresh_all()
            self._select_rows([max(0, min(i + delta, self.list.count() - 1))
                               for i in rows])
            self._history_push(tr("调整顺序"))

        def on_remove(self):
            rows = self._selected_rows()
            if not rows:
                return
            log("remove image rows=%s" % rows)
            self.project.remove(rows)
            self.timeline.set_selection([])
            self._refresh_all()
            self.list.clearSelection()
            self._history_push(tr("删除素材"))

        def on_edit_dur(self, item):
            row = self.list.row(item)
            rows = self._selected_rows()
            if row not in rows:
                rows = [row] if row >= 0 else []
            rows = [r for r in rows if 0 <= r < len(self.project.clips)]
            if not rows:
                return
            cur = self.project.clips[rows[0]].dur
            many = len(rows) > 1
            val, ok = NumberDialog.get_double(
                self,
                tr("改选中片段的时长") if many else tr("这一段显示多久"),
                tr("选中的 {n} 段一起改（秒）：", n=len(rows)) if many
                else tr("第 {row} 段（秒）：", row=rows[0] + 1),
                cur, MIN_DUR, 3600, 1)
            if not ok:
                return
            log("duration rows=%s %.2f -> %.2f" % (rows, cur, val))
            for i in rows:
                clip = self.project.clips[i]
                if clip.is_video and clip.kind == "video":
                    self.project.set_video_len(i, val)
                else:
                    self.project.set_duration(i, val)
            self._refresh_all()
            self._select_rows(rows)
            self._history_push(tr("改时长"))

        def on_rows_moved(self, *_a):
            if self._rebuilding:
                return
            order = []
            for i in range(self.list.count()):
                it = self.list.item(i)
                order.append(it.data(Qt.ItemDataRole.UserRole + 2))
            keep = [order[r] for r in self._selected_rows()
                    if r < len(order) and isinstance(order[r], int)]
            iids = [k for k in order if isinstance(k, int)]
            ok = len(iids) == len(order) and self.project.reorder_clips(iids)
            if not ok:      # 拿不到原对象（老数据/外部改动）才退回重建
                pairs = []
                for i in range(self.list.count()):
                    it = self.list.item(i)
                    pairs.append((it.data(Qt.ItemDataRole.UserRole),
                                  float(it.data(Qt.ItemDataRole.UserRole + 1))))
                self.project.reorder(pairs)
            self._refresh_all()
            if ok and keep:
                self._select_rows([iids.index(k) for k in keep if k in iids])
            self._history_push(tr("调整顺序"))

        def on_row_changed(self, row):
            self._sync_selection()

        def on_mode(self, mode):
            set_mode(self.project, mode)
            self._refresh_all()
            self._history_push(tr("改节奏"))

        def on_total_changed(self, value):
            if self.project.mode != "total":
                return
            self.project.total_setting = value
            self.project.normalize()
            self._refresh_all()
            self._history_push(tr("改节奏"))

        def on_timeline_pick(self, i):
            """时间线上点了/框了几段 → 列表也跟着高亮同一批。"""
            rows = [r for r in (self.timeline.selected or [i])
                    if 0 <= r < self.list.count()]
            if rows:
                self._select_rows(rows)
            self.timeline.set_selection(rows or [i])

        def on_timeline_resize(self, i, value):
            log("timeline resize i=%d -> %.2f" % (i, value))
            self.project.set_duration(i, value)
            self._update_titles()
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("改时长"))
            self.lbl_status.setText(
                tr("{n} 段素材 · 总长 {total}", n=self.project.segment_count(),
                   total=human_time(self.project.total())))

        def on_timeline_trim(self, i, edge, delta):
            """拖片段左右边：视频改入出点，图片改自己的时长。"""
            if not (0 <= i < len(self.project.clips)):
                return
            clip = self.project.clips[i]
            if clip.is_video:
                if edge == "left":
                    self.project.set_src_in(i, clip.src_in + delta)
                else:
                    self.project.set_src_out(i, clip.src_in + clip.span + delta)
            else:
                self.project.set_duration(
                    i, clip.dur - delta if edge == "left" else clip.dur + delta)
            self._update_titles()
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("改时长"))
            self.lbl_status.setText(
                tr("{n} 段素材 · 总长 {total}", n=self.project.segment_count(),
                   total=human_time(self.project.total())))

        def _clip_at(self, t: float, edge: bool = False):
            """时间线 t 秒落在第几段、段里第几秒；edge=True 时段尾算本段。"""
            acc = 0.0
            for i, c in enumerate(self.project.clips):
                end = acc + c.span
                if (t <= end + 1e-9) if edge else (t < end - 1e-9):
                    return i, max(0.0, t - acc)
                acc = end
            return -1, 0.0

        def _playhead(self) -> float:
            # 还没有音源（刚打开工程、混音没跑完）时播放器位置恒为 0，
            # 这时用时间线上的播放头，免得设入点/切段读到 0。
            if self.player.source().isEmpty():
                return max(0.0, self.timeline.play_pos)
            return max(0.0, self.player.position() / 1000.0)

        def _text_focused(self) -> bool:
            fw = QApplication.focusWidget()
            return isinstance(fw, (QLineEdit, QTextEdit, QPlainTextEdit))

        def on_mark_in(self):
            if self._text_focused():
                return
            i, off = self._clip_at(self._playhead(), edge=True)
            if i < 0:
                return
            clip = self.project.clips[i]
            if not clip.is_video:
                self.lbl_status.setText(tr("图片没有入点，只有视频段能裁"))
                return
            self.project.set_src_in(i, clip.src_in + off)
            self._after_trim(tr("设入点"))

        def on_mark_out(self):
            if self._text_focused():
                return
            i, off = self._clip_at(self._playhead(), edge=True)
            if i < 0:
                return
            clip = self.project.clips[i]
            if not clip.is_video:
                self.lbl_status.setText(tr("图片没有出点，只有视频段能裁"))
                return
            self.project.set_src_out(i, clip.src_in + off)
            self._after_trim(tr("设出点"))

        def on_split_at(self):
            """Ctrl+K：在播放头把当前段切一刀。"""
            t = self._playhead()
            if not self.project.split_at(t):
                self.lbl_status.setText(tr("这里切不了：离段首或段尾太近"))
                return
            self._refresh_all()
            self._history_push(tr("切一刀"))
            self.lbl_status.setText(
                tr("{n} 段素材 · 总长 {total}", n=self.project.segment_count(),
                   total=human_time(self.project.total())))

        def _after_trim(self, action: str):
            self._update_titles()
            self.timeline.update()
            self._mark_stale()
            self._history_push(action)
            self.lbl_status.setText(
                tr("{n} 段素材 · 总长 {total}", n=self.project.segment_count(),
                   total=human_time(self.project.total())))

        def on_timeline_move(self, i, pos):
            log("timeline move i=%d -> %d" % (i, pos))
            self.project.move_to(i, pos)
            self._refresh_all()
            self._history_push(tr("调整顺序"))
            if 0 <= i < self.list.count():
                self.list.setCurrentRow(i)

        def on_timeline_block_move(self, rows, dest):
            """时间线上多选的一段整块拖到别处：保持内部顺序和各自的设置。"""
            rows = [int(r) for r in (rows or [])]
            log("timeline block move rows=%s -> %s" % (rows, dest))
            if not self.project.move_block(rows, dest):
                return
            self._refresh_all()
            self._select_rows(list(range(dest, dest + len(rows))))
            self._history_push(tr("挪动选中的段"))
            self.lbl_status.setText(
                tr("已把 {n} 段挪到第 {k} 段", n=len(rows), k=dest + 1))

        def on_audio_group_move(self, items):
            """多选音频一起拖：保持彼此间距，整组挪同一个偏移。"""
            items = list(items or [])
            for k, start in items:
                if 0 <= k < len(self.project.audios):
                    self.project.audios[k].start = max(0.0, float(start))
            if not items:
                return
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("移音频"))
            self.lbl_status.setText(tr("已挪 {n} 条音频", n=len(items)))

        def on_subtitle_move(self, k, start):
            """字幕轨上整条拖动：改这条字幕的进出时间，导出跟着走。"""
            if not self.project.set_cue_start(int(k), float(start)):
                return
            self._refresh_subtitles()
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("改字幕时间"))
            self.lbl_status.setText(tr("字幕已挪到 {time}", time=human_time(
                float(start))))

        def add_audio_extra(self, path: str):
            """再叠一条音频（不占 BGM 那条，起始位置跟着播放头）。"""
            path = os.path.normpath(path)
            if not os.path.isfile(path):
                return
            log("add audio %s" % os.path.basename(path))
            dur = max(0.2, probe_duration(path) or 3.0)
            pos = min(max(0.0, self.player.position() / 1000.0),
                      max(0.0, self.project.total()))
            self.project.audios.append(
                AudioClip(path, dur, pos, 1.0, gap=False))
            self._refresh_all()
            self.lbl_status.setText(
                tr("已加音频 {name}", name=os.path.basename(path)))
            self._history_push(tr("加音频"))

        def on_add_audio(self):
            picked = FileDialog.get_open_files(
                self, tr("添加音频文件"), "",
                tr("音频 (*.mp3 *.m4a *.aac *.wav *.flac *.ogg)"))
            for p in picked:
                self.add_audio_extra(p)

        def on_toggle_snap(self, on):
            on = bool(on)
            if not hasattr(self, "timeline"):
                return          # 建界面的早期 setChecked 也会发 toggled
            self.timeline.set_snap(on)
            self.settings.update(snap_on=on)
            self.settings.save()
            self.lbl_status.setText(tr("磁吸已开") if on else tr("磁吸已关"))

        def on_toggle_razor(self, on):
            on = bool(on)
            if not hasattr(self, "timeline"):
                return
            self.timeline.set_razor(on)
            self.lbl_status.setText(
                tr("切割模式已开：点哪块切哪块，Esc 退出") if on
                else tr("切割模式已关"))

        def on_cut(self, kind, index, t):
            """刀片：在点击处切开画面段或音频段。"""
            if kind == "audio":
                ok = self.project.split_audio(index, t)
            else:
                ok = self.project.split_at(t)
            if not ok:
                self.lbl_status.setText(tr("这儿切不了：离段首或段尾太近"))
                return
            self._refresh_all()
            self._mark_stale()
            self._history_push(tr("切开"))
            self.lbl_status.setText(tr("已切开"))

        def on_audio_trim(self, k, edge, delta):
            """拖音频左右边：左边裁头（起点与源入点一起走），右边裁尾。"""
            if not (0 <= k < len(self.project.audios)):
                return
            a = self.project.audios[k]
            if a.gap:
                return
            step = float(delta)
            if edge == "left":
                step = max(-a.start, min(max(0.0, a.dur - 0.1), step))
                if abs(step) < 1e-6:
                    return
                a.start = max(0.0, a.start + step)
                a.src_in = max(0.0, a.src_in + step)
                a.dur = max(0.1, a.dur - step)
                a.points = clean_env_points([[max(0.0, p - step), g]
                                             for p, g in a.points])
            else:
                src_len = probe_duration(a.path) or (a.src_in + a.dur)
                room = max(0.1, src_len - a.src_in)
                a.dur = min(room, max(0.1, a.dur + step))
            self.timeline.update()
            self._mark_stale()
            self.lbl_status.setText(
                tr("这条音频 {a} → {b} 秒", a=human_time(a.start),
                   b=human_time(a.start + a.dur)))

        def on_audio_trim_done(self, k):
            self._history_push(tr("裁音频"))
            self._mark_dirty()
            self._refresh_scene()

        def on_remove_audio(self, k):
            if 0 <= k < len(self.project.audios):
                log("remove audio k=%d" % k)
                self.project.audios.pop(k)
                self.timeline.sel_audio = []
                self._refresh_all()
                self._mark_stale()
                self._history_push(tr("删音频"))

        def on_remove_audio_ripple(self, rows):
            """涟漪删除：删掉这几条，后面的音频往前补上空出来的时间。"""
            if not rows:
                return
            self.project.remove_audio(rows, ripple=True)
            self.timeline.sel_audio = []
            self._refresh_all()
            self._mark_stale()
            self._history_push(tr("删音频并前移"))

        def on_audio_move(self, k, start):
            if not (0 <= k < len(self.project.audios)):
                return
            self.project.audios[k].start = max(0.0, float(start))
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("移音频"))

        def on_audio_volume(self, k, delta):
            self.on_audio_set_volume([k], None, delta)

        def on_audio_set_volume(self, rows, value=None, delta=None):
            """把这几条音频的音量改成 value，或者各自加上 delta。"""
            log("audio volume rows=%s value=%s delta=%s"
                % (rows, value, delta))
            for k in rows:
                if not (0 <= k < len(self.project.audios)):
                    continue
                a = self.project.audios[k]
                if value is None:
                    a.volume = max(0.0, min(2.0, a.volume + (delta or 0.0)))
                else:
                    a.volume = max(0.0, min(2.0, value))
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("改音量"))
            if rows and 0 <= rows[0] < len(self.project.audios):
                self.lbl_status.setText(tr("音量 {pct}%", pct=int(round(
                    self.project.audios[rows[0]].volume * 100))))

        # ---------------- 画中画
        def on_add_overlay(self):
            base = (self.settings["last_export_dir"] or self._project_dir())
            picked = FileDialog.get_open_files(
                self, tr("选叠加素材"), base,
                tr("图片或视频 (*.jpg *.jpeg *.png *.bmp *.webp *.mp4"
                   " *.mov *.mkv *.webm *.avi)"))
            if not picked:
                return
            path = picked[0]
            if not os.path.exists(path):
                MsgBox.warn(self, tr("素材不在了"), path)
                return
            if (not path.lower().endswith(tuple(MEDIA_EXTS))
                    and not image_ok(path)):
                MsgBox.warn(self, tr("这个文件读不出来"), path)
                return
            total = max(0.1, self._export_total())
            ov = OverlayDialog.get_overlay(
                self, Overlay(path, 0.0, min(total, 3.0)), total)
            if ov is None:
                return
            self.project.overlays.append(ov)
            self.timeline.sel_overlay = [len(self.project.overlays) - 1]
            self._refresh_all()
            self.lbl_status.setText(tr("加了一层画中画"))
            self._history_push(tr("加画中画"))

        def on_edit_overlay(self, k):
            if not (0 <= k < len(self.project.overlays)):
                return
            total = max(0.1, self._export_total())
            ov = OverlayDialog.get_overlay(
                self, self.project.overlays[k], total)
            if ov is None:
                return
            self.project.overlays[k] = ov
            self._refresh_all()
            self.lbl_status.setText(tr("画中画改好了"))
            self._history_push(tr("改画中画"))

        def on_remove_overlay(self, k):
            if not (0 <= k < len(self.project.overlays)):
                return
            self.project.overlays.pop(k)
            self.timeline.sel_overlay = []
            self._refresh_all()
            self.lbl_status.setText(tr("删掉一层画中画"))
            self._history_push(tr("删画中画"))

        def on_overlay_moved(self, k, start):
            if not (0 <= k < len(self.project.overlays)):
                return
            ov = self.project.overlays[k]
            span = max(OVERLAY_MIN_SPAN, ov.end - ov.start)
            ov.start = max(0.0, float(start))
            ov.end = ov.start + span
            self._refresh_all()
            self._history_push(tr("移动画中画"))

        def on_timeline_menu(self, kind, index, pos):
            m = self.timeline_menu(kind, index)
            if m is not None:
                m.exec(pos)

        def timeline_menu(self, kind, index):
            rows = list(self.timeline.selected)
            arows = list(self.timeline.sel_audio)
            if kind in ("overlay", "overlay_empty"):
                m = QMenu(self)
                add = m.addAction(tr("加画中画…"))
                add.triggered.connect(
                    lambda _c=False: self.on_add_overlay())
                if kind == "overlay":
                    m.addSeparator()
                    edit = m.addAction(tr("编辑画中画…"))
                    edit.triggered.connect(
                        lambda _c=False, k=index: self.on_edit_overlay(k))
                    rm = m.addAction(tr("删除画中画"))
                    rm.triggered.connect(
                        lambda _c=False, k=index: self.on_remove_overlay(k))
                return m
            if kind == "empty":
                return None
            m = QMenu(self)
            if kind == "video":
                targets = rows or [index]
                fades = [self.project.clips[i].fade for i in targets
                         if 0 <= i < len(self.project.clips)]
                act = m.addAction(tr("淡入淡出"))
                act.setCheckable(True)
                act.setChecked(bool(fades) and all(fades))
                act.triggered.connect(
                    lambda _c=False, t=targets: self.on_toggle_fade(t))
                col = m.addAction(tr("调色…"))
                col.triggered.connect(
                    lambda _c=False, t=targets: self.on_edit_color(t))
                xm = m.addMenu(tr("转场"))
                cur_names = [self.project.clips[i].xfade_name
                             for i in targets
                             if 0 <= i < len(self.project.clips)
                             and self.project.clips[i].xfade]
                on_now = any(0 <= i < len(self.project.clips)
                             and self.project.clips[i].xfade for i in targets)
                none_act = xm.addAction(tr("无（硬切）"))
                none_act.setCheckable(True)
                none_act.setChecked(not on_now)
                none_act.triggered.connect(
                    lambda _c=False, t=targets: self.on_set_transition(t, ""))
                xm.addSeparator()
                for name in XFADE_NAMES:
                    act = xm.addAction(tr(XFADE_LABELS[name]))
                    act.setCheckable(True)
                    act.setChecked(on_now and all(
                        (n or "fade") == name for n in cur_names))
                    act.setToolTip(name)
                    act.triggered.connect(
                        lambda _c=False, t=targets, n=name:
                        self.on_set_transition(t, n))
                presets = asset_transition_presets()
                if presets:
                    xm.addSeparator()
                    for preset in presets:
                        pact = xm.addAction(
                            tr("{name}（资源包）", name=preset["name"]))
                        pact.setToolTip("%s · %.2f 秒" % (
                            preset["xfade"], preset["duration"]))
                        pact.setCheckable(True)
                        pact.setChecked(on_now and all(
                            (n or "fade") == preset["xfade"] for n in cur_names))
                        pact.triggered.connect(
                            lambda _c=False, t=targets, pr=preset:
                            self.on_set_transition_preset(t, pr))
                xm.addSeparator()
                dm = xm.addMenu(tr("转场时长"))
                cur_dur = [self.project.clips[i].xfade_dur for i in targets
                           if 0 <= i < len(self.project.clips)
                           and self.project.clips[i].xfade]
                follow = dm.addAction(tr("跟随设置"))
                follow.setCheckable(True)
                follow.setChecked(on_now and all(d is None for d in cur_dur))
                follow.triggered.connect(
                    lambda _c=False, t=targets:
                    self.on_set_transition_dur(t, None))
                for sec in (0.2, 0.3, 0.5, 0.8, 1.0):
                    act = dm.addAction(tr("{n} 秒", n=sec))
                    act.setCheckable(True)
                    act.setChecked(on_now and all(
                        d is not None and abs(d - sec) < 1e-6 for d in cur_dur))
                    act.triggered.connect(
                        lambda _c=False, t=targets, v=sec:
                        self.on_set_transition_dur(t, v))
                if (len(targets) == 1 and len(arows) == 1
                        and 0 <= targets[0] < len(self.project.clips)):
                    fill = m.addAction(tr("画面铺满这条音频"))
                    fill.triggered.connect(
                        lambda _c=False, t=targets, a=arows:
                        self.on_fill_audio(t[0], a[0]))
                m.addSeparator()
                dur = m.addAction(tr("这一段显示多久…"))
                dur.setEnabled(len(targets) == 1 and (
                    not self.project.clips[targets[0]].is_video
                    or self.project.clips[targets[0]].kind == "video"))
                dur.triggered.connect(
                    lambda _c=False, t=targets:
                    self.on_edit_dur(self.list.item(t[0])))
            else:
                targets = arows or [index]
                if len(targets) == 1 and 0 <= index < len(self.project.audios):
                    a = self.project.audios[index]
                    info = m.addAction(tr("音量 {pct}%", pct=int(round(
                        a.volume * 100))))
                    info.setEnabled(False)
                    m.addSeparator()
                setact = m.addAction(tr("设置音量…"))
                setact.triggered.connect(
                    lambda _c=False, t=targets: self.on_ask_volume(t))
                quick = m.addMenu(tr("音量预设"))
                for pct in (25, 50, 75, 100, 150, 200):
                    q = quick.addAction(f"{pct}%")
                    q.triggered.connect(
                        lambda _c=False, t=targets, v=pct / 100.0:
                        self.on_audio_set_volume(t, value=v))
                gain = m.addAction(tr("增益…"))
                gain.triggered.connect(
                    lambda _c=False, t=targets: self.on_ask_gain(t))
                clr = m.addAction(tr("清空包络"))
                clr.setEnabled(any(self.project.audios[i].points
                                   for i in targets
                                   if 0 <= i < len(self.project.audios)))
                clr.triggered.connect(
                    lambda _c=False, t=targets: self.on_clear_envelope(t))
                m.addSeparator()
                add = m.addAction(tr("添加音频文件…"))
                add.triggered.connect(lambda _c=False: self.on_add_audio())
                extra = [k for k in targets if 0 <= k < len(self.project.audios)
                         and not self.project.audios[k].gap]
                if extra:
                    rm = m.addAction(tr("删除这条音频"))
                    rm.triggered.connect(
                        lambda _c=False, k=extra[0]: self.on_remove_audio(k))
                    rm2 = m.addAction(tr("删除并前移"))
                    rm2.setToolTip(tr("删掉这条音频，后面的音频整体往前挪"))
                    rm2.triggered.connect(
                        lambda _c=False, t=extra:
                        self.on_remove_audio_ripple(t))
            return m

        def on_toggle_fade(self, rows):
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            want = not all(self.project.clips[i].fade for i in rows)
            for i in rows:
                self.project.clips[i].fade = want
            self._refresh_all()
            self._history_push(tr("淡入淡出"))

        def on_edit_color(self, rows):
            """给选中的画面元素调色（多选时一次用同一套参数）。"""
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            vals = ColorDialog.get_values(self, self.project.clips[rows[0]],
                                          self.settings["lut_dir"])
            if vals is None:
                return
            b, c, s, lut = vals
            for i in rows:
                clip = self.project.clips[i]
                clip.brightness, clip.contrast = b, c
                clip.saturation, clip.lut = s, lut
            self._refresh_all()
            self._history_push(tr("调色"))

        def on_set_transition(self, rows, name):
            """给选中的画面元素挑转场；name 空 = 硬切。"""
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            for i in rows:
                c = self.project.clips[i]
                if name:
                    c.xfade = True
                    c.xfade_name = name
                else:
                    c.xfade = False
                    c.xfade_name = ""
                    c.xfade_dur = None
            self._refresh_all()
            self._history_push(tr("转场"))
            self._check_transitions()

        def on_set_transition_preset(self, rows, preset):
            """资源包里的转场预设：内置名 + 它自己的默认时长。"""
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            name = preset.get("xfade") or "fade"
            try:
                dur = float(preset.get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            for i in rows:
                c = self.project.clips[i]
                c.xfade = True
                c.xfade_name = name if name in XFADE_NAMES else "fade"
                c.xfade_dur = dur if dur > 0 else None
            self._refresh_all()
            self._history_push(tr("转场"))
            self._check_transitions()

        def on_set_transition_dur(self, rows, sec):
            """转场的时长：None = 跟随设置里的默认值。"""
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            for i in rows:
                self.project.clips[i].xfade_dur = (None if sec is None
                                                  else float(sec))
            self._refresh_all()
            self._history_push(tr("转场时长"))
            self._check_transitions()

        def _check_transitions(self):
            """转场塞不下时当场提醒（不然要到导出才报错）。"""
            s = self.settings
            short = xfade_short(self.project.clips, self.project.frame_counts(),
                                int(s["fps"]), float(s["transition_sec"]))
            if short:
                MsgBox.warn(self, tr("转场放不下"), xfade_error_text(short))

        def on_fill_audio(self, clip_row, audio_row):
            """让这一个画面元素正好铺满它下面那条音频。"""
            if not (0 <= clip_row < len(self.project.clips)):
                return
            if not (0 <= audio_row < len(self.project.audios)):
                return
            a = self.project.audios[audio_row]
            dur = a.dur or probe_duration(a.path)
            if dur <= 0:
                return
            if self.project.clips[clip_row].is_video:
                self.project.set_video_len(clip_row, dur)
            else:
                self.project.set_duration(clip_row, dur)
            self._refresh_all()
            self._history_push(tr("铺满音频"))

        def on_ask_gain(self, rows):
            """每段增益（dB）：0 就是原样，负数压下去、正数抬起来。"""
            rows = [k for k in rows if 0 <= k < len(self.project.audios)]
            if not rows:
                return
            cur = self.project.audios[rows[0]].gain_db
            val, ok = NumberDialog.get_double(
                self, tr("增益"), tr("这条音频的增益（分贝，0 就是原样）："),
                cur, AUDIO_GAIN_MIN, AUDIO_GAIN_MAX, 1, suffix=tr(" 分贝"))
            if not ok:
                return
            for k in rows:
                self.project.audios[k].gain_db = clamp_gain_db(val)
            self._refresh_all()
            self._history_push(tr("增益"))

        def on_clear_envelope(self, rows):
            rows = [k for k in rows if 0 <= k < len(self.project.audios)]
            if not rows:
                return
            for k in rows:
                self.project.audios[k].points = []
            self._refresh_all()
            self._history_push(tr("清空包络"))

        def on_envelope_changed(self, k):
            """时间线上加了 / 拖了 / 删了包络点。"""
            self._refresh_all()
            self._history_push(tr("音量包络"))

        def on_ask_volume(self, rows):
            rows = [k for k in rows if 0 <= k < len(self.project.audios)]
            if not rows:
                return
            cur = self.project.audios[rows[0]].volume * 100
            val, ok = NumberDialog.get_double(
                self, tr("音量"),
                tr("音量（%）："), cur, 0, 200, 0)
            if ok:
                self.on_audio_set_volume(rows, value=val / 100.0)

        # ---------------- 预览（实时合成，不再渲整片）
        def _player_duration(self) -> float:
            if self.scene is not None and self.scene.total > 0:
                return self.scene.total
            return max(0.0, self.player.duration() / 1000.0)

        def _player_time_text(self, cur: float) -> str:
            return "%s / %s" % (human_time(cur),
                                human_time(self._player_duration()))

        def _render_at(self, t: float):
            """按时间现画一帧贴到预览画布上。"""
            if self.scene is None or self.scene.total <= 0:
                return
            t = max(0.0, min(float(t), max(0.0, self.scene.total - 0.001)))
            try:
                self.video.set_frame(self.scene.frame_at(t))
            except Exception as e:  # noqa: BLE001
                log("preview frame failed: %s" % e)

        def _draw_frame(self, t: float):
            self._render_at(t)
            self.timeline.play_pos = max(0.0, float(t))
            if (self.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState):
                # 播放头走出可见窗口就把窗口跟上（缩放过才需要）
                self.timeline.ensure_visible(self.timeline.play_pos)
            self.timeline.update()
            self._sync_player(max(0.0, float(t)))

        def _tick_frame(self):
            """播放时按播放器位置刷新画面（16ms 一次，只画不重算）。"""
            if self.scene is None or self._scrubbing:
                return
            if (self.player.playbackState()
                    != QMediaPlayer.PlaybackState.PlayingState):
                return
            self._draw_frame(self.player.position() / 1000.0)

        def _sync_preview_meta(self):
            total = self.scene.total if self.scene is not None else 0.0
            ms = int(round(total * 1000))
            self.slider_pos.setRange(0, max(0, ms))
            self.slider_pos.setEnabled(ms > 0)
            self._sync_player(min(self._playhead(),
                                  max(0.0, total - 0.001)))

        def _refresh_scene(self):
            """时间线/参数一变就重建场景模型（图还在缓存里），并立刻画一帧。"""
            if not hasattr(self, "video"):
                return                    # 界面还没搭好
            if not self.project.clips:
                self.scene = None
                self.video.set_frame(None)
                self._sync_preview_meta()
                return
            opts = self._opts(preview=True)
            clips = list(self.project.clips)
            counts = self.project.frame_counts()
            if self.scene is None:
                self.scene = Scene(clips, counts, int(opts["fps"]), opts,
                                   overlays=opts["overlays"],
                                   cues=opts["subtitles"])
            else:
                self.scene.reconfigure(clips, counts, int(opts["fps"]), opts,
                                       overlays=opts["overlays"],
                                       cues=opts["subtitles"])
            self._sync_preview_meta()
            self._draw_frame(self._playhead())
            self._clean_stale()              # 画面已经跟上，不用再挂着「待刷新」
            self._mix_timer.start()          # 音频防抖：连着改只混最后一次
            QTimer.singleShot(150, self._warm_scene)

        def _warm_scene(self):
            """停着的时候把后面一两段先铺好底图。"""
            if self.scene is None or not self.project.clips:
                return
            if (self.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState):
                return
            i, _lt = self.scene._segment_at(self._playhead())
            try:
                self.scene.warm(i, 2)
            except Exception:  # noqa: BLE001
                pass

        def _audio_signature(self) -> str:
            """音轨内容指纹：只改画面参数时不必重混音频。"""
            pr = self.project
            bits = []
            for c in pr.clips:
                bits.append("%s|%d|%s" % (c.path, int(c.dur * 1000), c.kind))
            for a in pr.audios:
                bits.append("%s|%d|%d|%.3f|%.3f|%s|%s" % (
                    a.path, int(a.start * 1000), int(a.dur * 1000),
                    a.volume, a.gain_db, a.gap, tuple(a.points)))
            s = self.settings
            bits += ["%g" % float(s["fade_in"]), "%g" % float(s["fade_out"]),
                     "%g" % float(s["music_volume"]),
                     "%g" % float(s["music_start"]),
                     "%g" % float(s["transition_sec"])]
            return hashlib.sha1(";".join(bits).encode("utf-8", "replace")
                                ).hexdigest()

        def _start_audio_mix(self, force: bool = False):
            if not self.project.clips:
                return
            if (self._audio_worker is not None
                    and self._audio_worker.isRunning()):
                self._audio_pending = True
                return
            sig = self._audio_signature()
            if (not force and sig == self._audio_sig
                    and not self.player.source().isEmpty()):
                return
            self._audio_seq += 1
            path = os.path.join(self.tmpdir,
                                "preview_audio_%d.m4a" % self._audio_seq)
            worker = AudioMixWorker(
                list(self.project.clips), self.project.frame_counts(),
                list(self.project.audios), path, opts=self._scene_opts(),
                sig=sig, parent=self)
            worker.ok.connect(self._on_audio_ok)
            worker.bad.connect(self._on_audio_bad)
            self._audio_worker = worker
            worker.start()

        def _scene_opts(self) -> dict:
            return self._opts(preview=True)

        def _on_audio_ok(self, path, sig):
            log("preview audio ok %s" % path)
            self._audio_worker = None
            self._audio_sig = sig
            pos = self._playhead()
            keep = (self.player.playbackState()
                    == QMediaPlayer.PlaybackState.PlayingState)
            if self._play_after_mix:
                self._play_after_mix = False
                keep = True
            if pos > 0.05:
                self._resume_at = pos
            try:
                self.player.stop()
                self.player.setSource(QUrl.fromLocalFile(path))
            except Exception as e:  # noqa: BLE001
                log("preview audio attach failed: %s" % e)
            self.player.setPlaybackRate(float(self.settings["preview_speed"]))
            if keep:
                self.player.play()
            self.lbl_preview.setText(tr("预览：实时合成"))
            self._draw_frame(self._playhead())
            if self._audio_pending:
                self._audio_pending = False
                self._start_audio_mix()
            if self.preview_stale:
                self._clean_stale()

        def _on_audio_bad(self, msg):
            log("preview audio failed: %s" % msg)
            self._audio_worker = None
            self.lbl_status.setText(tr("预览音频失败"))
            if os.environ.get("PIC2VIDEO_SHOT"):
                print("PREVIEW_AUDIO_FAILED:", msg)
                return
            MsgBox.warn(self, tr("预览失败"), msg)

        def seek(self, seconds):
            dur = self._player_duration()
            if dur > 0:
                seconds = min(max(0.0, seconds), max(0.0, dur - 0.02))
            seconds = max(0.0, seconds)
            if not self.player.source().isEmpty():
                self.player.setPosition(int(seconds * 1000))
            self._draw_frame(seconds)

        def _sync_player(self, cur: float):
            """把播放位置同步到进度条与时间标签（拖动时不抢）。"""
            if not self._scrubbing:
                self.slider_pos.blockSignals(True)
                self.slider_pos.setValue(int(cur * 1000))
                self.slider_pos.blockSignals(False)
            self.lbl_playtime.setText(self._player_time_text(cur))

        def _sync_play_button(self):
            playing = (self.player.playbackState()
                       == QMediaPlayer.PlaybackState.PlayingState)
            self.btn_play.setText("⏸" if playing else "▶")
            self.btn_play.setToolTip(
                tr("暂停（空格）") if playing else tr("播放（空格）"))

        def on_skip(self, delta: float):
            t = self._playhead() + delta
            self.seek(max(0.0, t))

        def eventFilter(self, obj, ev):
            """←/→ 一帧，Shift+←/→ 一秒，Home/End 跳首尾。

            装在 QApplication 上，所以要绕开输入框、下拉框、列表这些
            自己要用方向键的控件。
            """
            if (ev.type() != QEvent.Type.KeyPress
                    or QApplication.activeModalWidget() is not None
                    or QApplication.activePopupWidget() is not None
                    or not self.isActiveWindow()):
                return super().eventFilter(obj, ev)
            focus = QApplication.focusWidget()
            if isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit,
                                  QAbstractSpinBox, QComboBox,
                                  QAbstractItemView, QSlider)):
                return super().eventFilter(obj, ev)
            key = ev.key()
            ctrl = bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if ctrl and key in (Qt.Key.Key_0, Qt.Key.Key_Equal,
                                Qt.Key.Key_Plus, Qt.Key.Key_Minus):
                # Ctrl+0 回到全片，Ctrl+= / Ctrl+- 以时间线中间为准缩放
                if key == Qt.Key.Key_0:
                    self.timeline.reset_view()
                else:
                    self.on_tl_zoom(key not in (Qt.Key.Key_Minus,))
                return True
            if key == Qt.Key.Key_Escape and self.timeline.razor:
                self.btn_razor.setChecked(False)   # 走 toggled → on_toggle_razor
                return True
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                step = 1.0
                if not (ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                    step = 1.0 / max(1.0, float(self.settings["fps"]))
                self.on_skip(-step if key == Qt.Key.Key_Left else step)
                return True
            if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
                self.seek(0.0 if key == Qt.Key.Key_Home
                          else self._export_total())
                return True
            return super().eventFilter(obj, ev)

        def on_scrub_start(self):
            self._scrubbing = True

        def on_scrub_move(self, ms):
            self._scrubbing = True
            cur = ms / 1000.0
            self.lbl_playtime.setText(self._player_time_text(cur))
            self.timeline.play_pos = cur
            self.timeline.update()
            self._render_at(cur)

        def on_scrub_end(self):
            self._scrubbing = False
            self.seek(self.slider_pos.value() / 1000.0)

        # ---------------- 时间线缩放 / 横向滚动
        def _on_tl_view(self, start, span):
            """时间线自己缩放/滚动了：把下面的滚动条和倍数标签对齐。"""
            self._sync_tl_bar()
            self._sync_tl_scroll()

        def _sync_tl_scroll(self):
            if not hasattr(self, "tl_scroll"):
                return
            tl = self.timeline
            total = tl._total()
            span = tl._view_span()
            tot_ms = max(0, int(round(total * 1000)))
            span_ms = max(1, int(round(span * 1000)))
            self.tl_scroll.blockSignals(True)
            self.tl_scroll.setRange(0, max(0, tot_ms - span_ms))
            self.tl_scroll.setPageStep(span_ms)
            self.tl_scroll.setSingleStep(max(1, int(round(span_ms / 20.0))))
            self.tl_scroll.setValue(int(round(tl.view_start * 1000)))
            self.tl_scroll.setEnabled(tl.zoom > 1.001 and tot_ms > span_ms)
            self.tl_scroll.blockSignals(False)
            self.lbl_zoom.setText("%.1f×" % tl.zoom if tl.zoom > 1.001
                                  else tr("全片"))
            self.btn_fit.setEnabled(tl.zoom > 1.001)

        def on_tl_scroll(self, value):
            self.timeline.set_view_start(value / 1000.0)

        def on_tl_fit(self):
            self.timeline.reset_view()

        def on_tl_zoom(self, up: bool):
            self.timeline.zoom_at(self.timeline.width() / 2.0, up)

        def on_speed_changed(self, idx):
            try:
                rate = float(self.cmb_speed.itemData(idx))
            except (TypeError, ValueError):
                return
            self.player.setPlaybackRate(rate)
            self.settings.update(preview_speed=rate)
            self.settings.save()
            self.lbl_status.setText(tr("预览倍速：{n}×", n="%g" % rate))

        def on_quality_changed(self, idx):
            q = self.cmb_quality.itemData(idx)
            if q not in PREVIEW_QUALITY:
                return
            self.settings.update(preview_quality=q)
            self.settings.save()
            self.lbl_status.setText(tr("预览质量：{q}", q=self.cmb_quality.itemText(idx)))
            if self.project.clips:
                self._refresh_scene()

        def on_duration(self, _ms):
            self._sync_preview_meta()

        def on_play_state(self, _state):
            self._sync_play_button()

        def toggle_play(self):
            if not self.project.clips:
                self.lbl_status.setText(tr("先加素材"))
                return
            if self.scene is None:
                self.on_preview()
                return
            if self.player.source().isEmpty():
                self._play_after_mix = True
                self._start_audio_mix(force=True)
                return
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def on_media_status(self, status):
            if (status == QMediaPlayer.MediaStatus.LoadedMedia
                    and getattr(self, "_resume_at", 0.0) > 0.05):
                # 换过音轨后接着刚才的位置看：等文件真加载好再跳
                self.player.setPosition(int(self._resume_at * 1000))
                self._resume_at = 0.0
                self._draw_frame(self.player.position() / 1000.0)
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
                self.player.play()
                self._draw_frame(0.0)
            self._sync_play_button()

        def on_position(self, ms):
            self.timeline.play_pos = ms / 1000.0
            self.timeline.update()
            self._sync_player(ms / 1000.0)

        def on_player_error(self, _err, text):
            """播放器解不开预览文件时，说清楚，并试着重新挂一次。"""
            if not text:
                return
            self.lbl_status.setText(tr("预览播放出错：{msg}", msg=text))
            src = self.player.source()
            if not src.isEmpty():
                def retry():
                    self.player.setSource(QUrl())
                    self.player.setSource(src)
                    self.player.play()
                QTimer.singleShot(400, retry)

        def _rendering(self, on, kind="export"):
            """渲染中：禁用导出/刷新，露出进度条和「取消」。"""
            self.btn_export.setEnabled(not on)
            self.btn_refresh.setEnabled(not on)
            self.progress.setVisible(on)
            self.progress.setValue(0)
            self.btn_abort.setVisible(on)
            self.btn_abort.setEnabled(on)
            if on:
                self._render_kind = kind
                self._render_t0 = time.time()

        def _on_render_progress(self, p):
            """进度条 + 导出时的剩余时间。"""
            self.progress.setValue(int(p * 1000))
            if self._render_kind != "export":
                self.lbl_status.setText(tr("生成预览… {pct}%", pct=int(p * 100)))
                return
            spent = time.time() - self._render_t0
            left = max(0.0, spent / p - spent) if p > 0.02 else 0.0
            if p < 0.02 or left < 2:
                self.lbl_status.setText(
                    tr("正在导出… {pct}%", pct=int(p * 100)))
            else:
                self.lbl_status.setText(
                    tr("正在导出… {pct}% · 剩余约 {left}",
                       pct=int(p * 100), left=human_time(left)))

        def on_abort(self):
            w = self.worker
            if w is None or not w.isRunning():
                return
            self.btn_abort.setEnabled(False)
            self.lbl_status.setText(tr("正在取消…"))
            w.cancel()

        def on_render_stopped(self):
            self._rendering(False)
            self.lbl_status.setText(tr("已取消"))

        def on_preview(self):
            """手动「刷新预览」：画面本来就是实时的，这里顺带重混音轨。"""
            if not self.project.clips:
                self.lbl_status.setText(tr("先加素材"))
                return
            self._follow_audio()
            self._refresh_scene()
            self._audio_sig = None
            self._start_audio_mix(force=True)
            self.lbl_status.setText(tr("预览已更新（实时合成）"))
            self.preview_name = tr("实时合成")
            self.lbl_preview.setText(tr("预览：{name}", name=self.preview_name))

        # ---------------- BGM
        def on_bgm_typed(self, _text):
            self.timer.start()

        def start_fetch(self):
            url = self.edit_bgm.text().strip()
            if not url:
                return
            if self.fetch_worker is not None and self.fetch_worker.isRunning():
                return
            if kind_of(url) == "web" and not ytdlp_ready():
                if not self._offer_ytdlp():
                    return
            log("fetch start %s" % url[:120])
            self.btn_retry.setEnabled(False)
            self.lbl_status.setText(tr("正在取音轨…"))
            self.fetch_worker = FetchWorker(url, persistent_media_dir(), self)
            self.fetch_worker.note.connect(self.lbl_status.setText)
            self.fetch_worker.ok.connect(self.on_fetch_ok)
            self.fetch_worker.bad.connect(self.on_fetch_bad)
            self.fetch_worker.start()

        def _offer_ytdlp(self) -> bool:
            """非 B 站链接需要音源组件时问一句，点了就后台装。"""
            yes = MsgBox.ask(
                self, tr("需要音源组件"),
                tr("这个平台的链接要靠 yt-dlp 解析（约 25 MB，"
                   "装在工具目录里，随时可删）。现在下载吗？"),
                tr("下载并继续"), tr("取消"))
            if not yes:
                return False
            self.lbl_status.setText(tr("正在下载音源组件…"))
            worker = ComponentWorker("ytdlp", self)
            worker.note.connect(self.lbl_status.setText)
            worker.done.connect(lambda _v: self.start_fetch())
            worker.bad.connect(lambda m: MsgBox.warn(
                self, tr("音源组件"), m))
            self._ydlp = worker
            worker.start()
            return False

        def on_fetch_ok(self, path, dur):
            log("fetch ok dur=%.2f %s" % (dur, path))
            self.project.audio_path = path
            self.project.audio_dur = dur
            self.project.audio_start = float(self.settings["music_start"])
            self.project.audio_len = float(self.settings["music_len"])
            self.project.set_music_volume(float(self.settings["music_volume"]))
            self.project.normalize()
            self.btn_retry.setEnabled(True)
            self._refresh_all()
            self.lbl_status.setText(
                tr("背景音乐已就绪：{total} · 文件在：{path}",
                   total=human_time(dur), path=path))
            self.edit_bgm.setToolTip(tr("当前背景音乐：{path}", path=path))
            self._history_push(tr("换背景音乐"))

        def on_fetch_bad(self, msg):
            log("fetch failed: %s" % msg)
            self.btn_retry.setEnabled(True)
            self.lbl_status.setText(tr("取音轨失败"))
            MsgBox.warn(self, tr("取音轨失败"), msg)

        # ---------------- 导出
        def _export_total(self) -> float:
            """成片总长（秒）：跟 render 里算总帧数的方式一致。"""
            fps = max(1, int(self.settings["fps"]))
            return sum(self.project.frame_counts()) / float(fps)

        def _ask_export_plan(self):
            """弹导出框；取消返回 None。"""
            counts = self.project.frame_counts()
            fps = int(self.settings["fps"])
            default_at = cover_default_time(
                self.project.clips, counts, fps,
                float(self.settings["transition_sec"]))
            remembered = float(self.settings["cover_at"])
            at = default_at if remembered < 0 else remembered
            return ExportDialog.get_plan(self, self.settings,
                                         self._export_total(), at)

        def on_export(self):
            if not self.project.clips:
                self.lbl_status.setText(tr("先加素材"))
                return
            if self.worker is not None and self.worker.isRunning():
                return
            shot = bool(os.environ.get("PIC2VIDEO_SHOT"))   # 自动化测试绝不弹窗
            if shot:
                plan = export_plan("generic", dict(
                    w=even(self.settings["width"]),
                    h=even(self.settings["height"]),
                    fps=int(self.settings["fps"]),
                    crf=self.settings.crf(), ab="192k"))
                plan.update(cover=False, cover_at=0.0)
            else:
                plan = self._ask_export_plan()
                if plan is None:        # 点了取消
                    return
            first_dir = os.path.dirname(self.project.clips[0].path) or os.getcwd()
            base_dir = (self.settings["export_dir"]
                        or self.settings["last_export_dir"] or first_dir)
            if not os.path.isdir(base_dir):
                base_dir = first_dir
            stem = "slideshow_" + time.strftime("%Y%m%d_%H%M") + plan["suffix"]
            out = os.environ.get("PIC2VIDEO_EXPORT_PATH") or ""
            if out:
                pass
            elif shot:
                out = os.path.join(tempfile.gettempdir(),
                                   "p2v_shot_" + time.strftime("%H%M%S") + ".mp4")
            elif self.settings["ask_export_dir"]:
                out = FileDialog.get_save_file(
                    self, tr("导出到…"),
                    os.path.join(base_dir, stem + ".mp4"),
                    tr("视频 (*.mp4)"))
                if not out:
                    self._cover_plan = None
                    return
            else:                       # 不问了，直接落到默认目录
                out = os.path.join(base_dir, stem + ".mp4")
                i = 2
                while os.path.exists(out):
                    out = os.path.join(base_dir, f"{stem}_{i}.mp4")
                    i += 1
            if not out.lower().endswith(".mp4"):
                out += ".mp4"
            self.settings["last_export_dir"] = os.path.dirname(out) or base_dir
            if plan["preset"] in EXPORT_PRESETS:
                self.settings["export_preset"] = plan["preset"]
            self.settings["cover_export"] = bool(plan.get("cover"))
            self.settings["cover_at"] = float(plan.get("cover_at", -1.0))
            self.settings.save()
            opts = self._opts()
            opts.update(w=plan["w"], h=plan["h"], fps=plan["fps"],
                        crf=plan["crf"], ab=plan["ab"],
                        subtitle_size=subtitle_px(
                            int(self.settings["subtitle_size"]), plan["h"]))
            counts = rescale_counts(self.project.frame_counts(),
                                    int(self.project.fps), plan["fps"])
            cover_total = sum(counts) / float(plan["fps"] or 30)
            self._cover_plan = (dict(cover=True,
                                     at=float(plan.get("cover_at", 0.0)),
                                     total=cover_total)
                                if plan.get("cover") else None)
            if (plan["preset"] in EXPORT_PRESETS
                    and self.project.export_preset != plan["preset"]):
                self.project.export_preset = plan["preset"]
                self._mark_dirty()
            log("export start %s plan=%r" % (out, plan))
            if shot:
                print("EXPORT_START:", out, flush=True)
            self._rendering(True, "export")
            self.lbl_status.setText(tr("正在导出…"))
            self.worker = RenderWorker(
                list(self.project.clips), counts,
                list(self.project.audios), out, opts=opts,
                preview=False, parent=self)
            self.worker.progress.connect(self._on_render_progress)
            self.worker.ok.connect(self.on_export_ok)
            self.worker.bad.connect(self.on_export_bad)
            self.worker.stopped.connect(self.on_render_stopped)
            self.worker.start()

        def _write_cover(self, video_path: str) -> str:
            """导出成功后按计划截封面；失败只提示，不搅乱这次导出。"""
            info, self._cover_plan = self._cover_plan, None
            if not info or not info.get("cover"):
                return ""
            out = os.path.splitext(video_path)[0] + ".jpg"
            try:
                return grab_cover(video_path, out, info.get("at", 0.0),
                                  info.get("total", 0.0))
            except RenderError as exc:
                log("cover failed: %s" % exc)
                if os.environ.get("PIC2VIDEO_SHOT"):
                    print("COVER_FAILED:", exc, flush=True)
                else:
                    MsgBox.warn(self, tr("封面没截出来"), str(exc))
                return ""

        def on_export_ok(self, path):
            log("export ok %s" % path)
            cover = self._write_cover(path)
            self._rendering(False)
            self.lbl_status.setText(
                tr("导出完成：{path}", path=path)
                + (tr("（封面：{name}）", name=cover)
                   if cover else ""))
            if os.environ.get("PIC2VIDEO_SHOT"):
                print("EXPORT_OK:", path, flush=True)
                if cover:
                    print("COVER_OK:", cover, flush=True)
                return
            if self.settings["open_after_export"]:
                try:
                    subprocess.Popen(["explorer", "/select,",
                                      os.path.normpath(path)])
                except OSError:
                    pass
                return
            body = tr("视频已生成：\n{path}", path=path)
            if cover:
                body += tr("\n\n封面：\n{path}", path=cover)
            picked = MsgBox.choose(
                self, tr("导出完成"), body,
                [(tr("打开所在文件夹"), "plain"), (tr("好"), "primary")])
            if picked == 0:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

        def on_export_bad(self, msg):
            log("export failed: %s" % msg)
            self._cover_plan = None
            self._rendering(False)
            self.lbl_status.setText(tr("导出失败"))
            if os.environ.get("PIC2VIDEO_SHOT"):
                print("EXPORT_FAILED:", msg, flush=True)
                return
            MsgBox.warn(self, tr("导出失败"), msg)

        # ---------------- 拖放
        def dragEnterEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dropEvent(self, ev):
            # 拖一整个文件夹进来 = 里面能用的全要
            paths = expand_paths([u.toLocalFile()
                                  for u in ev.mimeData().urls()
                                  if u.isLocalFile()])
            log("drop n=%d %r" % (len(paths), [os.path.basename(p)
                                               for p in paths[:6]]))
            clips = [p for p in paths
                     if os.path.splitext(p)[1].lower() in IMG_EXTS
                     or os.path.splitext(p)[1].lower() in MEDIA_EXTS]
            audio = [p for p in paths
                     if os.path.splitext(p)[1].lower() in AUDIO_EXTS]
            if clips:
                self.add_media(sorted(clips,
                                      key=lambda p: os.path.basename(p).lower()))
            # 拖进来的音频：还没有音乐就当背景音乐，已经有了就叠一条额外音频
            if audio:
                if self.project.audio_path:
                    for p in audio:
                        self.add_audio_extra(p)
                else:
                    self.edit_bgm.setText(audio[0])
                    self.start_fetch()
            if clips or audio:
                ev.acceptProposedAction()

        def nativeEvent(self, event_type, message):
            # 窗口还没装好标题栏时也会收到消息，别在这儿崩
            bar = getattr(self, "titlebar", None)
            try:                          # 这里一旦抛异常就是整个进程崩
                hit = hit_test(self, bar, event_type, message, resize=True)
            except Exception:             # noqa: BLE001
                return False, 0   # 别调 super()：PyQt6 在这里调会崩掉整个进程
            if hit is None:
                return False, 0   # 别调 super()：PyQt6 在这里调会崩掉整个进程
            return True, hit

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            if self._maxed:
                scr = self.screen() or QApplication.primaryScreen()
                if scr is not None and self.geometry() != scr.availableGeometry():
                    self._maxed = False
                    bar = getattr(self, "titlebar", None)
                    if bar is not None:
                        try:
                            bar.sync_max()
                        except Exception:     # noqa: BLE001
                            pass

        def showEvent(self, ev):
            super().showEvent(ev)
            log("window show %dx%d" % (self.width(), self.height()))
            bar = getattr(self, "titlebar", None)
            if bar is not None:
                try:
                    bar.sync_max()
                except Exception:         # noqa: BLE001
                    pass

        def changeEvent(self, ev):
            bar = getattr(self, "titlebar", None)
            if bar is not None and ev.type() == QEvent.Type.WindowStateChange:
                try:
                    bar.sync_max()
                except Exception:         # noqa: BLE001
                    pass
            super().changeEvent(ev)

        def closeEvent(self, ev):
            log("window close")
            busy = self.worker is not None and self.worker.isRunning()
            if busy and not os.environ.get("PIC2VIDEO_SHOT"):
                stay = MsgBox.ask(
                    self, tr("还在导出"),
                    tr("视频还在导出，现在关掉会丢掉这次的结果。"),
                    tr("继续导出"), tr("关掉"), danger=True)
                if stay:
                    ev.ignore()
                    return
            if not os.environ.get("PIC2VIDEO_SHOT") and self._dirty:
                if not self._confirm_discard():
                    ev.ignore()
                    return
            self.autosaver.clear()
            self._save_geometry()
            if self.scene is not None:
                self.scene.close()
            self._frame_timer.stop()
            self._mix_timer.stop()
            if self.worker is not None and self.worker.isRunning():
                self.worker.cancel()
            if self._tts_worker is not None and self._tts_worker.isRunning():
                self._tts_worker.cancel()
            if self._audition_player is not None:
                self._audition_player.stop()
            for w in (self.worker, self.fetch_worker, self._audio_worker,
                      self._tts_worker):
                if w is not None and w.isRunning():
                    w.wait(3000)
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            super().closeEvent(ev)

    def ensure_ffmpeg_gui() -> bool:
        """缺 ffmpeg 时当场下载（约 120 MB，只下一次），装完直接用。"""
        if not missing_tools():
            return True
        if not os.environ.get("PIC2VIDEO_SHOT"):      # 跑测试时别弹窗等点击
            ok = MsgBox.ask(
                None, tr("第一次运行"),
                tr("出片要靠 ffmpeg（约 120 MB，只下这一次，之后不再问）。"
                   "\n现在就自动下载吗？"),
                tr("现在下载"), tr("先不用"))
            if not ok:
                return False

        dlg = ProgressDialog(None, tr("下载 ffmpeg"),
                             tr("正在下载 ffmpeg…"), tr("取消"))

        loop = QEventLoop()
        worker = ComponentWorker("ffmpeg")

        def note(text: str, pct: float = 0.0):
            dlg.set_label(text)
            dlg.set_value(int(pct * 100))

        worker.note.connect(note)
        worker.done.connect(lambda _v: loop.quit())
        worker.bad.connect(lambda _m: loop.quit())
        dlg.canceled.connect(worker.terminate)
        dlg.canceled.connect(loop.quit)
        worker.start()
        loop.exec()
        worker.wait(3000)
        dlg.reset()

        if not components.have("ffmpeg") and missing_tools():
            MsgBox.crit(
                None, tr("下载失败"),
                tr("ffmpeg 没下成功。检查一下网络再开一次，"
                   "或者手动装：winget install Gyan.FFmpeg"))
            return False
        components.prepend_path()
        return True

    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")        # 统一画法：Qt 自带的图标/控件才跟得上配色
    register_asset_fonts()
    ico = asset_path("icon.ico")
    if ico:
        app.setWindowIcon(QIcon(ico))
    if not ensure_ffmpeg_gui():
        return 1
    w = Window()

    # 冒烟测试用：设了 PIC2VIDEO_SHOT 就截图然后退出（不影响正常使用）
    # PIC2VIDEO_SHOT_LANG / PIC2VIDEO_SHOT_UI 可以顺便把语言和模式定下来。
    shot = os.environ.get("PIC2VIDEO_SHOT")
    tag = ""
    if shot:
        lang = os.environ.get("PIC2VIDEO_SHOT_LANG")
        ui = os.environ.get("PIC2VIDEO_SHOT_UI")
        if lang:
            w.settings["lang"] = lang
            set_locale(lang)
            apply_qt_translator(current_locale())
            tag += "_" + lang
        if ui:
            w.settings["ui"] = ui
            tag += "_" + ui
        th = os.environ.get("PIC2VIDEO_SHOT_THEME")
        acc = os.environ.get("PIC2VIDEO_SHOT_ACCENT")
        if th:
            w.settings["theme"] = th
            tag += "_" + th
        if acc:
            w.settings["accent"] = acc
            tag += "_acc"
        if tag:
            w.retranslate()
        if th or acc:
            w.apply_theme()

    w.on_mode(w.cmb_rhythm.currentData())
    w.radio_total.toggled.connect(
        lambda on: on and w.on_mode("total"))
    w.radio_free.toggled.connect(
        lambda on: on and w.on_mode("free"))
    w.radio_voice.toggled.connect(
        lambda on: on and w.on_mode("voice"))
    w.spin_total.valueChanged.connect(w.on_total_changed)
    w.show()

    if shot:
        for p in [x for x in os.environ.get("PIC2VIDEO_SHOT_IMAGES", "").split(";") if x]:
            w.add_images([p])
        audio = os.environ.get("PIC2VIDEO_SHOT_AUDIO")
        if audio:
            w.project.audio_path = audio
            w.project.audio_dur = probe_duration(audio)
            w.project.set_music_volume(float(w.settings["music_volume"]))
            w.project.normalize()
        w._refresh_all()
        def _shot(name):
            w.grab().save(os.path.join(os.path.dirname(shot), name + tag + ".png"))

        # 时间线右键菜单：只把菜单本身截下来（菜单是弹出窗口，抓主窗口抓不到）
        menu_probe = os.environ.get("PIC2VIDEO_SHOT_MENU")
        if menu_probe:
            outdir = os.path.dirname(shot)
            idx = [i for i, c in enumerate(w.project.clips) if c.kind == "image"]
            if menu_probe in ("video", "transition") and idx:
                w.timeline.set_selection(idx[:2] if len(idx) >= 2 else idx[:1])
                menu = w.timeline_menu("video", idx[0])
                name = "shot_menu_video"
            else:
                w.timeline.set_audio_selection([0])
                menu = w.timeline_menu("audio", 0)
                name = "shot_menu_audio"
            # 子菜单单独抓一份（转场那 12 个名字得自己核对一遍）
            sub = None
            if menu_probe == "transition" and idx:
                sub = [a for a in menu.actions()
                       if a.menu() is not None and a.text() == tr("转场")][0]
                sub = sub.menu()
            menu.popup(w.mapToGlobal(w.rect().center()))
            if sub is not None:
                sub.popup(w.mapToGlobal(w.rect().center()) + QPoint(200, 40))

            def grab_menu():
                if sub is not None:
                    sub.grab().save(
                        os.path.join(outdir, "shot_menu_transition" + tag + ".png"))
                    print("SUBMENU", [a.text() for a in sub.actions()],
                          flush=True)
                    sub.close()
                    menu.close()
                    app.quit()
                    return
                menu.grab().save(os.path.join(outdir, name + tag + ".png"))
                menu.close()
                print("MENU", name, [a.text() for a in menu.actions()],
                      flush=True)
                app.quit()

            QTimer.singleShot(500, grab_menu)
            return app.exec()

        # 把各种菜单 / 下拉的弹层挨个抓下来，肉眼核对有没有文字被压
        if os.environ.get("PIC2VIDEO_SHOT_MENU4"):
            outdir = os.path.dirname(shot)
            outs = []
            vidx = [i for i, c in enumerate(w.project.clips)
                    if c.kind == "image"]
            if len(vidx) >= 2:
                w.timeline.set_selection(vidx[:2])
                m1 = w.timeline_menu("video", vidx[0])
                m1.popup(w.mapToGlobal(QPoint(40, 60)))
                QApplication.processEvents()
                m1.grab().save(os.path.join(outdir, "menu_video2" + tag + ".png"))
                outs.append(("video2", [a.text() for a in m1.actions()],
                             m1.sizeHint().width()))
                m1.close()
            if w.project.audios:
                w.timeline.set_audio_selection([0])
                m2 = w.timeline_menu("audio", 0)
                m2.popup(w.mapToGlobal(QPoint(40, 60)))
                QApplication.processEvents()
                m2.grab().save(os.path.join(outdir, "menu_audio2" + tag + ".png"))
                outs.append(("audio2", [a.text() for a in m2.actions()],
                             m2.sizeHint().width()))
                m2.close()
            dlg = SettingsDialog(w.settings, w, w.project)
            dlg.show()
            QApplication.processEvents()
            for nm, cb in (("lang", dlg.cmb_lang), ("ratio", dlg.cmb_ratio),
                           ("quality", dlg.cmb_quality)):
                cb.showPopup()
                QApplication.processEvents()
                view = cb.view()
                view.window().grab().save(
                    os.path.join(outdir, "menu_cb_" + nm + tag + ".png"))
                outs.append(("cb_" + nm, [cb.itemText(i)
                                          for i in range(cb.count())],
                             view.window().width()))
                cb.hidePopup()
            dlg.close()
            for name, texts, width in outs:
                print("MENU4", name, "w=", width, texts, flush=True)
            app.quit()

            return app.exec()

        # 节奏下拉：切到「跟素材总长」看每张时长
        if os.environ.get("PIC2VIDEO_SHOT_RHYTHM"):
            outdir = os.path.dirname(shot)
            print("RHYTHM items:", [w.cmb_rhythm.itemText(i)
                                    for i in range(w.cmb_rhythm.count())],
                  "mode:", w.project.mode, flush=True)
            w.cmb_rhythm.setCurrentIndex(1)
            w.on_mode(w.cmb_rhythm.currentData())
            QApplication.processEvents()
            print("RHYTHM after:", w.project.mode,
                  [round(c.dur, 3) for c in w.project.clips],
                  "frames:", sum(w.project.frame_counts()), flush=True)
            w.grab().save(os.path.join(outdir, "shot_rhythm" + tag + ".png"))
            app.quit()
            return app.exec()
        # 铺满音频：选中一个画面元素 + 一条音频，走右键菜单里那一项
        if os.environ.get("PIC2VIDEO_SHOT_FILL"):
            outdir = os.path.dirname(shot)
            if w.project.audios and len(w.project.clips) >= 2:
                w.timeline.set_selection([0])
                w.timeline.set_audio_selection([0])
                menu = w.timeline_menu("video", 0)
                texts = [a.text() for a in menu.actions()]
                print("FILL menu:", texts, flush=True)
                before = [round(c.dur, 3) for c in w.project.clips]
                for a in menu.actions():
                    if a.text() == tr("画面铺满这条音频"):
                        a.trigger()
                        break
                menu.close()
                after = [round(c.dur, 3) for c in w.project.clips]
                print("FILL audio_dur:", round(w.project.audios[0].dur, 3),
                      "before:", before, "after:", after,
                      "history:", [h["label"] for h in w.history], flush=True)
                w.timeline.grab().save(
                    os.path.join(outdir, "shot_fill" + tag + ".png"))
            else:
                print("FILL skipped", flush=True)
            app.quit()
            return app.exec()
        # 菜单 + 子菜单一起截屏：看文字有没有被压住 / 被裁掉
        if os.environ.get("PIC2VIDEO_SHOT_MENUSUB"):
            outdir = os.path.dirname(shot)
            w.timeline.set_audio_selection([0])
            menu = w.timeline_menu("audio", 0)
            menu.popup(w.mapToGlobal(w.rect().center()))

            def grab_sub():
                sub = None
                for a in menu.actions():
                    if a.menu() is not None:
                        sub = a.menu()
                rect = menu.geometry()
                if sub is not None:
                    sub.popup(menu.mapToGlobal(
                        QPoint(menu.width() - 6, 24)))
                    QApplication.processEvents()
                    rect = rect.united(sub.geometry())
                scr = QApplication.primaryScreen()
                shotpix = scr.grabWindow(0, rect.x(), rect.y(),
                                         rect.width(), rect.height())
                shotpix.save(os.path.join(outdir, "shot_menusub" + tag +
                                          ".png"))
                print("MENUSUB", rect.width(), rect.height(),
                      "| menu:", menu.sizeHint().width(),
                      "| sub:", sub.sizeHint().width() if sub else None,
                      flush=True)
                for a in menu.actions():
                    r = menu.actionGeometry(a)
                    print("  ITEM %-28r geo=%dx%d+%d+%d" % (
                        a.text(), r.width(), r.height(), r.x(), r.y()),
                        flush=True)
                menu.close()
                app.quit()

            QTimer.singleShot(500, grab_sub)
            return app.exec()

        # 单选按钮勾选前后宽度：勾上时文字会不会整体挪一下
        if os.environ.get("PIC2VIDEO_SHOT_JITTER"):
            def jitter():
                for rb in (w.cmb_rhythm, w.radio_total, w.radio_free):
                    if isinstance(rb, QRadioButton):
                        rb.setChecked(False)
                QApplication.processEvents()
                off = [rb.sizeHint().width() for rb in
                       (w.cmb_rhythm, w.radio_total, w.radio_free)]
                w.radio_total.setChecked(True)
                QApplication.processEvents()
                on = [rb.sizeHint().width() for rb in
                      (w.cmb_rhythm, w.radio_total, w.radio_free)]
                print("JITTER off:", off, "on:", on, flush=True)
                from PyQt6.QtWidgets import QStyle, QStyleOptionButton
                def ind(rb):
                    opt = QStyleOptionButton()
                    opt.initFrom(rb)
                    if rb.isChecked():
                        opt.state |= QStyle.StateFlag.State_On
                    else:
                        opt.state &= ~QStyle.StateFlag.State_On
                    r = rb.style().subControlRect(
                        QStyle.ComplexControl.CC_RadioButton, opt,
                        QStyle.SubControl.SC_RadioButtonIndicator, rb)
                    return r.width(), r.height()
                for rb in (w.radio_total, w.radio_free):
                    rb.setChecked(False)
                    QApplication.processEvents()
                    print("  RADIO off", rb.text(), ind(rb), flush=True)
                    rb.setChecked(True)
                    QApplication.processEvents()
                    print("  RADIO on ", rb.text(), ind(rb), flush=True)
                    rb.setChecked(False)
                app.quit()

            QTimer.singleShot(600, jitter)
            return app.exec()

        # 自绘对话框的样子（文件框 / 消息框 / 数字框各截一张）
        dlg_probe = os.environ.get("PIC2VIDEO_SHOT_DLG")
        if dlg_probe:
            outdir = os.path.dirname(shot)

            def grab_fd():
                mb = MsgBox(w, tr("提示"), tr("导出完成，要不要打开文件夹？"),
                            [(tr("打开所在文件夹"), "plain"), (tr("好"), "primary")],
                            icon="ask")
                mb.show()

                def grab_mb():
                    mb.grab().save(os.path.join(outdir,
                                                "shot_msgbox" + tag + ".png"))
                    mb.close()
                    nd = NumberDialog(w, tr("这一段显示多久"), tr("秒数"), 3.0,
                                     MIN_DUR, 600.0)

                    def grab_nd():
                        nd.grab().save(os.path.join(outdir,
                                                    "shot_number" + tag + ".png"))
                        nd.close()
                        print("DLG OK", flush=True)
                        app.quit()

                    QTimer.singleShot(500, grab_nd)

                QTimer.singleShot(600, grab_mb)

            QTimer.singleShot(700, grab_fd)
            return app.exec()

        # 自绘边框的命中测试：把 WM_NCHITTEST 直接发给窗口，看回了哪个码
        # （10 左 / 11 右 / 12 上 / 13 左上 / 14 右上 / 15 下 / 16 左下 / 17 右下 /
        #   2 标题栏 / 1 客户区）
        hit_probe = os.environ.get("PIC2VIDEO_SHOT_HIT")
        if hit_probe:
            WM_NCHITTEST = 0x0084
            hwnd = int(w.winId())
            geo = w.geometry()

            dpr = w.devicePixelRatioF() or 1.0

            def probe(name, x, y):
                x, y = int(round(x * dpr)), int(round(y * dpr))   # 物理像素
                lp = ctypes.c_long((y << 16) | (x & 0xFFFF)).value
                code = int(ctypes.windll.user32.SendMessageW(
                    wintypes.HWND(hwnd), WM_NCHITTEST, 0, lp))
                print("HIT %-12s (%d,%d) -> %d" % (name, x, y, code),
                      flush=True)

            probe("left", geo.x() + 3, geo.y() + geo.height() // 2)
            probe("right", geo.right() - 1, geo.y() + geo.height() // 2)
            probe("top", geo.x() + geo.width() // 2, geo.y() + 2)
            probe("bottom", geo.x() + geo.width() // 2, geo.bottom() - 1)
            probe("topleft", geo.x() + 2, geo.y() + 2)
            probe("botright", geo.right() - 1, geo.bottom() - 1)
            probe("titlebar", geo.x() + geo.width() // 2, geo.y() + 16)
            probe("closebtn", geo.right() - 20, geo.y() + 17)
            probe("body", geo.x() + geo.width() // 2, geo.y() + geo.height() - 60)
            QTimer.singleShot(200, app.quit)
            return app.exec()

        # 撤回/还原冒烟：删一条 → 撤回两次 → 还原一次
        if os.environ.get("PIC2VIDEO_SHOT_UNDO"):
            def undo_probe():
                def snap(tag):
                    print("UNDO %-12s clips=%d index=%d/%d %s" % (
                        tag, len(w.project.clips), w.hist_index,
                        len(w.history) - 1,
                        [e["label"] for e in w.history]), flush=True)
                snap("start")
                w.left_tabs.setCurrentIndex(1)
                w.list.setCurrentRow(0)
                w.on_remove()
                snap("after-remove")
                w.on_undo()
                snap("undo1")
                w.on_undo()
                snap("undo2")
                w.on_redo()
                snap("redo1")
                print("UNDO tabs:", w.left_tabs.tabText(0), "/",
                      w.left_tabs.tabText(1), "rows:",
                      w.hist_list.count(), "undo-btn:",
                      w.btn_undo.isEnabled(), "redo-btn:",
                      w.btn_redo.isEnabled(), flush=True)
                QTimer.singleShot(200, app.quit)

            QTimer.singleShot(600, undo_probe)


        # 快捷键冒烟：改成自定义键后重绑，打印实际绑上的序列
        if os.environ.get("PIC2VIDEO_SHOT_KEYS"):
            def keys_probe():
                print("KEYS default:", [sc.key().toString()
                                        for sc in w._shortcuts], flush=True)
                w.settings.update(shortcuts=dict(SHORTCUT_DEFAULTS,
                                                 add="Ctrl+Alt+A",
                                                 preview="F9"))
                w._bind_shortcuts()
                print("KEYS custom :", [sc.key().toString()
                                        for sc in w._shortcuts], flush=True)
                QTimer.singleShot(200, app.quit)

            QTimer.singleShot(400, keys_probe)
            return app.exec()

        # 拖入三分区冒烟：直接发 drop_files，看插前 / 替换 / 插后三种落点
        if os.environ.get("PIC2VIDEO_SHOT_DROP"):
            def drop_probe():
                def names():
                    return [os.path.basename(c.path) for c in w.project.clips]
                extra = os.environ.get("PIC2VIDEO_SHOT_DROP_IMG") or ""
                if not os.path.isfile(extra):
                    print("DROP: 没有 PIC2VIDEO_SHOT_DROP_IMG", flush=True)
                    app.quit()
                    return
                print("DROP start      :", names(), flush=True)
                w.list.drop_files.emit([extra], 1, "before")
                print("DROP before @1  :", names(), flush=True)
                w.list.drop_files.emit([extra], 0, "replace")
                print("DROP replace @0 :", names(), flush=True)
                w.list.drop_files.emit([extra], 0, "after")
                print("DROP after @0   :", names(), flush=True)
                # 画一下落点提示（grab 会同步触发 paintEvent，崩了这里就炸）
                base_dir = os.path.dirname(os.environ.get("PIC2VIDEO_SHOT") or "")
                if base_dir:
                    w.list._zone = (1, "replace")
                    w.list.grab().save(os.path.join(base_dir, "shot_drop_zone.png"))
                    w.list._zone = (1, "before")
                    w.list.grab().save(os.path.join(base_dir, "shot_drop_before.png"))
                w.list._zone = None
                w.list.viewport().update()
                print("DROP history    :", [e["label"] for e in w.history],
                      flush=True)
                QTimer.singleShot(300, app.quit)

            QTimer.singleShot(800, drop_probe)
            return app.exec()

        # 故意在 Qt 回调里抛异常：验证「未捕获异常会连 traceback 写进日志」
        if os.environ.get("PIC2VIDEO_SHOT_CRASH"):
            log("crash probe armed")

            def _boom():
                log("crash probe firing")
                raise RuntimeError("crash probe: uncaught in Qt slot")

            QTimer.singleShot(300, _boom)
            QTimer.singleShot(1500, app.quit)    # 没 abort 的话也退出来
            return app.exec()

        # 「取消」和「窗口记忆」两个冒烟分支，同样只在 PIC2VIDEO_SHOT 下跑
        cancel_after = os.environ.get("PIC2VIDEO_SHOT_CANCEL")
        geo_probe = os.environ.get("PIC2VIDEO_SHOT_GEO")
        if cancel_after and audio:
            w.project.mode = "free"
            for i in range(len(w.project.clips)):
                w.project.set_duration(i, 8.0)
            w._refresh_all()

            def report_cancel():
                out = os.environ.get("PIC2VIDEO_EXPORT_PATH", "")
                print("CANCEL STATUS:", w.lbl_status.text(), flush=True)
                print("CANCEL RUNNING:",
                      bool(w.worker and w.worker.isRunning()), flush=True)
                print("CANCEL FILE EXISTS:",
                      os.path.exists(out) if out else "n/a", flush=True)
                print("CANCEL UI:", w.btn_abort.isVisible(),
                      w.btn_export.isEnabled(), w.progress.isVisible(), flush=True)
                app.quit()

            def do_abort():
                print("STATUS DURING:", w.lbl_status.text(),
                      "| progress:", w.progress.value(), flush=True)
                w.on_abort()
                QTimer.singleShot(2500, report_cancel)

            QTimer.singleShot(500, w.on_export)
            QTimer.singleShot(int(float(cancel_after) * 1000), do_abort)
            return app.exec()
        if geo_probe:
            w.resize(1300, 760)
            w.move(120, 90)
            w._save_geometry()
            w2 = Window()
            print("GEO RESTORED:", w2.width(), w2.height(), w2.x(), w2.y(),
                  flush=True)
            print("SHORTCUTS:", sorted(s.key().toString()
                                       for s in w.findChildren(QShortcut)),
                  flush=True)
            w2.deleteLater()
            for _mode in ("max", "fixed", "remember"):
                w.settings["win_mode"] = _mode
                w.settings["win_fixed_w"] = 1200
                w.settings["win_fixed_h"] = 700
                w.showNormal()
                w._restore_geometry()
                for _ in range(6):
                    app.processEvents()
                    time.sleep(0.05)
                _scr = QApplication.primaryScreen()
                _flag = bool(w.windowState()
                             & Qt.WindowState.WindowMaximized)
                print("WINMODE %-8s %dx%d max=%s flag=%s screen=%dx%d" % (
                    _mode, w.width(), w.height(), w.is_maxed(), _flag,
                    _scr.availableGeometry().width(),
                    _scr.availableGeometry().height()), flush=True)
                if _mode == "max":
                    w.toggle_maximum()
                    app.processEvents()
                    print("RESTORE  %dx%d max=%s" % (
                        w.width(), w.height(), w.is_maxed()), flush=True)
            QTimer.singleShot(300, app.quit)
            return app.exec()

        def shot_settings():
            dlg = SettingsDialog(w.settings, w, w.project)
            dlg.show()

            def after_dlg():
                dlg.close()
                wheel = ColorWheelDialog(w.settings["accent"], tr("选主色"), w)
                wheel.show()

                def grab_wheel():
                    wheel.grab().save(os.path.join(os.path.dirname(shot),
                                                   "shot_wheel" + tag + ".png"))
                    wheel.close()
                    _shot("shot_edit")
                    w.on_preview()

                QTimer.singleShot(600, grab_wheel)

            def grab_dlg():
                tab_name = os.environ.get("PIC2VIDEO_SHOT_TAB")
                if tab_name:
                    tabs = dlg.findChild(QTabWidget)
                    if tabs is not None:
                        for i in range(tabs.count()):
                            if (tabs.tabText(i) == tab_name
                                    or str(i) == tab_name):
                                tabs.setCurrentIndex(i)
                                break
                        dlg.adjustSize()
                dlg.grab().save(os.path.join(os.path.dirname(shot),
                                             "shot_settings" + tag + ".png"))
                preset = os.environ.get("PIC2VIDEO_SHOT_PRESET")
                if not preset:
                    after_dlg()
                    return

                def grab_preset():
                    print("PRESET:", dlg.cmb_preset.currentText(),
                          dlg._accent, theme.complementary(dlg._accent),
                          "| button:", dlg.btn_accent.text(), flush=True)
                    dlg.grab().save(os.path.join(
                        os.path.dirname(shot), "shot_settings_preset" + tag + ".png"))
                    after_dlg()

                def use_preset():
                    i = int(preset)
                    dlg.cmb_preset.setCurrentIndex(i)
                    dlg._on_preset(i)
                    QTimer.singleShot(400, grab_preset)

                QTimer.singleShot(300, use_preset)

            QTimer.singleShot(700, grab_dlg)

        def step1():
            shot_settings()
            QTimer.singleShot(4000, step2)

        def step2():
            _shot("shot_preview")
            if os.environ.get("PIC2VIDEO_SHOT_REPEAT"):
                # 连着重渲两次预览：验证旧文件被松手、能正常播放
                w.on_preview()
                QTimer.singleShot(300, w.on_preview)
            if audio:
                w.on_export()
                QTimer.singleShot(6000, step3)
            else:
                app.quit()

        def step3():
            _shot("shot_export")
            if os.environ.get("PIC2VIDEO_SHOT_REPEAT"):
                print("PREVIEW SRC:", w.player.source().toLocalFile(), flush=True)
                print("PREVIEW STATE:", w.player.playbackState().name,
                      "| err:", w.player.errorString(), flush=True)
                print("PREVIEW STATUS:", w.lbl_status.text(), flush=True)
            app.quit()

        def live_switch():
            """现场切语言 / 模式，验证不用重开窗口（留出重新布局的时间再截图）。"""
            _shot("shot_live_before")

            def to_en():
                w.settings["lang"] = "en"
                set_locale("en")
                apply_qt_translator(current_locale())
                w.retranslate()
                QTimer.singleShot(600, shot_en)

            def shot_en():
                _shot("shot_live_en")
                w.settings["lang"] = "ja"
                set_locale("ja")
                apply_qt_translator(current_locale())
                w.retranslate()
                w.on_set_ui("simple")       # 真切一次模式（含写 settings.json）
                QTimer.singleShot(600, shot_ja)

            def shot_ja():
                _shot("shot_live_ja_simple")
                names = ["btn_add", "btn_up", "btn_down", "btn_remove",
                         "btn_settings", "btn_refresh", "btn_export",
                         "btn_mode", "btn_more", "cmb_rhythm", "spin_total"]
                print("VISIBLE:", [(n, getattr(w, n).isVisible()) for n in names])
                print("MENU ACTIONS:", w._menu_labels(), flush=True)
                QTimer.singleShot(6000, shot_ready)

            def shot_ready():
                _shot("shot_live_simple_ready")
                print("AUTO PREVIEW STATUS:", w.lbl_status.text(),
                      "| stale:", w.preview_stale, flush=True)
                app.quit()

            QTimer.singleShot(400, to_en)

        if os.environ.get("PIC2VIDEO_SHOT_LIVE"):
            QTimer.singleShot(1200, live_switch)
        else:
            QTimer.singleShot(1200, step1)
    return app.exec()


LOG_NAME = "crash.log"
LOG_MAX = 1024 * 1024              # 超过就轮转成 crash.log.1，别无限长
_log_fh = None


def log_path() -> str:
    r"""日志放 %LOCALAPPDATA%\Pic2Video\：发布包里别多出一个日志文件。"""
    base = os.path.join(os.environ.get("LOCALAPPDATA")
                        or tempfile.gettempdir(), "Pic2Video")
    return os.path.join(base, LOG_NAME)


def log(msg: str) -> None:
    """写一行日志：时间 + 线程 + 内容。写日志本身出错一律吞掉，绝不再崩一次。"""
    fh = globals().get("_log_fh")
    if fh is None:
        return
    try:
        fh.write("%s [%s] %s\n" % (time.strftime("%H:%M:%S"),
                                   threading.current_thread().name, msg))
        fh.flush()
    except Exception:
        pass


def _log_traceback(kind: str, exc, tb) -> None:
    try:
        import traceback
        fh = globals().get("_log_fh")
        if fh is None:
            return
        fh.write("\n!!! uncaught %s\n" % kind)
        # 用 format_exception(type, value, tb) 这个老三参形式；
        # 3.12 的 print_exception(exc, tb) 会把 tb 当成 value，什么都不打。
        fh.write("".join(traceback.format_exception(
            type(exc) if exc is not None else Exception, exc, tb)))
        fh.flush()
    except Exception:
        pass


def _start_log() -> None:
    """全程日志 + 崩溃兜底。窗口版 exe 没有控制台，print 也落到这里。"""
    global _log_fh
    try:
        path = log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
        fh = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        return
    _log_fh = fh                         # 模块级引用，别让它被回收关掉
    try:
        if sys.stdout is None:
            sys.stdout = fh
        if sys.stderr is None:
            sys.stderr = fh
    except Exception:
        pass
    try:
        fh.write("\n=== %s  Pic2Video %s ===\n"
                 % (time.strftime("%Y-%m-%d %H:%M:%S"), APP_VERSION))
        fh.flush()
        log("frozen=%s argv=%r" % (bool(getattr(sys, "frozen", False)),
                                   sys.argv[1:]))
        log("python=%s platform=%s" % (sys.version.split()[0], sys.platform))
        log("cwd=%s" % os.getcwd())
        log("settings=%s" % SETTINGS_PATH)
    except Exception:
        pass
    try:
        # 注意是三个参数：类型 / 值 / traceback（原来写成两参，真崩时反而报
        # "Error in sys.excepthook" 把原始栈也吞了）
        sys.excepthook = lambda _t, exc, tb: _log_traceback("sys", exc, tb)
        threading.excepthook = lambda a: _log_traceback(
            "thread", a.exc_value, a.exc_traceback)
    except Exception:
        pass
    try:
        import faulthandler
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        pass
    try:
        from PyQt6.QtCore import qInstallMessageHandler

        def _qt_message(_mode, _ctx, text):
            log("[qt] %s" % text)

        qInstallMessageHandler(_qt_message)
    except Exception:
        pass


def main() -> int:
    _start_log()                       # 源码运行也记日志，出问题才有据可查
    argv = sys.argv[1:]
    if "--export" in argv:
        return cli_export(argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
