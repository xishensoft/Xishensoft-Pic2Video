# -*- coding: utf-8 -*-
"""pic2video —— 图片 + B站 BGM → 视频（单文件小工具）

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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import components
import theme
from audio_source import (AUDIO_EXTS, SourceError, fetch_audio, install_ytdlp,
                          kind_of, remove_ytdlp, ytdlp_ready, ytdlp_version)
from i18n import (LOCALES, LOCALE_LABELS, SYSTEM, bili_message,
                  current_locale, language_label, set_locale, tr)

components.prepend_path()      # tools/ 里的 ffmpeg 优先于系统那份

# exe 是窗口程序，没有控制台：起子进程时别让它继承无效的句柄
CREATION_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

APP_VERSION = "1.1.0"

WIDTH, HEIGHT, FPS = 1920, 1080, 30
PREVIEW_W, PREVIEW_H = 960, 540
MIN_DUR = 0.2
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v"}
FADE_IN, FADE_OUT = 0.5, 1.5
# 画面是 limited range（16–235）。不写清范围/色度，播放器只能猜：猜成 full 就会发灰、发暗。
# setparams 把标记写进帧属性，x264 才会把 VUI（color_range/colour_primaries…）写进 mp4。
COLOR_TAG = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"

FFMPEG = os.environ.get("PIC2VIDEO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("PIC2VIDEO_FFPROBE", "ffprobe")


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
PROJECT_VERSION = 1
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
MODE_LABELS = {"bgm": "跟 BGM", "material": "跟素材总长",
               "total": "自设总长", "free": "逐张秒数"}
MODE_BY_LABEL = {v: k for k, v in MODE_LABELS.items()}
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
SHORTCUT_LABELS = {"add": "加图片", "export": "导出视频", "undo": "撤回",
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
        "fade_in": 0.5,
        "fade_out": 1.5,
        "transition": "none",     # none / fade
        "transition_sec": 0.3,
        "export_dir": "",         # 空 = 图片所在目录
        "last_export_dir": "",
        "recent_projects": [],    # 最近打开的工程（最多 8 条，新的在前）
        "lang": SYSTEM,           # 界面语言：auto 跟随系统 / zh-CN / zh-TW / en / ja / ko
        "ui": "simple",           # 界面模式：simple 简洁（默认）/ pro 专业
        "theme": "auto",          # 外观：auto 跟随系统 / light / dark
        "accent": theme.DEFAULT_ACCENT,   # 主色（对撞色由它自动算）
        "motion": "none",         # 画面动效：none / in / out / inout
        "music_volume": 1.0,      # 背景音乐音量倍数（0..2）
        "music_start": 0.0,       # 背景音乐从第几秒开始用
        "music_len": 0.0,         # 音乐用多长；0 = 一直用到音乐结尾
        "crop_focus": "center",   # 裁切铺满时留中心 / 偏上 / 偏下
        "open_after_export": True,   # 导出完自动打开所在文件夹
        "ask_export_dir": True,      # 导出时先问保存到哪（关掉就直接用默认目录）
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
                    "intro_path", "outro_path"):
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
        if d.get("ui") not in ("simple", "pro"):
            d["ui"] = "simple"
        if d.get("theme") not in theme.THEMES:
            d["theme"] = "auto"
        if not is_hex_color(d.get("accent", "")):
            d["accent"] = theme.DEFAULT_ACCENT
        if not isinstance(d.get("open_after_export"), bool):
            d["open_after_export"] = True
        if not isinstance(d.get("ask_export_dir"), bool):
            d["ask_export_dir"] = True
        if d.get("motion") not in MOTIONS:
            d["motion"] = "none"
        if d.get("crop_focus") not in CROP_FOCUS:
            d["crop_focus"] = "center"
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

    def preview_size(self) -> tuple[int, int]:
        w, h = self.size()
        k = 960.0 / max(w, h)
        return even(w * k), even(h * k)

    def crf(self) -> int:
        return QUALITY[self.data["quality"]]


# ---------------------------------------------------------------- 时间线模型

class Clip:
    """时间线上的一个视频元素：图片、片头或片尾。

    kind: image / intro / outro
    fade: 这个元素自己开头淡入、结尾淡出（黑场）
    xfade: 跟下一个元素交叉淡化
    """

    __slots__ = ("path", "dur", "kind", "fade", "xfade")

    def __init__(self, path: str, dur: float, kind: str = "image",
                 fade: bool = False, xfade: bool = False):
        self.path = path
        self.dur = float(dur)
        self.kind = kind if kind in ("image", "intro", "outro") else "image"
        self.fade = bool(fade)
        self.xfade = bool(xfade)

    @property
    def is_video(self) -> bool:
        return self.kind in ("intro", "outro")

    def copy(self):
        return Clip(self.path, self.dur, self.kind, self.fade, self.xfade)

    def __repr__(self):
        return f"Clip({os.path.basename(self.path)!r}, {self.dur:.2f}, {self.kind})"


class AudioClip:
    """音频轨道上的一条：可以随时拖着换起始位置。

    gap=True 的（背景音乐）只在图片段出声，片头/片尾那几段会安静；
    别的音频从 start 开始按自己长度播。
    """

    __slots__ = ("path", "dur", "start", "volume", "gap")

    def __init__(self, path: str, dur: float, start: float = 0.0,
                 volume: float = 1.0, gap: bool = False):
        self.path = path
        self.dur = float(dur)
        self.start = max(0.0, float(start))
        self.volume = max(0.0, min(2.0, float(volume)))
        self.gap = bool(gap)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def __repr__(self):
        return f"AudioClip({self.name!r}, {self.dur:.2f}, @{self.start:.2f})"


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


class Project:
    """时间线模型：片头 + 图片序列 + 片尾，外加一条或多条音频。

    mode:
      bgm   跟 BGM：图片段总长 = 音乐长度，片头片尾各占自己的时长
      total 自设总长：整个成片（含片头片尾）= total_setting
      free  逐张秒数：图片段 = 各图之和
    固定总长时手改某张，其余按原比例让位，总长守恒（片头片尾不动）。
    """

    def __init__(self, fps: int = FPS):
        self.clips: list[Clip] = []
        self.audios: list[AudioClip] = []
        self.mode = "bgm"
        self.audio_start = 0.0        # 背景音乐从第几秒开始用
        self.audio_len = 0.0          # 用多长；0 = 到音乐结尾
        self.total_setting = 10.0
        self.fps = int(fps)

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
                       "fade": bool(c.fade), "xfade": bool(c.xfade)}
                      for c in self.clips],
            "audios": [{"path": a.path, "dur": a.dur, "start": a.start,
                        "volume": a.volume, "gap": bool(a.gap)}
                       for a in self.audios],
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
        new_clips = [self._clip_from_doc(raw) for raw in clips]
        new_audios = [self._audio_from_doc(raw) for raw in audios]
        mode = doc.get("mode")
        self.clips = new_clips
        self.audios = new_audios
        self.mode = mode if mode in MODE_LABELS else "bgm"
        self.audio_start = _doc_num(doc.get("audio_start"), 0.0)
        self.audio_len = _doc_num(doc.get("audio_len"), 0.0)
        self.total_setting = _doc_num(doc.get("total_setting"), 10.0)
        self.fps = 60 if _doc_num(doc.get("fps"), FPS) >= 50 else 30

    @staticmethod
    def _clip_from_doc(raw) -> Clip:
        if not isinstance(raw, dict):
            raise ProjectFileError("bad-shape", "素材条目不是对象")
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            raise ProjectFileError("bad-shape", "素材条目缺 path")
        kind = raw.get("kind", "image")
        if kind not in ("image", "intro", "outro"):
            raise ProjectFileError("bad-shape", "不认识的素材类型：%r" % (kind,))
        return Clip(path, max(0.0, _doc_num(raw.get("dur"), 0.0)), kind,
                    bool(raw.get("fade", False)),
                    bool(raw.get("xfade", False)))

    @staticmethod
    def _audio_from_doc(raw) -> AudioClip:
        if not isinstance(raw, dict):
            raise ProjectFileError("bad-shape", "音频条目不是对象")
        path = raw.get("path", "")
        if not isinstance(path, str):
            raise ProjectFileError("bad-shape", "音频条目的 path 不是文字")
        return AudioClip(path, max(0.0, _doc_num(raw.get("dur"), 0.0)),
                         _doc_num(raw.get("start"), 0.0),
                         _doc_num(raw.get("volume"), 1.0),
                         bool(raw.get("gap", False)))

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
                     + [a.path for a in self.audios]):
            if path and path not in out and not os.path.exists(path):
                out.append(path)
        return out

    # -- 片段分类 ------------------------------------------------------
    def images(self) -> list[Clip]:
        return [c for c in self.clips if not c.is_video]

    def image_count(self) -> int:
        return sum(1 for c in self.clips if not c.is_video)

    def intro(self) -> Clip | None:
        return next((c for c in self.clips if c.kind == "intro"), None)

    def outro(self) -> Clip | None:
        return next((c for c in self.clips if c.kind == "outro"), None)

    def fixed_span(self) -> float:
        """片头 + 片尾占掉的时间（图片段要减掉它）。"""
        return sum(c.dur for c in self.clips if c.is_video)

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
        return sum(c.dur for c in self.clips)

    # -- 归一化 --------------------------------------------------------
    def normalize(self):
        if self.mode == "material":
            self.apply_material()
            return
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

    def set_video_len(self, index: int, value: float):
        """改片头 / 片尾这类视频元素播多久：比原片短就截短，长就循环。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        if not clip.is_video:
            return
        clip.dur = max(MIN_DUR, float(value))
        t = self.target()
        imgs = self.images()
        if t is not None and imgs:
            room = max(MIN_DUR * len(imgs), t - self.fixed_span())
            self._fix_sum(room)

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

    def move(self, index: int, delta: int):
        j = index + delta
        if 0 <= index < len(self.clips) and 0 <= j < len(self.clips):
            self.clips[index], self.clips[j] = self.clips[j], self.clips[index]

    def reorder(self, paths_and_durs):
        self.clips = [Clip(p, d) for p, d in paths_and_durs]
        self.normalize()

    def move_to(self, index: int, pos: float):
        """把第 index 个视频元素挪到时间线上的 pos（秒）附近。"""
        if not (0 <= index < len(self.clips)):
            return
        clip = self.clips[index]
        others = [c for c in self.clips if c is not clip]
        acc, dest = 0.0, len(others)
        for k, c in enumerate(others):
            if pos < acc + c.dur / 2:
                dest = k
                break
            acc += c.dur
        self.clips.remove(clip)
        self.clips.insert(min(dest, len(self.clips)), clip)
        self.normalize()

    # -- 帧数 ----------------------------------------------------------
    def frame_counts(self) -> list[int]:
        """按累计时间取整，保证总帧数 = round(总时长 * FPS)，不漂移。"""
        self.apply_material()
        counts, acc, prev = [], 0.0, 0
        for c in self.clips:
            acc += c.dur
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


def _xfade_plan(clips, counts, fps: int, xfade_sec: float):
    """交叉淡化时每条边界两侧各补一半帧，输出总帧数守恒。

    返回 (counts2, overlaps)，overlaps[i] 是第 i 与 i+1 段的重叠帧数（偶数，0=硬切）。
    """
    counts2 = list(counts)
    if not clips or len(clips) < 2:
        return counts2, []
    overlaps = [0] * (len(clips) - 1)
    want = max(1, int(round(max(0.0, float(xfade_sec)) * fps / 2.0)))
    for i in range(len(clips) - 1):
        if not (clips[i].xfade or clips[i + 1].xfade):
            continue
        room = min(counts2[i], counts2[i + 1]) // 2
        half = min(want, room)
        if half < 1:
            continue
        overlaps[i] = 2 * half
        counts2[i] += half
        counts2[i + 1] += half
    return counts2, overlaps


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
                xfade_sec: float = 0.3) -> str:
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
            parts.append(
                f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase:"
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
            parts.append(f"{prev}[v{i}]xfade=transition=fade:duration={d:.3f}:"
                         f"offset={off:.3f}[m{i}]")
            acc += segs[i] - d
        else:
            parts.append(f"{prev}[v{i}]concat=n=2:v=1:a=0[m{i}]")
            acc += segs[i]
        prev = f"[m{i}]"
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

    if extras:
        # 主音频先落到一个标签上，才接得进 amix（否则把整条链塞进 input 列表就废了）
        main_label = "[bgmain]"
        parts.append(audio + main_label)
        mix = [main_label]
        for k, ex in enumerate(extras):
            ch = (f"[{ex['index']}:a]atrim=start=0:end={ex['dur']:.3f},"
                  f"asetpts=N/SR/TB")
            vol = float(ex.get("volume", 1.0))
            if abs(vol - 1.0) > 0.005:
                ch += f",volume={max(0.0, vol):.3f}"
            ms = int(round(max(0.0, float(ex["start"])) * 1000))
            ch += f",adelay={ms}|{ms}[ax{k}]"
            parts.append(ch)
            mix.append(f"[ax{k}]")
        parts.append("".join(mix) +
                     f"amix=inputs={len(mix)}:normalize=0:duration=longest,"
                     f"apad,atrim=0:{total:.3f}[aout]")
    else:
        parts.append(audio + "[aout]")
    return ";".join(parts)


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
                          "volume": a.volume, "gap": a.gap})
        else:  # 路径字符串
            items.append({"path": a, "start": 0.0,
                          "dur": probe_duration(a), "volume": 1.0, "gap": True})
    return items


def render(clips, counts, audio, out_path, *, w=WIDTH, h=HEIGHT,
           crf=20, preset="veryfast", fps=FPS, fit="own", fade_in=FADE_IN,
           fade_out=FADE_OUT, transition="none", transition_sec=0.3,
           bg_blur=True, pad_color="#000000", bg_path="", motion="none",
           volume=1.0, crop_focus="center", music_start=0.0, xfade_sec=0.3,
           on_progress=None, proc_holder=None) -> str:
    """一条 filtergraph 渲完；on_progress(0..1) 回调。

    audio 可以是路径、AudioClip 列表或等价的 dict 列表。
    proc_holder 给一个 dict 时，把 ffmpeg 进程塞进 proc_holder["proc"]，
    外面就能 terminate() 取消这次渲染。
    """
    if not counts:
        raise RenderError(tr("没有图片"))
    for c, nf in zip(clips, counts):
        if c.is_video and not os.path.exists(c.path):
            raise RenderError(tr("这张图片读不出来：{name}",
                                 name=os.path.basename(c.path)))
    bad = [c.path for c in clips if not c.is_video and not image_ok(c.path)]
    if bad:
        raise RenderError(tr("这张图片读不出来：{name}",
                             name=os.path.basename(bad[0])))
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

    graph = build_graph(counts, total, w, h, fps=fps, fit=fit,
                        fade_in=fade_in, fade_out=fade_out,
                        transition=transition, transition_sec=transition_sec,
                        bg_blur=bg_blur, pad_color=pad_color,
                        bg_index=(len(counts) if add_bg else None),
                        audio_index=len(counts) + (1 if add_bg else 0),
                        motion=motion, volume=volume,
                        crop_focus=crop_focus, music_start=music_start,
                        clips=clips, timeline=timeline,
                        extras=None, xfade_sec=xfade_sec)
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
                            "volume": ex.get("volume", 1.0)})
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
                            xfade_sec=xfade_sec)

    graph_file = None
    if len(graph) > 6000:
        fd, graph_file = tempfile.mkstemp(prefix="p2v_graph_", suffix=".txt")
        os.close(fd)
        with open(graph_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(graph)
        cmd += ["-/filter_complex", graph_file]
    else:
        cmd += ["-filter_complex", graph]

    cmd += ["-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-color_range", "tv",
            "-profile:v", "high", "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
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
        if graph_file:
            try:
                os.remove(graph_file)
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
    ap = argparse.ArgumentParser(description=tr("图片 + BGM 合成视频（无窗口）"))
    ap.add_argument("--export", required=True, help=tr("输出 mp4 路径"))
    ap.add_argument("--audio",
                    help=tr("本地音视频文件，或抖音 / YouTube 等视频链接"))
    ap.add_argument("--bili", help=tr("bilibili 链接或 BV 号（同 --audio）"))
    ap.add_argument("--total", type=float, help=tr("每张图片的秒数（默认均分音乐）"))
    ap.add_argument("--keep-audio", action="store_true",
                    help=tr("保留取下来的音频文件"))
    ap.add_argument("--fps", type=int, default=30, help=tr("帧率：30 或 60"))
    ap.add_argument("--fit", choices=list(FIT_MODES) + list(FIT_LEGACY),
                    default="own",
                    help=tr("铺底：own 原图 / color 纯色 / image 背景图 / video 背景视频 / cover 裁切"))
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
                    help=tr("图片转场：none 硬切 / fade 淡入淡出"))
    ap.add_argument("--motion", choices=list(MOTIONS), default="none",
                    help=tr("画面动效：none 静止 / in 缓慢推近 / out 缓慢拉远 / inout 交替"))
    ap.add_argument("--crop-focus", choices=list(CROP_FOCUS), default="center",
                    help=tr("裁切铺满时留住哪里：center 居中 / top 偏上 / bottom 偏下"))
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
    print(tr("[2/3] {n} 张图，总长 {total}s，{durs}",
             n=len(pr.clips), total=f"{pr.total():.2f}",
             durs=" + ".join(f"{c.dur:.2f}" for c in pr.clips)))
    out_dir = os.path.dirname(os.path.abspath(a.export)) or "."
    os.makedirs(out_dir, exist_ok=True)
    print(tr("[3/3] 渲染 {w}x{h} {fps}fps {fit}…",
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
    from PyQt6.QtCore import (QEvent, QEventLoop, QLibraryInfo, QPoint, QPointF,
                              QSize, Qt, QThread, QTimer, QTranslator, QUrl,
                              pyqtSignal)
    from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QIcon,
                            QImageReader, QKeySequence, QLinearGradient,
                            QPainter, QPainterPath, QPalette, QPen, QPixmap,
                            QPolygonF, QShortcut)
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtWidgets import (
        QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog,
        QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
        QFormLayout, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit,
        QListWidget,
        QListWidgetItem, QMainWindow, QMenu, QProgressBar,
        QPushButton, QRadioButton, QSizePolicy, QSpinBox,
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

    class NumberDialog(FramelessDialog):
        """要一个数字的输入框（改每张图显示多久用）。"""

        def __init__(self, parent, title: str, label: str, value: float,
                     minimum: float, maximum: float, decimals: int = 1):
            super().__init__(parent, title)
            lab = QLabel(label)
            self.body.addWidget(lab)
            self.spin = QDoubleSpinBox()
            self.spin.setRange(minimum, maximum)
            self.spin.setDecimals(decimals)
            self.spin.setSingleStep(0.5)
            self.spin.setSuffix(tr(" 秒"))
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
                       decimals: int = 1):
            dlg = cls(parent, title, label, value, minimum, maximum, decimals)
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
            try:
                path, _kind = fetch_audio(
                    self.url, os.path.join(self.out_dir, "bgm.m4a"),
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
            p.end()

    class Timeline(QWidget):
        """双轨时间线：上面视频元素，下面音频元素。

        能做的：点一下跳转、拖播放头擦洗、拖色块换位置、拖右边界改时长、
        Shift/Ctrl 多选、音频块滚轮调音量、右键弹出对应元素的菜单。
        """

        seeked = pyqtSignal(float)
        picked = pyqtSignal(int)
        resized = pyqtSignal(int, float)
        moved = pyqtSignal(int, float)
        audioMoved = pyqtSignal(int, float)
        audioVolume = pyqtSignal(int, float)
        menu = pyqtSignal(str, int, object)

        DEFAULT_COLORS = {"bg": "#1f2329", "empty": "#9ca3af",
                          "piece_a": "#b86b6b", "piece_b": "#b8a05f",
                          "piece_text": "#111827", "playhead": "#f59e0b",
                          "selected_edge": "#3b82f6", "audio": "#2f6f5e",
                          "audio_sel": "#3f9d84"}

        def __init__(self, project, parent=None):
            super().__init__(parent)
            self.project = project
            self.selected: list[int] = []
            self.sel_audio: list[int] = []
            self.play_pos = 0.0
            self._drag = None
            self._press = 0.0
            self.c = dict(self.DEFAULT_COLORS)
            self.setMinimumHeight(112)
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        # -- 几何 ------------------------------------------------------
        def _video_rect(self):
            h = self.height()
            return 4, max(30, int(h * 0.60))

        def _audio_rect(self):
            top = self._video_rect()[1] + 14
            return top, max(16, self.height() - top - 4)

        def _total(self) -> float:
            return sum(c.dur for c in self.project.clips) or 1.0

        def _spans(self):
            total = self._total()
            x, spans = 0.0, []
            for d in (c.dur for c in self.project.clips):
                w = self.width() * d / total
                spans.append((x, x + w, d))
                x += w
            return spans, total

        def _audio_spans(self):
            total = self._total()
            out = []
            for a in self.project.audios:
                x0 = self.width() * max(0.0, a.start) / total
                x1 = self.width() * min(total, a.start + a.dur) / total
                out.append((x0, max(x0 + 3, x1), a))
            return out

        def _audio_rows(self, count: int):
            """多条音频各自一行，能并排看到叠在一起的效果。"""
            top, h = self._audio_rect()
            n = max(1, count)
            row = max(10.0, h / n)
            return [(top + i * row, top + (i + 1) * row - 2) for i in range(n)]

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
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.fillRect(self.rect(), QColor(self.c["bg"]))
            spans, total = self._spans()
            if not spans:
                p.setPen(QColor(self.c["empty"]))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           tr("把图片拖进来，或点「加图片」"))
                return
            font = QFont(self.font())
            font.setPointSize(9)
            p.setFont(font)
            top, vh = self._video_rect()
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
                if x1 - x0 > 26:
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    name = {"intro": tr("片头"), "outro": tr("片尾")}.get(
                        clip.kind, f"{i + 1}")
                    marks = ""
                    if clip.fade:
                        marks += "◐"
                    if clip.xfade:
                        marks += "⇄"
                    p.drawText(int(x0) + 6, top + 15,
                               f"{name} · {d:.1f}s {marks}")
                if clip.xfade and i < len(spans) - 1:
                    crosses += 1
                    p.setPen(QPen(QColor(self.c["playhead"]), 1,
                                  Qt.PenStyle.DotLine))
                    p.drawLine(int(x1), top, int(x1), top + vh - top)

            # 音频轨
            aspans = self._audio_spans()
            rows = self._audio_rows(len(aspans))
            atrack_top, atrack_bottom = self._audio_rect()
            p.setPen(QColor(self.c["empty"]))
            p.drawText(4, atrack_top - 4, tr("音频"))
            for k, (x0, x1, a) in enumerate(aspans):
                ry0, ry1 = rows[k]
                col = QColor(self.c["audio_sel"] if k in self.sel_audio
                             else self.c["audio"])
                if a.gap:
                    col = QColor(self.c["piece_b"])
                p.fillRect(int(x0), int(ry0) + 1, max(2, int(x1 - x0)),
                           max(4, int(ry1 - ry0) - 1), col)
                if x1 - x0 > 34:
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    p.drawText(int(x0) + 5, int(ry0) + 12,
                               f"{os.path.basename(a.path)[:18]}  "
                               f"{int(round(a.volume * 100))}%")

            px = int(self.width() * min(1.0, self.play_pos / total))
            p.setPen(QPen(QColor(self.c["playhead"]), 2))
            p.drawLine(px, 0, px, self.height())

        # -- 命中 ------------------------------------------------------
        def _hit_video(self, x, y):
            _t, vh = self._video_rect()
            if y > vh + 2:
                return -1, -1.0
            spans, _ = self._spans()
            for i, (x0, x1, _d) in enumerate(spans):
                if x0 - 3 <= x <= x1 + 3:
                    return i, x1
            return -1, 0.0

        def _hit_audio(self, x, y):
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
                i, _ = self._hit_video(x, y)
                if i >= 0:
                    if i not in self.selected:
                        self.selected = [i]
                    self.picked.emit(i)
                    self.update()
                    self.menu.emit("video", i, ev.globalPosition().toPoint())
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

            i, edge_x = self._hit_video(x, y)
            if i >= 0:
                spans, total = self._spans()
                x0, x1, d = spans[i]
                clip = self.project.clips[i]
                if (not clip.is_video) and abs(x - x1) <= 5:
                    self._drag = ("resize", i, x, d)
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
                else:
                    self.selected = [i]
                self.picked.emit(i)
                start = sum(c.dur for c in self.project.clips[:i])
                self.seeked.emit(start)
                self.play_pos = start
                self._drag = ("clip", i, x, x0)
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
                else:
                    self.sel_audio = [k]
                self._drag = ("audio", k, x, a.start)
                self.update()
                return

            # 空白处：拖播放头
            self._drag = ("scrub", -1, x, 0.0)
            _spans, total = self._spans()
            self.play_pos = total * x / max(1, self.width())
            self.seeked.emit(self.play_pos)
            self.update()

        def mouseMoveEvent(self, ev):
            x, y = ev.position().x(), ev.position().y()
            if not self._drag:
                i, edge_x = self._hit_video(x, y)
                near = (i >= 0 and not self.project.clips[i].is_video
                        and abs(x - edge_x) <= 5)
                self.setCursor(Qt.CursorShape.SizeHorCursor if near
                               else Qt.CursorShape.PointingHandCursor)
                return
            kind = self._drag[0]
            if kind == "scrub":
                _spans, total = self._spans()
                self.play_pos = total * x / max(1, self.width())
                self.seeked.emit(self.play_pos)
                self.update()
                return
            if kind == "resize":
                _k, i, x0, d0 = self._drag
                _spans, total = self._spans()
                self.resized.emit(
                    i, d0 + (x - x0) * total / max(1, self.width()))
                return
            if kind == "audio":
                _k, idx, x0, start0 = self._drag
                _spans, total = self._spans()
                self.play_pos = total * x / max(1, self.width())
                self.update()
                return
            if kind == "clip":
                self.play_pos = self._time_at(x)
                self.update()

        def _time_at(self, x: float) -> float:
            total = self._total()
            return max(0.0, min(total, total * x / max(1, self.width())))

        def mouseReleaseEvent(self, ev):
            drag, self._drag = self._drag, None
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            if not drag:
                return
            kind = drag[0]
            x = ev.position().x()
            if kind == "clip":
                i = drag[1]
                if abs(x - drag[2]) > 6:
                    self.moved.emit(i, self._time_at(x))
                return
            if kind == "audio":
                _k, idx, x0, start0 = drag
                if abs(x - x0) <= 6:
                    return
                total = self._total()
                delta = (x - x0) * total / max(1, self.width())
                a = self.project.audios[idx]
                self.audioMoved.emit(idx, max(0.0, start0 + delta))
                return

        def wheelEvent(self, ev):
            k = self._hit_audio(ev.position().x(), ev.position().y())
            if k < 0:
                ev.ignore()
                return
            step = 0.05 if ev.angleDelta().y() > 0 else -0.05
            self.audioVolume.emit(k, step)
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

        def __init__(self, settings: Settings, parent=None):
            super().__init__(parent, tr("设置"))
            self.s = settings
            self.setMinimumWidth(620)
            self.pro = settings["ui"] != "simple"
            self._pro_fields = []   # 简洁模式要藏起来的（标签和控件一起藏）
            self._groups = {}       # 组名 -> QFormLayout
            self._order = []
            self._group = ""
            self._group_pro = {}

            def add_row(text, field, pro=False):
                form = self._form()
                form.addRow(tr(text), field)
                if pro:
                    lab = form.labelForField(field)
                    if lab is not None:
                        self._pro_fields.append(lab)
                    self._pro_fields.append(field)
                return field

            def add_sep(text, pro=False):
                """开始新的一组：专业模式下每组自成一页（Tab），简洁模式下用细线连成一列。"""
                form = QFormLayout()
                form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
                form.setFieldGrowthPolicy(
                    QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                form.setContentsMargins(2, 10, 2, 4)
                self._order.append(text)
                self._group_pro[text] = pro
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
                tr("留空 = 每张图所在目录（关掉「先问我」时就用这里）"))
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
            add_row("启动时窗口", self.cmb_win)
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
            add_row("固定大小", wrap_win)
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
            self._pro_fields.append(hint)
            row_size.addWidget(hint)
            row_size.addStretch(1)
            wrap_size = QWidget()
            wrap_size.setLayout(row_size)
            add_row("分辨率（宽×高）", wrap_size, pro=True)

            self.lbl_size = QLabel()
            add_row("成片尺寸", self.lbl_size)
            self._syncing = False

            self.cmb_fps = QComboBox()
            self.cmb_fps.addItems(["30", "60"])
            self.cmb_fps.setCurrentText(str(settings["fps"]))
            add_row("帧率", self.cmb_fps, pro=True)

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
            add_row("默认片头视频", wrap_intro)

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
            add_row("默认片尾视频", wrap_outro)

            self._color = settings["pad_color"]
            self.btn_color = QPushButton(self._color)
            self.btn_color.setToolTip(tr("纯色留白用的底色"))
            self.btn_color.clicked.connect(self._pick_color)
            add_row("留白颜色", self.btn_color, pro=True)
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
            add_row("背景文件", wrap_bg, pro=True)

            self.chk_bg_blur = QCheckBox(
                tr("背景模糊（原图铺底与自定义背景都适用）"))
            self.chk_bg_blur.setChecked(bool(settings["bg_blur"]))
            add_row("背景处理", self.chk_bg_blur, pro=True)

            self.cmb_crop = QComboBox()
            for key in CROP_FOCUS:
                self.cmb_crop.addItem(tr(CROP_LABELS[key]), key)
            self.cmb_crop.setCurrentIndex(
                max(0, list(CROP_FOCUS).index(settings["crop_focus"])))
            add_row("裁切位置", self.cmb_crop, pro=True)

            add_sep("音乐", pro=True)
            self.spin_mstart = QDoubleSpinBox()
            self.spin_mstart.setRange(0.0, 36000.0)
            self.spin_mstart.setDecimals(1)
            self.spin_mstart.setSingleStep(1.0)
            self.spin_mstart.setSuffix(tr(" 秒"))
            self.spin_mstart.setValue(float(settings["music_start"]))
            add_row("音乐起点", self.spin_mstart, pro=True)

            self.spin_mlen = QDoubleSpinBox()
            self.spin_mlen.setRange(0.0, 36000.0)
            self.spin_mlen.setDecimals(1)
            self.spin_mlen.setSingleStep(1.0)
            self.spin_mlen.setSuffix(tr(" 秒"))
            self.spin_mlen.setSpecialValueText(tr("整首"))
            self.spin_mlen.setValue(float(settings["music_len"]))
            add_row("音乐长度", self.spin_mlen, pro=True)

            self.spin_fin = QDoubleSpinBox()
            self.spin_fin.setRange(0.0, 10.0)
            self.spin_fin.setDecimals(1)
            self.spin_fin.setSingleStep(0.1)
            self.spin_fin.setSuffix(tr(" 秒"))
            self.spin_fin.setValue(float(settings["fade_in"]))
            add_row("音乐淡入", self.spin_fin, pro=True)

            self.spin_fout = QDoubleSpinBox()
            self.spin_fout.setRange(0.0, 30.0)
            self.spin_fout.setDecimals(1)
            self.spin_fout.setSingleStep(0.1)
            self.spin_fout.setSuffix(tr(" 秒"))
            self.spin_fout.setValue(float(settings["fade_out"]))
            add_row("音乐淡出", self.spin_fout, pro=True)

            self.spin_vol = QSpinBox()
            self.spin_vol.setRange(0, 200)
            self.spin_vol.setSingleStep(5)
            self.spin_vol.setSuffix("%")
            self.spin_vol.setValue(int(round(float(settings["music_volume"]) * 100)))
            add_row("音乐音量", self.spin_vol, pro=True)

            add_sep("节奏与动效", pro=True)
            self.cmb_mode = QComboBox()
            for k in ("bgm", "total", "free"):
                self.cmb_mode.addItem(mode_label(k), k)
            self.cmb_mode.setCurrentIndex(("bgm", "total", "free").index(settings["mode"]))
            add_row("默认节奏", self.cmb_mode, pro=True)

            self.spin_per_image = QDoubleSpinBox()
            self.spin_per_image.setRange(MIN_DUR, 3600.0)
            self.spin_per_image.setDecimals(1)
            self.spin_per_image.setSingleStep(0.5)
            self.spin_per_image.setSuffix(tr(" 秒"))
            self.spin_per_image.setValue(float(settings["per_image"]))
            add_row("逐张模式默认", self.spin_per_image, pro=True)

            self.cmb_motion = QComboBox()
            for key in MOTIONS:
                self.cmb_motion.addItem(tr(MOTION_LABELS[key]), key)
            self.cmb_motion.setCurrentIndex(
                max(0, MOTIONS.index(settings["motion"])))
            add_row("画面动效", self.cmb_motion, pro=True)

            self.cmb_trans = QComboBox()
            self.cmb_trans.addItem(tr("硬切"), "none")
            self.cmb_trans.addItem(tr("淡入淡出（每张首尾淡黑）"), "fade")
            self.cmb_trans.setCurrentIndex(1 if settings["transition"] == "fade" else 0)
            self.cmb_trans.currentIndexChanged.connect(self._sync_trans)
            add_row("图片转场", self.cmb_trans, pro=True)

            self.spin_trans = QDoubleSpinBox()
            self.spin_trans.setRange(0.0, 2.0)
            self.spin_trans.setDecimals(1)
            self.spin_trans.setSingleStep(0.1)
            self.spin_trans.setSuffix(tr(" 秒"))
            self.spin_trans.setValue(float(settings["transition_sec"]))
            add_row("转场时长", self.spin_trans, pro=True)

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
            if self.pro:                      # 专业模式：一组一页，省得竖着太长
                tabs = QTabWidget()
                for name in self._order:
                    page = QWidget()
                    page.setLayout(self._groups[name])
                    tabs.addTab(page, tr(name))
                outer.addWidget(tabs, 1)
            else:                             # 简洁模式：一列，只留必要项
                for w_ in self._pro_fields:
                    w_.setVisible(False)
                holder = QWidget()
                v = QVBoxLayout(holder)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(4)
                for name in self._order:
                    form = self._groups[name]
                    if self._group_pro.get(name):
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
                   else tr("图片 (*.jpg *.jpeg *.png *.bmp *.webp)"))
            picked = FileDialog.get_open_files(
                self, cap, self.edit_bg.text() or "", flt)
            if picked:
                self.edit_bg.setText(picked[0])

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
                transition_sec=float(self.spin_trans.value()),
                win_mode=self.cmb_win.currentData(),
                win_fixed_w=int(self.spin_win_w.value()),
                win_fixed_h=int(self.spin_win_h.value()),
                intro_path=self.edit_intro.text().strip(),
                outro_path=self.edit_outro.text().strip(),
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

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowFlags(self.windowFlags()
                                | Qt.WindowType.FramelessWindowHint)
            self.setWindowIcon(_win_icon(32))
            self.setWindowTitle(tr("图片出片 —— 图片 + B站 BGM → 视频"))
            self.setObjectName("frame")         # 配 QSS 那一圈细边
            self.resize(1180, 800)
            self.setAcceptDrops(True)
            self.settings = Settings()
            set_locale(self.settings["lang"])
            apply_qt_translator(current_locale())
            self.preview_name = ""
            self.project = Project(fps=int(self.settings["fps"]))
            self.project.mode = self.settings["mode"]
            self.tmpdir = tempfile.mkdtemp(prefix="pic2video_")
            self.preview_seq = 0
            self.preview_path = os.path.join(self.tmpdir, "preview_0.mp4")
            self._preview_pending = False   # 生成中途又点了一次刷新
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
            self.autosaver = AutoSaver(   # 崩溃/断电后救命的那份
                os.path.join(components.data_dir(), "自动保存.p2v"), 15.0)

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
                              ("Ctrl+Shift+S", self.on_save_project_as)):
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

        def _build_project_menu(self) -> QMenu:
            menu = QMenu(self)
            act = menu.addAction(tr("新建工程"))
            act.triggered.connect(self.on_new_project)
            act = menu.addAction(tr("打开工程…"))
            act.triggered.connect(self.on_open_project)
            self.menu_recent = menu.addMenu(tr("最近打开"))
            self.menu_recent.aboutToShow.connect(self._refresh_recent_menu)
            menu.addSeparator()
            act = menu.addAction(tr("保存工程"))
            act.triggered.connect(self.on_save_project)
            act = menu.addAction(tr("另存为…"))
            act.triggered.connect(self.on_save_project_as)
            return menu

        def _refresh_recent_menu(self):
            menu = getattr(self, "menu_recent", None)
            if menu is None:
                return
            menu.clear()
            recent = self.settings.recent()
            if not recent:
                act = menu.addAction(tr("（还没有）"))
                act.setEnabled(False)
                return
            for path in recent:
                act = menu.addAction(os.path.basename(path))
                act.setToolTip(path)
                act.triggered.connect(
                    lambda _checked=False, _p=path: self.on_open_recent(_p))

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
            MsgBox.warn(
                self, tr("有素材找不到了"),
                tr("下面这些素材不在了，成片里会缺画面：\n%s%s\n"
                   "把它们放回原来的位置，或者重新导入。") % (names, more))

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
                self, tr("图片出片 —— 图片 + B站 BGM → 视频"), maximize=True)
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
            def btn(text, slot, tip=""):
                b = QPushButton(text)
                b.clicked.connect(slot)
                if tip:
                    b.setToolTip(tip)
                bar.addWidget(b)
                return b
            self.btn_add = btn(tr("加图片"), self.on_add)
            self.btn_add.setMinimumWidth(78)
            self.btn_up = btn(tr("上移"), lambda: self.on_move(-1))
            self.btn_down = btn(tr("下移"), lambda: self.on_move(1))
            self.btn_remove = btn(tr("删除"), self.on_remove)
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
            self.radio_free = QRadioButton(tr("逐张秒数"))
            bar.addWidget(self.cmb_rhythm)
            group = QButtonGroup(self)
            for r in (self.radio_total, self.radio_free):
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
            self.btn_mode = QPushButton()      # 简洁 / 专业 开关
            self.btn_mode.setStyleSheet("QPushButton{padding:3px 10px}")
            self.btn_mode.clicked.connect(self.on_toggle_mode)
            bar.addWidget(self.btn_mode)
            # 简洁模式的设置入口：窗口右上角一个小「⋯」（专业模式用「设置」按钮）
            self.btn_more = QPushButton("⋯")
            self.btn_more.setFixedWidth(34)
            self.btn_more.setToolTip(tr("设置"))
            self.btn_more.clicked.connect(self.on_settings)
            bar.addWidget(self.btn_more)
            v.addLayout(bar)

            split = QSplitter(Qt.Orientation.Horizontal)
            self.list = ImageList()
            self.list.setIconSize(QSize(96, 54))
            self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.list.setMinimumWidth(300)
            self.list.setStyleSheet("QListWidget::item{padding:4px 2px;}")
            self.list.order_changed.connect(self.on_rows_moved)
            self.list.drop_files.connect(self.on_drop_into_list)
            self.list.itemDoubleClicked.connect(self.on_edit_dur)
            self.list.currentRowChanged.connect(self.on_row_changed)
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
            split.addWidget(self.left_tabs)

            right = QWidget()
            rv = QVBoxLayout(right)
            rv.setContentsMargins(0, 0, 0, 0)
            self.video = QVideoWidget()
            self.video.setMinimumSize(480, 270)
            pal = self.video.palette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#000000"))
            self.video.setPalette(pal)
            self.video.setAutoFillBackground(True)
            self.video.mousePressEvent = self.on_video_click  # type: ignore
            self.player.setVideoOutput(self.video)
            self.player.mediaStatusChanged.connect(self.on_media_status)
            self.player.positionChanged.connect(self.on_position)
            self.player.errorOccurred.connect(self.on_player_error)
            rv.addWidget(self.video, 1)
            self.lbl_preview = QLabel(tr("预览：改完参数点右上角「刷新预览」"))
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
            self.timeline.moved.connect(self.on_timeline_move)
            self.timeline.audioMoved.connect(self.on_audio_move)
            self.timeline.audioVolume.connect(self.on_audio_volume)
            self.timeline.menu.connect(self.on_timeline_menu)
            v.addWidget(self.timeline)

            foot = QHBoxLayout()
            self.lbl_status = QLabel(tr("拖图片进来开始"))
            foot.addWidget(self.lbl_status, 1)
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
            # 简洁模式没有「刷新预览」按钮：改动后自己抖一下再重渲预览
            self.auto_timer = QTimer(self)
            self.auto_timer.setSingleShot(True)
            self.auto_timer.setInterval(900)
            self.auto_timer.timeout.connect(self.on_preview)
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
            self.btn_add.setText(tr("加图片"))
            self.btn_up.setText(tr("上移"))
            self.btn_down.setText(tr("下移"))
            self.btn_remove.setText(tr("删除"))
            self.btn_undo_bar.setText(tr("撤回"))
            self.btn_undo_bar.setToolTip(tr("撤销上一步（Ctrl+Z）"))
            self.btn_redo_bar.setText(tr("还原"))
            self.btn_redo_bar.setToolTip(tr("重做（Ctrl+Shift+Z）"))
            self.btn_undo.setText(tr("撤回"))
            self.btn_redo.setText(tr("还原"))
            self.left_tabs.setTabText(0, tr("素材"))
            self.left_tabs.setTabText(1, tr("历史"))
            self.btn_settings.setText(tr("设置"))
            self.btn_settings.setToolTip(
                tr("导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏"))
            self.cmb_rhythm.setItemText(0, tr(MODE_LABELS["bgm"]))
            self.cmb_rhythm.setItemText(1, tr(MODE_LABELS["material"]))
            self.radio_total.setText(tr("自设总长"))
            self.radio_free.setText(tr("逐张秒数"))
            self.spin_total.setSuffix(tr(" 秒"))
            self.lbl_bgm.setText(tr("音乐链接"))
            self.edit_bgm.setPlaceholderText(
                tr("B 站 / 抖音 / YouTube / 快手等链接，或把本地音视频拖进来，自动取整条音轨"))
            self.btn_retry.setText(tr("重试"))
            self.btn_export.setText(tr("导出视频"))
            simple = self.settings["ui"] == "simple"
            self.btn_mode.setText(tr("简洁模式") if simple else tr("专业模式"))
            self.btn_mode.setToolTip(
                tr("点一下切换：简洁模式只留必要设置，专业模式显示全部选项"))
            self.btn_more.setToolTip(tr("设置"))
            if self.preview_name:
                self.lbl_preview.setText(
                    tr("预览：{name}", name=self.preview_name))
            else:
                self.lbl_preview.setText(
                    tr("预览：改完参数点右上角「刷新预览」"))
            self._apply_ui_mode()
            self._refresh_all()
            self._refresh_history()
            # 英文/日文文案更长：先让按钮/标签不许缩到文字以下，再按需把窗口拉宽
            self.ensurePolished()
            for w_ in (self.btn_add, self.btn_up, self.btn_down, self.btn_remove,
                       self.btn_undo_bar, self.btn_redo_bar,
                       self.btn_settings, self.btn_refresh, self.btn_export,
                       self.btn_mode, self.btn_retry, self.lbl_bgm,
                       self.btn_more, self.cmb_rhythm, self.radio_total,
                       self.radio_free):
                w_.setMinimumWidth(w_.sizeHint().width())
            need = self.minimumSizeHint().width()
            if need > self.width():
                self.resize(need, self.height())

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
            self.video.setStyleSheet("QVideoWidget{background:#000000;}")
            self.lbl_audio.setStyleSheet(f"color:{pal['muted']};")
            self.lbl_preview.setStyleSheet(f"color:{pal['subtext']};")
            self.lbl_status.setStyleSheet(f"color:{pal['text']};")

        def _on_system_theme(self, *_a):
            if self.settings["theme"] == "auto":
                self.apply_theme()

        def on_toggle_mode(self):
            """简洁 / 专业 开关：立即生效，并记住上次的选择。"""
            simple = self.settings["ui"] != "simple"
            self.settings.update(ui="simple" if simple else "pro")
            self.settings.save()
            self.retranslate()
            self.lbl_status.setText(
                tr("简洁模式") if simple else tr("专业模式"))

        def _apply_ui_mode(self):
            """简洁模式只留「加图片 / B站链接 / 导出视频」，
            排序删除改秒数走列表右键菜单，设置走右上角「⋯」。"""
            simple = self.settings["ui"] == "simple"
            for w_ in (self.btn_up, self.btn_down, self.btn_remove,
                       self.btn_undo_bar, self.btn_redo_bar,
                       self.btn_settings, self.cmb_rhythm, self.radio_total,
                       self.radio_free, self.spin_total, self.btn_refresh):
                w_.setVisible(not simple)
            self.btn_more.setVisible(simple)
            if simple:
                if self.project.clips:
                    self._schedule_auto_preview()
            else:
                self.auto_timer.stop()

        def _schedule_auto_preview(self):
            if self.settings["ui"] == "simple" and self.project.clips:
                self.auto_timer.start()

        def _menu_labels(self):
            """右键菜单会出现的四项（冒烟测试用来核对文案）。"""
            return [tr("上移"), tr("下移"), tr("删除"), tr("这张图显示多久")]

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
            act_dur = menu.addAction(tr("这张图显示多久"))
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
        def _refresh_all(self):
            self._refresh_list()
            self.timeline.update()
            self._mark_stale()
            m = self.project.mode
            self.cmb_rhythm.blockSignals(True)
            self.cmb_rhythm.setCurrentIndex(1 if m == "material" else 0)
            self.cmb_rhythm.blockSignals(False)
            self.radio_total.setChecked(m == "total")
            self.radio_free.setChecked(m == "free")
            self.spin_total.setEnabled(self.project.mode == "total")
            if self.project.mode == "total":
                self.spin_total.blockSignals(True)
                self.spin_total.setValue(self.project.total_setting)
                self.spin_total.blockSignals(False)
            n = self.project.image_count()
            src = "BGM" if self.project.audio_path else tr("无音乐")
            extra = ""
            if self.project.intro() or self.project.outro():
                extra = tr("（含片头片尾）")
            self.lbl_status.setText(
                tr("{n} 张图 · 总长 {total} · {src}", n=n,
                   total=human_time(self.project.total()), src=src) + extra)

        def _refresh_list(self):
            self._rebuilding = True
            cur = self.list.currentRow()
            self.list.clear()
            for i, c in enumerate(self.project.clips):
                it = QListWidgetItem(self._thumb(c.path, c.kind), "")
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setData(Qt.ItemDataRole.UserRole + 1, c.dur)
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
                head = {"intro": tr("片头"),
                        "outro": tr("片尾")}.get(c.kind, f"{i + 1}.")
                it.setText(tr("{i}.  {dur} 秒   {name}", i=head,
                              dur=f"{c.dur:.1f}",
                              name=os.path.basename(c.path)))

        def _thumb(self, path, kind="image"):
            if kind in ("intro", "outro"):
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
            self.preview_stale = True
            self.btn_refresh.setText(tr("刷新预览 ●"))
            self._schedule_auto_preview()

        def _clean_stale(self):
            self.preview_stale = False
            self.btn_refresh.setText(tr("刷新预览"))

        # ---------------- 设置
        def on_settings(self):
            log("settings open")
            dlg = SettingsDialog(self.settings, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.settings.save()
            log("settings saved lang=%s ui=%s theme=%s ratio=%s size=%s" % (
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
                tr("参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果",
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
            w, h = s.preview_size() if preview else s.size()
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
                        crop_focus=s["crop_focus"])

        # ---------------- 动作
        def on_add(self):
            paths = FileDialog.get_open_files(
                self, tr("选图片"), "",
                tr("图片 (*.jpg *.jpeg *.png *.bmp *.webp)"), multi=True)
            if paths:
                self.add_images(paths)

        def add_images(self, paths):
            log("add_images n=%d first=%s" % (
                len(paths), os.path.basename(paths[0]) if paths else ""))
            good = []
            for i, p in enumerate(paths, 1):
                rd = QImageReader(p)
                if rd.canRead() and rd.size().width() > 0:
                    good.append(p)
                else:
                    self.lbl_status.setText(
                        tr("{name} 读不了，已跳过", name=os.path.basename(p)))
            if good:
                dur = (self.settings["per_image"]
                       if self.project.mode == "free" else None)
                self.project.add_images(
                    good, dur=dur,
                    intro=self.settings["intro_path"],
                    outro=self.settings["outro_path"])
                self.radio_total.setChecked(self.project.mode == "total")
                self._refresh_all()
                self.lbl_status.setText(tr("已加 {n} 张图", n=len(good)))
                self._history_push(tr("加图片"))

        def on_drop_into_list(self, paths, row, zone):
            """从外面拖到素材列表上：左 / 中 / 右三段分别是插前 / 替换 / 插后。"""
            paths = expand_paths(paths)
            good = []
            for p in paths:
                rd = QImageReader(p)
                if os.path.isdir(p):
                    continue
                if rd.canRead() and rd.size().width() > 0:
                    good.append(p)
                else:
                    self.lbl_status.setText(
                        tr("{name} 读不了，已跳过", name=os.path.basename(p)))
            if not good:
                return
            good.sort(key=lambda p: os.path.basename(p).lower())
            dur = (self.settings["per_image"]
                   if self.project.mode == "free" else None)
            if (zone == "replace" and 0 <= row < len(self.project.clips)
                    and not self.project.clips[row].is_video):
                self.project.replace_image(row, good[0])
                if len(good) > 1:
                    self.project.insert_images(row + 1, good[1:], dur)
                label = tr("替换素材")
                self.lbl_status.setText(tr("已换成 {n} 张图", n=len(good)))
            else:
                if row < 0:
                    at = len(self.project.clips)
                elif zone == "before":
                    at = row
                else:
                    at = row + 1
                self.project.insert_images(at, good, dur)
                label = tr("加图片")
                self.lbl_status.setText(
                    tr("已插入 {n} 张图", n=len(good)))
            self._refresh_all()
            self._history_push(label)

        def on_move(self, delta):
            row = self.list.currentRow()
            if row < 0:
                return
            self.project.move(row, delta)
            self._refresh_all()
            self._history_push(tr("调整顺序"))
            self.list.setCurrentRow(max(0, min(row + delta, self.list.count() - 1)))

        def on_remove(self):
            row = self.list.currentRow()
            if row < 0:
                return
            log("remove image row=%d" % row)
            self.project.remove([row])
            self._refresh_all()
            self._history_push(tr("删除素材"))

        def on_edit_dur(self, item):
            row = self.list.row(item)
            if not (0 <= row < len(self.project.clips)):
                return
            cur = self.project.clips[row].dur
            val, ok = NumberDialog.get_double(
                self, tr("这张图显示多久"),
                tr("第 {row} 张（秒）：", row=row + 1), cur, MIN_DUR, 3600, 1)
            if ok:
                log("duration row=%d %.2f -> %.2f" % (row, cur, val))
                self.project.set_duration(row, val)
                self._refresh_all()
                self._history_push(tr("改时长"))

        def on_rows_moved(self, *_a):
            if self._rebuilding:
                return
            pairs = []
            for i in range(self.list.count()):
                it = self.list.item(i)
                pairs.append((it.data(Qt.ItemDataRole.UserRole),
                              float(it.data(Qt.ItemDataRole.UserRole + 1))))
            self.project.reorder(pairs)
            self._refresh_all()
            self._history_push(tr("调整顺序"))

        def on_row_changed(self, row):
            if row < 0:
                self.timeline.set_selection([])
            else:
                rows = [i.row() for i in self.list.selectedItems()]
                self.timeline.set_selection(rows or [row])

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
            if 0 <= i < self.list.count():
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
            self.timeline.set_selection(self.timeline.selected or [i])

        def on_timeline_resize(self, i, value):
            log("timeline resize i=%d -> %.2f" % (i, value))
            self.project.set_duration(i, value)
            self._update_titles()
            self.timeline.update()
            self._mark_stale()
            self._history_push(tr("改时长"))
            self.lbl_status.setText(
                tr("{n} 张图 · 总长 {total}", n=self.project.image_count(),
                   total=human_time(self.project.total())))

        def on_timeline_move(self, i, pos):
            log("timeline move i=%d -> %d" % (i, pos))
            self.project.move_to(i, pos)
            self._refresh_all()
            self._history_push(tr("调整顺序"))
            if 0 <= i < self.list.count():
                self.list.setCurrentRow(i)

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

        def on_remove_audio(self, k):
            if 0 <= k < len(self.project.audios):
                log("remove audio k=%d" % k)
                self.project.audios.pop(k)
                self.timeline.sel_audio = []
                self._refresh_all()
                self._mark_stale()
                self._history_push(tr("删音频"))

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

        def on_timeline_menu(self, kind, index, pos):
            m = self.timeline_menu(kind, index)
            if m is not None:
                m.exec(pos)

        def timeline_menu(self, kind, index):
            rows = list(self.timeline.selected)
            arows = list(self.timeline.sel_audio)
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
                if len(targets) >= 2:
                    xfs = [self.project.clips[i].xfade for i in targets
                           if 0 <= i < len(self.project.clips)]
                    xact = m.addAction(tr("交叉淡化（与相邻元素）"))
                    xact.setCheckable(True)
                    xact.setChecked(bool(xfs) and all(xfs))
                    xact.triggered.connect(
                        lambda _c=False, t=targets: self.on_toggle_xfade(t))
                if (len(targets) == 1 and len(arows) == 1
                        and 0 <= targets[0] < len(self.project.clips)):
                    fill = m.addAction(tr("画面铺满这条音频"))
                    fill.triggered.connect(
                        lambda _c=False, t=targets, a=arows:
                        self.on_fill_audio(t[0], a[0]))
                m.addSeparator()
                dur = m.addAction(tr("这张显示多久…"))
                dur.setEnabled(len(targets) == 1
                               and not self.project.clips[targets[0]].is_video)
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
                m.addSeparator()
                add = m.addAction(tr("添加音频文件…"))
                add.triggered.connect(lambda _c=False: self.on_add_audio())
                extra = [k for k in targets if 0 <= k < len(self.project.audios)
                         and not self.project.audios[k].gap]
                if extra:
                    rm = m.addAction(tr("删除这条音频"))
                    rm.triggered.connect(
                        lambda _c=False, k=extra[0]: self.on_remove_audio(k))
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

        def on_toggle_xfade(self, rows):
            rows = [i for i in rows if 0 <= i < len(self.project.clips)]
            if not rows:
                return
            want = not all(self.project.clips[i].xfade for i in rows)
            for i in rows:
                self.project.clips[i].xfade = want
            self._refresh_all()
            self._history_push(tr("淡入淡出"))

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

        # ---------------- 预览
        def seek(self, seconds):
            self.player.setPosition(int(seconds * 1000))
            self.timeline.play_pos = seconds
            self.timeline.update()

        def on_video_click(self, ev):
            self.toggle_play()

        def toggle_play(self):
            if self.player.source().isEmpty():
                self.on_preview()
                return
            if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

        def on_media_status(self, status):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                self.player.setPosition(0)
                self.player.play()

        def on_position(self, ms):
            self.timeline.play_pos = ms / 1000.0
            self.timeline.update()

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
            self._preview_pending = False
            self.lbl_status.setText(tr("已取消"))

        def on_preview(self):
            if not self.project.clips:
                self.lbl_status.setText(tr("先加图片"))
                return
            if self.worker is not None and self.worker.isRunning():
                if self._render_kind == "preview":
                    self._preview_pending = True   # 跑完自动再来一次
                return
            self._follow_audio()
            # 先把播放器松手：Windows 上它握着旧文件时 ffmpeg 写不进去，
            # 正是「预览有时候显示不出来」的一个来源。
            try:
                self.player.stop()
                self.player.setSource(QUrl())
            except Exception:                     # noqa: BLE001
                pass
            self.preview_seq += 1
            old_preview = self.preview_path
            self.preview_path = os.path.join(
                self.tmpdir, f"preview_{self.preview_seq}.mp4")
            if old_preview != self.preview_path and os.path.exists(old_preview):
                QTimer.singleShot(1500, lambda p=old_preview: _unlink_quiet(p))
            log("preview start seq=%d clips=%d" % (self.preview_seq,
                                                   len(self.project.clips)))
            self._rendering(True, "preview")
            self.lbl_status.setText(tr("生成预览…"))
            self.worker = RenderWorker(
                list(self.project.clips), self.project.frame_counts(),
                list(self.project.audios), self.preview_path,
                opts=self._opts(preview=True), preview=True, parent=self)
            self.worker.progress.connect(self._on_render_progress)
            self.worker.ok.connect(self.on_preview_ok)
            self.worker.bad.connect(self.on_preview_bad)
            self.worker.stopped.connect(self.on_render_stopped)
            self.worker.start()

        def on_preview_ok(self, path):
            log("preview ok %s" % path)
            self._rendering(False)
            self._clean_stale()
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            if self._preview_pending:              # 期间又改过东西
                self._preview_pending = False
                QTimer.singleShot(200, self.on_preview)
            self.lbl_status.setText(
                tr("预览已更新（点画面播放/暂停，点色带跳转）"))
            self.preview_name = os.path.basename(path)
            self.lbl_preview.setText(tr("预览：{name}", name=self.preview_name))

        def on_preview_bad(self, msg):
            log("preview failed: %s" % msg)
            self._rendering(False)
            self._preview_pending = False
            self.lbl_status.setText(tr("预览失败"))
            if os.environ.get("PIC2VIDEO_SHOT"):
                print("PREVIEW_FAILED:", msg)
                return
            MsgBox.warn(self, tr("预览失败"), msg)

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
            self.fetch_worker = FetchWorker(url, self.tmpdir, self)
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
                tr("BGM 已就绪：{total} · {name}",
                   total=human_time(dur), name=os.path.basename(path)))
            self._history_push(tr("换背景音乐"))

        def on_fetch_bad(self, msg):
            log("fetch failed: %s" % msg)
            self.btn_retry.setEnabled(True)
            self.lbl_status.setText(tr("取音轨失败"))
            MsgBox.warn(self, tr("取音轨失败"), msg)

        # ---------------- 导出
        def on_export(self):
            if not self.project.clips:
                self.lbl_status.setText(tr("先加图片"))
                return
            if self.worker is not None and self.worker.isRunning():
                return
            first_dir = os.path.dirname(self.project.clips[0].path) or os.getcwd()
            base_dir = (self.settings["export_dir"]
                        or self.settings["last_export_dir"] or first_dir)
            if not os.path.isdir(base_dir):
                base_dir = first_dir
            stem = "slideshow_" + time.strftime("%Y%m%d_%H%M")
            shot = bool(os.environ.get("PIC2VIDEO_SHOT"))   # 自动化测试绝不弹窗
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
            self.settings.save()
            log("export start %s opts=%r" % (out, self._opts()))
            if shot:
                print("EXPORT_START:", out, flush=True)
            self._rendering(True, "export")
            self.lbl_status.setText(tr("正在导出…"))
            self.worker = RenderWorker(
                list(self.project.clips), self.project.frame_counts(),
                list(self.project.audios), out, opts=self._opts(),
                preview=False, parent=self)
            self.worker.progress.connect(self._on_render_progress)
            self.worker.ok.connect(self.on_export_ok)
            self.worker.bad.connect(self.on_export_bad)
            self.worker.stopped.connect(self.on_render_stopped)
            self.worker.start()

        def on_export_ok(self, path):
            log("export ok %s" % path)
            self._rendering(False)
            self.lbl_status.setText(tr("导出完成：{path}", path=path))
            if os.environ.get("PIC2VIDEO_SHOT"):
                print("EXPORT_OK:", path, flush=True)
                return
            if self.settings["open_after_export"]:
                try:
                    subprocess.Popen(["explorer", "/select,",
                                      os.path.normpath(path)])
                except OSError:
                    pass
                return
            picked = MsgBox.choose(
                self, tr("导出完成"),
                tr("视频已生成：\n{path}", path=path),
                [(tr("打开所在文件夹"), "plain"), (tr("好"), "primary")])
            if picked == 0:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

        def on_export_bad(self, msg):
            log("export failed: %s" % msg)
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
            imgs = [p for p in paths if os.path.splitext(p)[1].lower() in IMG_EXTS]
            audio = [p for p in paths
                     if os.path.splitext(p)[1].lower() in AUDIO_EXTS]
            media = [p for p in paths
                     if os.path.splitext(p)[1].lower() in MEDIA_EXTS]
            if imgs:
                self.add_images(sorted(imgs, key=lambda p: os.path.basename(p).lower()))
            # 拖进来的音频 / 视频：还没有 BGM 就当 BGM（视频会抽音轨），
            # 已经有 BGM 就叠一条额外音频
            if audio or media:
                if self.project.audio_path:
                    for p in audio:
                        self.add_audio_extra(p)
                    if media:
                        self.edit_bgm.setText(media[0])
                        self.start_fetch()
                else:
                    self.edit_bgm.setText((audio or media)[0])
                    self.start_fetch()
            if imgs or audio or media:
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
            if self.worker is not None and self.worker.isRunning():
                self.worker.cancel()
            for w in (self.worker, self.fetch_worker):
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
            if menu_probe == "video" and idx:
                w.timeline.set_selection(idx[:2] if len(idx) >= 2 else idx[:1])
                menu = w.timeline_menu("video", idx[0])
                name = "shot_menu_video"
            else:
                w.timeline.set_audio_selection([0])
                menu = w.timeline_menu("audio", 0)
                name = "shot_menu_audio"
            menu.popup(w.mapToGlobal(w.rect().center()))

            def grab_menu():
                menu.grab().save(os.path.join(outdir, name + tag + ".png"))
                menu.close()
                print("MENU", name, [a.text() for a in menu.actions()],
                      flush=True)
                app.quit()

            QTimer.singleShot(400, grab_menu)
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
            dlg = SettingsDialog(w.settings, w)
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
                    nd = NumberDialog(w, tr("这张图显示多久"), tr("秒数"), 3.0,
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
            return app.exec()

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
            dlg = SettingsDialog(w.settings, w)
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
                w.btn_mode.click()          # 真按一次模式开关（含写 settings.json）
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
