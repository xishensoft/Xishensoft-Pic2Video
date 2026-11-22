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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import theme
from audio_source import (AUDIO_EXTS, SourceError, fetch_audio, install_ytdlp,
                          kind_of, remove_ytdlp, ytdlp_ready, ytdlp_version)
from i18n import (LOCALES, LOCALE_LABELS, SYSTEM, bili_message,
                  current_locale, language_label, set_locale, tr)

WIDTH, HEIGHT, FPS = 1920, 1080, 30
PREVIEW_W, PREVIEW_H = 960, 540
MIN_DUR = 0.2
IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MEDIA_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".flv", ".m4v"}
FADE_IN, FADE_OUT = 0.5, 1.5

FFMPEG = os.environ.get("PIC2VIDEO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("PIC2VIDEO_FFPROBE", "ffprobe")


# ---------------------------------------------------------------- 基础工具

class RenderError(RuntimeError):
    pass


def missing_tools() -> list[str]:
    """ffmpeg / ffprobe 缺哪个（空列表＝都找得到）。"""
    miss = []
    for name, exe in (("ffmpeg", FFMPEG), ("ffprobe", FFPROBE)):
        if not (os.path.isfile(exe) or shutil.which(exe)):
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
                           capture_output=True, text=True, timeout=20)
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
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return float((p.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def extract_audio(src: str, dst: str) -> str:
    """从本地音视频里抽一条 AAC 音轨（拖本地视频当 BGM 用）。"""
    folder = os.path.dirname(os.path.abspath(dst)) or "."
    os.makedirs(folder, exist_ok=True)
    p = subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-i", src, "-vn", "-c:a", "aac",
         "-b:a", "192k", "-ar", "48000", "-ac", "2", dst],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
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

SETTINGS_PATH = os.path.join(HERE, "settings.json")

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
MODE_LABELS = {"bgm": "跟 BGM", "total": "自设总长", "free": "逐张秒数"}
MODE_BY_LABEL = {v: k for k, v in MODE_LABELS.items()}
MOTIONS = ("none", "in", "out", "inout")          # 画面动效
MOTION_LABELS = {"none": "静止", "in": "缓慢推近",
                 "out": "缓慢拉远", "inout": "交替推拉"}
MOTION_AMOUNT = 0.06                              # 推拉幅度：放大 6%
CROP_FOCUS = {"center": 0.5, "top": 0.0, "bottom": 1.0}   # 裁切铺满时留住哪一段
CROP_LABELS = {"center": "居中", "top": "偏上", "bottom": "偏下"}


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
        "lang": SYSTEM,           # 界面语言：auto 跟随系统 / zh-CN / zh-TW / en / ja / ko
        "ui": "simple",           # 界面模式：simple 简洁（默认）/ pro 专业
        "theme": "auto",          # 外观：auto 跟随系统 / light / dark
        "accent": theme.DEFAULT_ACCENT,   # 主色（对撞色由它自动算）
        "motion": "none",         # 画面动效：none / in / out / inout
        "music_volume": 1.0,      # 背景音乐音量倍数（0..2）
        "crop_focus": "center",   # 裁切铺满时留中心 / 偏上 / 偏下
        "open_after_export": True,  # 导出完自动打开所在文件夹
        "win_w": 0,               # 窗口大小/位置，0 = 用默认
        "win_h": 0,
        "win_x": -1,
        "win_y": -1,
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
                            ("per_image", MIN_DUR, 3600.0),
                            ("fade_in", 0.0, 10.0),
                            ("fade_out", 0.0, 30.0),
                            ("transition_sec", 0.0, 5.0)):
            try:
                d[key] = float(min(hi, max(lo, float(d.get(key, self.DEFAULTS[key])))))
            except (TypeError, ValueError):
                d[key] = self.DEFAULTS[key]
        for key in ("export_dir", "last_export_dir", "bg_path"):
            if not isinstance(d.get(key), str):
                d[key] = ""
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
        if d.get("motion") not in MOTIONS:
            d["motion"] = "none"
        if d.get("crop_focus") not in CROP_FOCUS:
            d["crop_focus"] = "center"
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
    __slots__ = ("path", "dur")

    def __init__(self, path: str, dur: float):
        self.path = path
        self.dur = float(dur)

    def __repr__(self):
        return f"Clip({os.path.basename(self.path)!r}, {self.dur:.2f})"


class Project:
    """图片序列 + 时长模式。

    mode:
      bgm   跟 BGM：总长 = 音乐时长，各图按比例均摊（默认）
      total 自设总长：总长 = total_setting
      free  逐张秒数：总长 = 各图之和
    固定总长时手改某张，其余按原比例让位，总长守恒。
    """

    def __init__(self, fps: int = FPS):
        self.clips: list[Clip] = []
        self.mode = "bgm"
        self.audio_path: str | None = None
        self.audio_dur = 0.0
        self.total_setting = 10.0
        self.fps = int(fps)

    # -- 目标总长 ------------------------------------------------------
    def target(self) -> float | None:
        n = len(self.clips)
        if n == 0:
            return None
        if self.mode == "bgm" and self.audio_dur > 0:
            return self.audio_dur
        if self.mode == "total":
            return max(self.total_setting, MIN_DUR * n)
        return None

    def total(self) -> float:
        return sum(c.dur for c in self.clips)

    # -- 归一化 --------------------------------------------------------
    def normalize(self):
        t = self.target()
        if t is None or not self.clips:
            return
        durs = [c.dur for c in self.clips]
        total = sum(durs)
        if total <= 0:
            durs = [t / len(durs)] * len(durs)
        else:
            durs = [max(MIN_DUR, d * t / total) for d in durs]
        self.clips = [Clip(c.path, d) for c, d in zip(self.clips, durs)]
        self._fix_sum(t)

    def _fix_sum(self, t: float):
        if not self.clips:
            return
        diff = t - sum(c.dur for c in self.clips)
        if abs(diff) < 1e-9:
            return
        i = max(range(len(self.clips)), key=lambda k: self.clips[k].dur)
        self.clips[i].dur = max(MIN_DUR, self.clips[i].dur + diff)

    def set_duration(self, index: int, value: float):
        if not (0 <= index < len(self.clips)):
            return
        t = self.target()
        if t is None:
            self.clips[index].dur = max(MIN_DUR, value)
            return
        n = len(self.clips)
        value = min(max(value, MIN_DUR), max(MIN_DUR, t - MIN_DUR * (n - 1)))
        durs = [c.dur for c in self.clips]
        durs[index] = value
        self._fit_others(durs, index, t)
        self.clips = [Clip(c.path, d) for c, d in zip(self.clips, durs)]
        self._fix_sum(t)

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
    def add_images(self, paths, dur: float | None = None):
        if not paths:
            return
        t = self.target()
        base = dur if dur is not None else ((t / (len(self.clips) + len(paths))) if t else 3.0)
        for p in paths:
            self.clips.append(Clip(p, max(MIN_DUR, base)))
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

    # -- 帧数 ----------------------------------------------------------
    def frame_counts(self) -> list[int]:
        """按累计时间取整，保证总帧数 = round(总时长 * FPS)，不漂移。"""
        counts, acc, prev = [], 0.0, 0
        for c in self.clips:
            acc += c.dur
            boundary = int(round(acc * self.fps))
            n = max(1, boundary - prev)
            counts.append(n)
            prev += n
        return counts


def set_mode(project: Project, mode: str) -> None:
    project.mode = mode
    if mode == "total":
        project.total_setting = max(MIN_DUR * max(1, len(project.clips)), project.total())
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


def build_graph(counts: list[int], total: float, w: int = WIDTH, h: int = HEIGHT,
                *, fps: int = FPS, fit: str = "own", fade_in: float = FADE_IN,
                fade_out: float = FADE_OUT, transition: str = "none",
                transition_sec: float = 0.3, bg_blur: bool = True,
                pad_color: str = "#000000", bg_index: int | None = None,
                audio_index: int | None = None, motion: str = "none",
                motion_amount: float = MOTION_AMOUNT, volume: float = 1.0,
                crop_focus: str = "center") -> str:
    """一条 filtergraph：每图按帧数切片 → 拼接 → 音频裁到总长并淡入淡出。

    fit: own 原图铺底（bg_blur 决定模糊不模糊）/ color 纯色留白（pad_color）
         / image·video 自定义背景（输入下标 bg_index）+ 原图完整居中
         / cover 裁切铺满
    transition: none 硬切；fade 每张首尾淡黑
    motion: none 静帧；in 缓慢推近；out 缓慢拉远；inout 一张推近一张拉远
    volume: 背景音乐音量倍数（1.0 = 原样）
    crop_focus: 裁切铺满时留住 center / top / bottom
    """
    K = len(counts)
    if audio_index is None:
        audio_index = K
    pad_hex = "0x" + pad_color.lstrip("#")
    segs = [nf / fps for nf in counts]
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

    for i, nf in enumerate(counts):
        seg = segs[i]
        if fit == "cover":
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
        if motion in MOTIONS and motion != "none" and nf > 1:
            chain = _motion_chain(chain, motion, i, nf, w, h, fps,
                                  motion_amount)
        td = min(max(0.0, float(transition_sec)), seg / 2.0)
        if transition == "fade" and td > 0.02 and seg > 0.1:
            chain += (f",fade=t=in:st=0:d={td:.3f}"
                      f",fade=t=out:st={max(0.0, seg - td):.3f}:d={td:.3f}")
        parts.append(chain + f"[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(K))
    parts.append(f"{labels}concat=n={K}:v=1:a=0[vout]")
    audio = f"[{audio_index}:a]atrim=0:{total:.3f},asetpts=N/SR/TB"
    if abs(volume - 1.0) > 0.005:
        audio += f",volume={max(0.0, float(volume)):.3f}"
    if fade_in > 0:
        audio += f",afade=t=in:st=0:d={fade_in:.3f}"
    fade = min(max(0.0, fade_out), max(0.0, total - 0.05))
    if fade > 0.05:
        audio += f",afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}"
    parts.append(audio + "[aout]")
    return ";".join(parts)


def render(clips, counts, audio_path, out_path, *, w=WIDTH, h=HEIGHT,
           crf=20, preset="veryfast", fps=FPS, fit="own", fade_in=FADE_IN,
           fade_out=FADE_OUT, transition="none", transition_sec=0.3,
           bg_blur=True, pad_color="#000000", bg_path="", motion="none",
           volume=1.0, crop_focus="center", on_progress=None,
           proc_holder=None) -> str:
    """一条 filtergraph 渲完；on_progress(0..1) 回调。

    proc_holder 给一个 dict 时，把 ffmpeg 进程塞进 proc_holder["proc"]，
    外面就能 terminate() 取消这次渲染。
    """
    if not counts:
        raise RenderError(tr("没有图片"))
    bad = [c.path for c in clips if not image_ok(c.path)]
    if bad:
        raise RenderError(tr("这张图片读不出来：{name}", name=os.path.basename(bad[0])))
    total = sum(counts) / fps
    add_bg = fit in BG_MODES
    if add_bg:
        if not bg_path or not os.path.exists(bg_path):
            raise RenderError(tr("选的背景文件不存在，去设置里重新选一个"))
        if fit == "image" and not bg_path.lower().endswith(tuple(IMG_EXTS)):
            raise RenderError(tr("背景图格式不支持，用 jpg / png / webp / bmp"))
    graph = build_graph(counts, total, w, h, fps=fps, fit=fit,
                        fade_in=fade_in, fade_out=fade_out,
                        transition=transition, transition_sec=transition_sec,
                        bg_blur=bg_blur, pad_color=pad_color,
                        bg_index=(len(counts) if add_bg else None),
                        audio_index=(len(counts) + (1 if add_bg else 0)),
                        motion=motion, volume=volume, crop_focus=crop_focus)
    cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
           "-progress", "pipe:1", "-nostats"]
    for c, nf in zip(clips, counts):
        cmd += ["-loop", "1", "-framerate", str(fps), "-t",
                f"{nf / fps + 0.2:.3f}", "-i", c.path]
    if add_bg:
        if fit == "video":
            cmd += ["-stream_loop", "-1", "-i", bg_path]
        else:
            cmd += ["-loop", "1", "-framerate", str(fps), "-t",
                    f"{total + 1.0:.3f}", "-i", bg_path]
    if audio_path:
        adur = probe_duration(audio_path)
        if adur > 0 and adur + 0.05 < total:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", audio_path]
    else:
        cmd += ["-f", "lavfi", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000"]

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
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
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
        print(tr("没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）"),
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
              crop_focus=a.crop_focus)
    w, h = st.size()
    opts = dict(w=w, h=h, fps=int(st["fps"]), fit=st["fit"], crf=st.crf(),
                bg_blur=bool(st["bg_blur"]), pad_color=st["pad_color"],
                bg_path=st["bg_path"],
                transition=st["transition"],
                transition_sec=float(st["transition_sec"]),
                motion=st["motion"], volume=float(st["music_volume"]),
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
        pr.total_setting = max(MIN_DUR * len(pr.clips), a.total * len(pr.clips))
    if audio:
        pr.audio_path = audio
        pr.audio_dur = probe_duration(audio)
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

    render(pr.clips, pr.frame_counts(), audio, a.export, **opts,
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
    from PyQt6.QtCore import (QPointF, QSize, Qt, QThread, QTimer, QUrl,
                              pyqtSignal)
    from PyQt6.QtGui import (QColor, QConicalGradient, QFont, QIcon,
                            QImageReader, QKeySequence, QLinearGradient,
                            QPainter, QPainterPath, QPalette, QPen, QPixmap,
                            QPolygonF, QShortcut)
    from PyQt6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtWidgets import (
        QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QColorDialog,
        QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
        QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
        QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
        QPushButton, QRadioButton, QSizePolicy, QSpinBox, QSplitter,
        QVBoxLayout, QWidget)
    from bili_audio import BiliError, bili_audio

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

    class YtdlpWorker(QThread):
        """在后台装 yt-dlp（装进 tools/，随时可删）。"""
        done = pyqtSignal(str)
        bad = pyqtSignal(str)
        note = pyqtSignal(str)

        def run(self):
            try:
                ver = install_ytdlp(progress=self.note.emit)
            except SourceError as e:
                self.bad.emit(e.message)
            except Exception as e:  # noqa: BLE001
                self.bad.emit(f"{e}")
            else:
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
        """内部拖动排序后发信号；QListWidget 的 internal move 不一定触发 rowsMoved。"""

        order_changed = pyqtSignal()

        def dropEvent(self, ev):
            super().dropEvent(ev)
            self.order_changed.emit()

    class Timeline(QWidget):
        seeked = pyqtSignal(float)
        picked = pyqtSignal(int)
        resized = pyqtSignal(int, float)

        DEFAULT_COLORS = {"bg": "#1f2329", "empty": "#9ca3af",
                          "piece_a": "#b86b6b", "piece_b": "#b8a05f",
                          "piece_text": "#111827", "playhead": "#f59e0b",
                          "selected_edge": "#3b82f6"}

        def __init__(self, project, parent=None):
            super().__init__(parent)
            self.project = project
            self.selected = -1
            self.play_pos = 0.0
            self._drag = None
            self.c = dict(self.DEFAULT_COLORS)
            self.setMinimumHeight(64)
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def _spans(self):
            durs = [c.dur for c in self.project.clips]
            total = sum(durs) or 1.0
            x, spans = 0.0, []
            for d in durs:
                w = self.width() * d / total
                spans.append((x, x + w, d))
                x += w
            return spans, total

        def set_colors(self, colors):
            """主题换了就换色带配色（时间线自己画，不吃 QSS）。"""
            self.c = dict(self.DEFAULT_COLORS)
            self.c.update({k: v for k, v in colors.items() if k in self.c})
            self.update()

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
            base = QColor(self.c["piece_a"])
            dark_bg = self.c.get("theme") == "dark"
            for i, (x0, x1, d) in enumerate(spans):
                # 色带统一用主色，隔一块微调明度，边界一眼能分清；
                # 对撞色只留给播放头 / 进度条这类点缀。
                col = QColor(base)
                if i % 2:
                    col = col.lighter(116) if dark_bg else col.darker(108)
                if i == self.selected:
                    col = col.lighter(125)
                p.fillRect(int(x0) + 1, 4, max(1, int(x1 - x0) - 2),
                           self.height() - 8, col)
                if i == self.selected:
                    p.setPen(QPen(QColor(self.c["selected_edge"]), 2))
                    p.drawRect(int(x0) + 1, 4, max(1, int(x1 - x0) - 2),
                               self.height() - 9)
                if x1 - x0 > 26:
                    p.setPen(QColor(theme.contrast_text(col.name())))
                    p.drawText(int(x0) + 6, self.height() - 12,
                               f"{i + 1} · {d:.1f}s")
            if total > 0:
                px = int(self.width() * min(1.0, self.play_pos / total))
                p.setPen(QPen(QColor(self.c["playhead"]), 2))
                p.drawLine(px, 0, px, self.height())

        def _hit(self, x):
            spans, _ = self._spans()
            for i, (x0, x1, _d) in enumerate(spans):
                if x0 - 4 <= x <= x1 + 4:
                    return i, x1
            return -1, 0

        def mousePressEvent(self, ev):
            if ev.button() != Qt.MouseButton.LeftButton:
                return
            x = ev.position().x()
            spans, total = self._spans()
            for i, (x0, x1, d) in enumerate(spans):
                if abs(x - x1) <= 5:
                    self._drag = (i, x, d)
                    self.setCursor(Qt.CursorShape.SizeHorCursor)
                    return
            for i, (x0, x1, d) in enumerate(spans):
                if x0 <= x <= x1:
                    self.selected = i
                    self.picked.emit(i)
                    start = sum(c.dur for c in self.project.clips[:i])
                    self.seeked.emit(start)
                    self.update()
                    return

        def mouseMoveEvent(self, ev):
            if self._drag:
                i, x0, d0 = self._drag
                spans, total = self._spans()
                if total > 0:
                    self.resized.emit(i, d0 + (ev.position().x() - x0) * total / max(1, self.width()))
                return
            x = ev.position().x()
            near = any(abs(x - x1) <= 5 for _x0, x1, _d in self._spans()[0])
            self.setCursor(Qt.CursorShape.SizeHorCursor if near
                           else Qt.CursorShape.PointingHandCursor)

        def mouseReleaseEvent(self, ev):
            self._drag = None
            self.setCursor(Qt.CursorShape.PointingHandCursor)

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

    class ColorWheelDialog(QDialog):
        """色环取色 + 自动对撞色预览（留白颜色、主色都用它）。"""

        def __init__(self, color: str, title: str, parent=None):
            super().__init__(parent)
            self.setWindowTitle(title)
            self.setMinimumWidth(360)
            v = QVBoxLayout(self)
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

    class SettingsDialog(QDialog):
        """设置：导出目录、画面规格、铺底、节奏默认值、声音与转场。

        预览音频不在这里选：它始终跟随 Windows 当前默认输出设备，
        系统里换耳机/扬声器时自动跟着换（见 Window._follow_audio）。
        """

        def __init__(self, settings: Settings, parent=None):
            super().__init__(parent)
            self.s = settings
            self.setWindowTitle(tr("设置"))
            self.setMinimumWidth(620)
            self.pro = settings["ui"] != "simple"
            form = QFormLayout(self)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setFieldGrowthPolicy(
                QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            self._pro_fields = []   # 简洁模式要藏起来的（标签和控件一起藏）

            def add_row(text, field, pro=False):
                form.addRow(tr(text), field)
                if pro:
                    lab = form.labelForField(field)
                    if lab is not None:
                        self._pro_fields.append(lab)
                    self._pro_fields.append(field)
                return field

            # 语言 + 导出
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
            add_row("主色", self.btn_accent)
            self._sync_accent()

            self.edit_dir = QLineEdit(settings["export_dir"])
            self.edit_dir.setPlaceholderText(
                tr("留空 = 每张图所在目录（每次导出还会弹框确认）"))
            browse = QPushButton(tr("浏览…"))
            browse.clicked.connect(self._browse_dir)
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(self.edit_dir, 1)
            row.addWidget(browse)
            wrap = QWidget()
            wrap.setLayout(row)
            add_row("默认导出目录", wrap)

            self.chk_open = QCheckBox(tr("导出完自动打开所在文件夹"))
            self.chk_open.setChecked(bool(settings["open_after_export"]))
            form.addRow(self.chk_open)   # 自带文字，不需要左边标签

            # 画面
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

            # 节奏
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

            # 声音
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

            self.cmb_crop = QComboBox()
            for key in CROP_FOCUS:
                self.cmb_crop.addItem(tr(CROP_LABELS[key]), key)
            self.cmb_crop.setCurrentIndex(
                max(0, list(CROP_FOCUS).index(settings["crop_focus"])))
            add_row("裁切位置", self.cmb_crop, pro=True)

            self.spin_vol = QSpinBox()
            self.spin_vol.setRange(0, 200)
            self.spin_vol.setSingleStep(5)
            self.spin_vol.setSuffix("%")
            self.spin_vol.setValue(int(round(float(settings["music_volume"]) * 100)))
            add_row("音乐音量", self.spin_vol, pro=True)

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

            # 音源组件（抖音 / YouTube 等要用，按需下载、按需删除）
            self._ydlp = None
            self.lbl_ytdlp = QLabel()
            self.btn_ytdlp = QPushButton(tr("下载"))
            self.btn_ytdlp.clicked.connect(self._toggle_ytdlp)
            row_y = QHBoxLayout()
            row_y.setContentsMargins(0, 0, 0, 0)
            row_y.addWidget(self.lbl_ytdlp, 1)
            row_y.addWidget(self.btn_ytdlp)
            wrap_y = QWidget()
            wrap_y.setLayout(row_y)
            add_row("抖音 / YouTube 支持", wrap_y, pro=True)
            self._sync_ytdlp()

            # 音频设备（只读，自动跟随）
            self.lbl_audio = QLabel(device_text())
            self.lbl_audio.setStyleSheet("color:#9ca3af")
            add_row("预览音频输出", self.lbl_audio, pro=True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel)
            buttons.button(
                QDialogButtonBox.StandardButton.Ok).setText(tr("好"))
            buttons.button(
                QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            form.addRow(buttons)

            if not self.pro:                      # 简洁模式：只留必要项
                for w_ in self._pro_fields:
                    w_.setVisible(False)

            self._sync_size_label()
            self._sync_fit()
            self._sync_trans()

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

        def _pick_accent(self):
            dlg = ColorWheelDialog(self._accent, tr("选主色"), self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self._accent = dlg.value()
                self._sync_accent()

        def _browse_bg(self):
            video = self.cmb_fit.currentData() == "video"
            cap = tr("选背景视频") if video else tr("选背景图片")
            flt = (tr("视频 (*.mp4 *.mov *.mkv *.webm)") if video
                   else tr("图片 (*.jpg *.jpeg *.png *.bmp *.webp)"))
            path, _ = QFileDialog.getOpenFileName(self, cap,
                                                  self.edit_bg.text() or "", flt)
            if path:
                self.edit_bg.setText(path)

        def _sync_trans(self, *_a):
            self.spin_trans.setEnabled(self.cmb_trans.currentData() == "fade")

        # -- 音源组件（yt-dlp）--------------------------------------------
        def _ytdlp_mb(self) -> int:
            import audio_source as _as
            total = 0
            for root, _dirs, files in os.walk(_as.TOOLS_DIR):
                for n in files:
                    try:
                        total += os.path.getsize(os.path.join(root, n))
                    except OSError:
                        pass
            return max(1, int(round(total / 1048576.0)))

        def _sync_ytdlp(self):
            if ytdlp_ready():
                ver = ytdlp_version()
                self.lbl_ytdlp.setText(
                    tr("已安装 {v} · {mb} MB（占地方可以随时删）",
                       v=ver or "yt-dlp", mb=self._ytdlp_mb()))
                self.btn_ytdlp.setText(tr("删除"))
            else:
                self.lbl_ytdlp.setText(
                    tr("没装：抖音 / YouTube / 快手等要靠它取音轨"))
                self.btn_ytdlp.setText(tr("下载"))

        def _toggle_ytdlp(self):
            if self._ydlp is not None and self._ydlp.isRunning():
                return
            if ytdlp_ready():
                remove_ytdlp()
                self._sync_ytdlp()
                return
            self.btn_ytdlp.setEnabled(False)
            self.lbl_ytdlp.setText(tr("正在下载音源组件…"))
            self._ydlp = YtdlpWorker(self)
            self._ydlp.note.connect(self.lbl_ytdlp.setText)
            self._ydlp.done.connect(self._on_ytdlp_done)
            self._ydlp.bad.connect(self._on_ytdlp_bad)
            self._ydlp.start()

        def _on_ytdlp_done(self, ver):
            self.btn_ytdlp.setEnabled(True)
            self._sync_ytdlp()

        def _on_ytdlp_bad(self, msg):
            self.btn_ytdlp.setEnabled(True)
            self._sync_ytdlp()
            QMessageBox.warning(self, tr("音源组件"), msg)

        def _browse_dir(self):
            d = QFileDialog.getExistingDirectory(self, tr("选默认导出目录"),
                                                 self.edit_dir.text() or "")
            if d:
                self.edit_dir.setText(d)

        def accept(self):
            self.s.update(
                lang=self.cmb_lang.currentData(),
                theme=self.cmb_theme.currentData(),
                accent=self._accent,
                export_dir=self.edit_dir.text().strip(),
                open_after_export=bool(self.chk_open.isChecked()),
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
                crop_focus=self.cmb_crop.currentData(),
                transition_sec=float(self.spin_trans.value()),
            )
            super().accept()

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(tr("图片出片 —— 图片 + B站 BGM → 视频"))
            self.resize(1180, 800)
            self.setAcceptDrops(True)
            self.settings = Settings()
            set_locale(self.settings["lang"])
            self.preview_name = ""
            self.project = Project(fps=int(self.settings["fps"]))
            self.project.mode = self.settings["mode"]
            self.tmpdir = tempfile.mkdtemp(prefix="pic2video_")
            self.preview_path = os.path.join(self.tmpdir, "preview.mp4")
            self.preview_stale = True
            self.worker = None
            self.fetch_worker = None
            self._rebuilding = False
            self._render_kind = "export"
            self._render_t0 = 0.0

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

        def _bind_shortcuts(self):
            """键盘：Ctrl+O 加图、Ctrl+E 导出、空格播放/暂停、Delete 删图。"""
            for keys, slot in (("Ctrl+O", self.on_add),
                               ("Ctrl+S", self.on_export),
                               ("Space", self.toggle_play),
                               ("Delete", self.on_remove),
                               ("F5", self.on_preview)):
                QShortcut(QKeySequence(keys), self).activated.connect(slot)

        # ---------------- 窗口大小 / 位置
        def _restore_geometry(self):
            """上次关窗时的大小和位置；不在任何屏幕上就退回默认。"""
            s = self.settings
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

        def _save_geometry(self):
            s = self.settings
            g = self.normalGeometry() if self.isMaximized() else self.geometry()
            s["win_w"], s["win_h"] = g.width(), g.height()
            s["win_x"], s["win_y"] = g.x(), g.y()
            s.save()

        # ---------------- UI
        def _build_ui(self):
            root = QWidget()
            v = QVBoxLayout(root)
            v.setContentsMargins(10, 8, 10, 8)
            v.setSpacing(8)

            bar = QHBoxLayout()
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
            self.btn_settings = btn(
                tr("设置"), self.on_settings,
                tr("导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏"))
            bar.addSpacing(12)
            self.radio_bgm = QRadioButton(tr("跟 BGM"))
            self.radio_total = QRadioButton(tr("自设总长"))
            self.radio_free = QRadioButton(tr("逐张秒数"))
            self.radio_bgm.setChecked(True)
            group = QButtonGroup(self)
            for r in (self.radio_bgm, self.radio_total, self.radio_free):
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
            self.list.itemDoubleClicked.connect(self.on_edit_dur)
            self.list.currentRowChanged.connect(self.on_row_changed)
            self.list.setContextMenuPolicy(
                Qt.ContextMenuPolicy.CustomContextMenu)
            self.list.customContextMenuRequested.connect(self.on_list_menu)
            split.addWidget(self.list)

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

        # ---------------- 语言 / 模式
        def retranslate(self):
            """换语言或换模式后重设文案，不重建窗口。"""
            self.setWindowTitle(tr("图片出片 —— 图片 + B站 BGM → 视频"))
            self.btn_add.setText(tr("加图片"))
            self.btn_up.setText(tr("上移"))
            self.btn_down.setText(tr("下移"))
            self.btn_remove.setText(tr("删除"))
            self.btn_settings.setText(tr("设置"))
            self.btn_settings.setToolTip(
                tr("导出目录、画幅比例、分辨率、帧率、画质、铺底、转场、默认节奏"))
            self.radio_bgm.setText(tr("跟 BGM"))
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
            # 英文/日文文案更长：先让按钮/标签不许缩到文字以下，再按需把窗口拉宽
            self.ensurePolished()
            for w_ in (self.btn_add, self.btn_up, self.btn_down, self.btn_remove,
                       self.btn_settings, self.btn_refresh, self.btn_export,
                       self.btn_mode, self.btn_retry, self.lbl_bgm,
                       self.btn_more, self.radio_bgm, self.radio_total,
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
            """按设置算一套配色，往窗口上贴 QSS；时间线单独喂一份颜色。"""
            pal = theme.palette(theme.resolve(self.settings["theme"]),
                                self.settings["accent"])
            self.pal = pal
            self.setStyleSheet(
                theme.build_qss(pal, assets=self._theme_assets(pal)))
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
                       self.btn_settings, self.radio_bgm, self.radio_total,
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
            self.radio_bgm.setChecked(self.project.mode == "bgm")
            self.radio_total.setChecked(self.project.mode == "total")
            self.radio_free.setChecked(self.project.mode == "free")
            self.spin_total.setEnabled(self.project.mode == "total")
            if self.project.mode == "total":
                self.spin_total.blockSignals(True)
                self.spin_total.setValue(self.project.total_setting)
                self.spin_total.blockSignals(False)
            n = len(self.project.clips)
            src = "BGM" if self.project.audio_path else tr("无音乐")
            self.lbl_status.setText(
                tr("{n} 张图 · 总长 {total} · {src}", n=n,
                   total=human_time(self.project.total()), src=src))

        def _refresh_list(self):
            self._rebuilding = True
            cur = self.list.currentRow()
            self.list.clear()
            for i, c in enumerate(self.project.clips):
                it = QListWidgetItem(self._thumb(c.path), "")
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
                it.setText(tr("{i}.  {dur} 秒   {name}", i=i + 1,
                              dur=f"{c.dur:.1f}",
                              name=os.path.basename(c.path)))

        def _thumb(self, path):
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
            dlg = SettingsDialog(self.settings, self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.settings.save()
            set_locale(self.settings["lang"])
            self.apply_theme()
            self.project.mode = self.settings["mode"]
            self.project.fps = int(self.settings["fps"])
            set_mode(self.project, self.project.mode)
            self.retranslate()
            self._mark_stale()
            w, h = self.settings.size()
            self.lbl_preview.setText(
                tr("参数已更新：{w}x{h} {fps}fps，点「刷新预览」看效果",
                   w=w, h=h, fps=self.settings["fps"]))
            self._follow_audio()
            self.lbl_status.setText(tr("设置已保存"))

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
                        motion=s["motion"],
                        volume=float(s["music_volume"]),
                        crop_focus=s["crop_focus"])

        # ---------------- 动作
        def on_add(self):
            paths, _ = QFileDialog.getOpenFileNames(
                self, tr("选图片"), "", tr("图片 (*.jpg *.jpeg *.png *.bmp *.webp)"))
            if paths:
                self.add_images(paths)

        def add_images(self, paths):
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
                self.project.add_images(good, dur=dur)
                self.radio_total.setChecked(self.project.mode == "total")
                self._refresh_all()
                self.lbl_status.setText(tr("已加 {n} 张图", n=len(good)))

        def on_move(self, delta):
            row = self.list.currentRow()
            if row < 0:
                return
            self.project.move(row, delta)
            self._refresh_all()
            self.list.setCurrentRow(max(0, min(row + delta, self.list.count() - 1)))

        def on_remove(self):
            row = self.list.currentRow()
            if row < 0:
                return
            self.project.remove([row])
            self._refresh_all()

        def on_edit_dur(self, item):
            row = self.list.row(item)
            if not (0 <= row < len(self.project.clips)):
                return
            cur = self.project.clips[row].dur
            val, ok = QInputDialog.getDouble(
                self, tr("这张图显示多久"), tr("第 {row} 张（秒）：", row=row + 1),
                cur, MIN_DUR, 3600, 1)
            if ok:
                self.project.set_duration(row, val)
                self._refresh_all()

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

        def on_row_changed(self, row):
            self.timeline.selected = row
            self.timeline.update()

        def on_mode(self, mode):
            set_mode(self.project, mode)
            self._refresh_all()

        def on_total_changed(self, value):
            if self.project.mode != "total":
                return
            self.project.total_setting = value
            self.project.normalize()
            self._refresh_all()

        def on_timeline_pick(self, i):
            self.list.setCurrentRow(i)

        def on_timeline_resize(self, i, value):
            self.project.set_duration(i, value)
            self._update_titles()
            self.timeline.update()
            self._mark_stale()
            self.lbl_status.setText(
                tr("{n} 张图 · 总长 {total}", n=len(self.project.clips),
                   total=human_time(self.project.total())))

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
            if not self.project.clips:
                self.lbl_status.setText(tr("先加图片"))
                return
            if self.worker is not None and self.worker.isRunning():
                return
            self._follow_audio()
            self._rendering(True, "preview")
            self.lbl_status.setText(tr("生成预览…"))
            self.worker = RenderWorker(
                list(self.project.clips), self.project.frame_counts(),
                self.project.audio_path, self.preview_path,
                opts=self._opts(preview=True), preview=True, parent=self)
            self.worker.progress.connect(self._on_render_progress)
            self.worker.ok.connect(self.on_preview_ok)
            self.worker.bad.connect(self.on_preview_bad)
            self.worker.stopped.connect(self.on_render_stopped)
            self.worker.start()

        def on_preview_ok(self, path):
            self._rendering(False)
            self._clean_stale()
            self.player.setSource(QUrl.fromLocalFile(path))
            self.player.play()
            self.lbl_status.setText(
                tr("预览已更新（点画面播放/暂停，点色带跳转）"))
            self.preview_name = os.path.basename(path)
            self.lbl_preview.setText(tr("预览：{name}", name=self.preview_name))

        def on_preview_bad(self, msg):
            self._rendering(False)
            self.lbl_status.setText(tr("预览失败"))
            QMessageBox.warning(self, tr("预览失败"), msg)

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
            self.btn_retry.setEnabled(False)
            self.lbl_status.setText(tr("正在取音轨…"))
            self.fetch_worker = FetchWorker(url, self.tmpdir, self)
            self.fetch_worker.note.connect(self.lbl_status.setText)
            self.fetch_worker.ok.connect(self.on_fetch_ok)
            self.fetch_worker.bad.connect(self.on_fetch_bad)
            self.fetch_worker.start()

        def _offer_ytdlp(self) -> bool:
            """非 B 站链接需要音源组件时问一句，点了就后台装。"""
            box = QMessageBox(self)
            box.setWindowTitle(tr("需要音源组件"))
            box.setText(tr("这个平台的链接要靠 yt-dlp 解析（约 15 MB，"
                           "装在工具目录里，随时可删）。现在下载吗？"))
            yes = box.addButton(tr("下载并继续"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(tr("取消"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is not yes:
                return False
            self.lbl_status.setText(tr("正在下载音源组件…"))
            worker = YtdlpWorker(self)
            worker.note.connect(self.lbl_status.setText)
            worker.done.connect(lambda _v: self.start_fetch())
            worker.bad.connect(lambda m: QMessageBox.warning(
                self, tr("音源组件"), m))
            self._ydlp = worker
            worker.start()
            return False

        def on_fetch_ok(self, path, dur):
            self.project.audio_path = path
            self.project.audio_dur = dur
            self.project.normalize()
            self.btn_retry.setEnabled(True)
            self._refresh_all()
            self.lbl_status.setText(
                tr("BGM 已就绪：{total} · {name}",
                   total=human_time(dur), name=os.path.basename(path)))

        def on_fetch_bad(self, msg):
            self.btn_retry.setEnabled(True)
            self.lbl_status.setText(tr("取音轨失败"))
            QMessageBox.warning(self, tr("取音轨失败"), msg)

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
            out = os.environ.get("PIC2VIDEO_EXPORT_PATH") or ""
            if not out:
                out, _picked = QFileDialog.getSaveFileName(
                    self, tr("导出到…"), os.path.join(base_dir, stem + ".mp4"),
                    tr("视频 (*.mp4)"))
            if not out:
                return
            if not out.lower().endswith(".mp4"):
                out += ".mp4"
            self.settings["last_export_dir"] = os.path.dirname(out) or base_dir
            self.settings.save()
            self._rendering(True, "export")
            self.lbl_status.setText(tr("正在导出…"))
            self.worker = RenderWorker(
                list(self.project.clips), self.project.frame_counts(),
                self.project.audio_path, out, opts=self._opts(),
                preview=False, parent=self)
            self.worker.progress.connect(self._on_render_progress)
            self.worker.ok.connect(self.on_export_ok)
            self.worker.bad.connect(self.on_export_bad)
            self.worker.stopped.connect(self.on_render_stopped)
            self.worker.start()

        def on_export_ok(self, path):
            self._rendering(False)
            self.lbl_status.setText(tr("导出完成：{path}", path=path))
            if os.environ.get("PIC2VIDEO_SHOT"):
                return
            if self.settings["open_after_export"]:
                try:
                    subprocess.Popen(["explorer", "/select,",
                                      os.path.normpath(path)])
                except OSError:
                    pass
                return
            box = QMessageBox(self)
            box.setWindowTitle(tr("导出完成"))
            box.setText(tr("视频已生成：\n{path}", path=path))
            open_btn = box.addButton(tr("打开所在文件夹"),
                                     QMessageBox.ButtonRole.ActionRole)
            box.addButton(tr("好"), QMessageBox.ButtonRole.AcceptRole)
            box.exec()
            if box.clickedButton() is open_btn:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

        def on_export_bad(self, msg):
            self._rendering(False)
            self.lbl_status.setText(tr("导出失败"))
            QMessageBox.warning(self, tr("导出失败"), msg)

        # ---------------- 拖放
        def dragEnterEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dropEvent(self, ev):
            # 拖一整个文件夹进来 = 里面能用的全要
            paths = expand_paths([u.toLocalFile()
                                  for u in ev.mimeData().urls()
                                  if u.isLocalFile()])
            imgs = [p for p in paths if os.path.splitext(p)[1].lower() in IMG_EXTS]
            audio = [p for p in paths
                     if os.path.splitext(p)[1].lower() in AUDIO_EXTS]
            media = [p for p in paths
                     if os.path.splitext(p)[1].lower() in MEDIA_EXTS]
            if imgs:
                self.add_images(sorted(imgs, key=lambda p: os.path.basename(p).lower()))
            # 拖进来的音频 / 视频当 BGM（视频会抽音轨）
            if audio or media:
                self.edit_bgm.setText((audio or media)[0])
                self.start_fetch()
            if imgs or audio or media:
                ev.acceptProposedAction()

        def closeEvent(self, ev):
            busy = self.worker is not None and self.worker.isRunning()
            if busy and not os.environ.get("PIC2VIDEO_SHOT"):
                box = QMessageBox(self)
                box.setWindowTitle(tr("还在导出"))
                box.setText(tr("视频还在导出，现在关掉会丢掉这次的结果。"))
                stay = box.addButton(tr("继续导出"), QMessageBox.ButtonRole.AcceptRole)
                box.addButton(tr("关掉"), QMessageBox.ButtonRole.DestructiveRole)
                box.exec()
                if box.clickedButton() is stay:
                    ev.ignore()
                    return
            self._save_geometry()
            if self.worker is not None and self.worker.isRunning():
                self.worker.cancel()
            for w in (self.worker, self.fetch_worker):
                if w is not None and w.isRunning():
                    w.wait(3000)
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            super().closeEvent(ev)

    app = QApplication(sys.argv[:1])
    if missing_tools():
        QMessageBox.critical(
            None, tr("缺少依赖"),
            tr("没找到 ffmpeg / ffprobe。\nWindows 上一行装完：winget install Gyan.FFmpeg\n（装好后重开这个窗口）"))
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

    w.radio_bgm.toggled.connect(
        lambda on: on and w.on_mode("bgm"))
    w.radio_total.toggled.connect(
        lambda on: on and w.on_mode("total"))
    w.radio_free.toggled.connect(
        lambda on: on and w.on_mode("free"))
    w.spin_total.valueChanged.connect(w.on_total_changed)
    w.show()

    if shot:
        for p in [x for x in os.environ.get("PIC2VIDEO_SHOT_IMAGES", "").split(";") if x]:
            w.project.add_images([p])
        audio = os.environ.get("PIC2VIDEO_SHOT_AUDIO")
        if audio:
            w.project.audio_path = audio
            w.project.audio_dur = probe_duration(audio)
            w.project.normalize()
        w._refresh_all()

        def _shot(name):
            w.grab().save(os.path.join(os.path.dirname(shot), name + tag + ".png"))

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
            QTimer.singleShot(300, app.quit)
            return app.exec()

        def shot_settings():
            dlg = SettingsDialog(w.settings, w)
            dlg.show()

            def grab_dlg():
                dlg.grab().save(os.path.join(os.path.dirname(shot),
                                             "shot_settings" + tag + ".png"))
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

            QTimer.singleShot(700, grab_dlg)

        def step1():
            shot_settings()
            QTimer.singleShot(4000, step2)

        def step2():
            _shot("shot_preview")
            if audio:
                w.on_export()
                QTimer.singleShot(6000, step3)
            else:
                app.quit()

        def step3():
            _shot("shot_export")
            app.quit()

        def live_switch():
            """现场切语言 / 模式，验证不用重开窗口（留出重新布局的时间再截图）。"""
            _shot("shot_live_before")

            def to_en():
                w.settings["lang"] = "en"
                set_locale("en")
                w.retranslate()
                QTimer.singleShot(600, shot_en)

            def shot_en():
                _shot("shot_live_en")
                w.settings["lang"] = "ja"
                set_locale("ja")
                w.retranslate()
                w.btn_mode.click()          # 真按一次模式开关（含写 settings.json）
                QTimer.singleShot(600, shot_ja)

            def shot_ja():
                _shot("shot_live_ja_simple")
                names = ["btn_add", "btn_up", "btn_down", "btn_remove",
                         "btn_settings", "btn_refresh", "btn_export",
                         "btn_mode", "btn_more", "radio_bgm", "spin_total"]
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


def main() -> int:
    argv = sys.argv[1:]
    if "--export" in argv:
        return cli_export(argv)
    return run_gui()


if __name__ == "__main__":
    sys.exit(main())
