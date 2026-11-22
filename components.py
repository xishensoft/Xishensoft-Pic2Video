# -*- coding: utf-8 -*-
r"""components —— 按需下载 / 删除外部组件（ffmpeg 与 yt-dlp）。

设计（用户拍板）：
  · 发布包**不带** ffmpeg，第一次打开自动下载；以后检测不到也会自动补。
  · yt-dlp 同样按需下载（抖音 / YouTube 之类才需要）。
  · 两个都在设置里能随时下载 / 删除，删了工具照样能开、能导出有 ffmpeg 的来源。
  · 源码运行时 tools/ 就在项目目录；打包成 exe 后放 exe 旁边的 tools/，
    那里不可写（例如放在 Program Files）就退到 %LOCALAPPDATA%\Pic2Video\tools。

对外：
  tools_dir() / data_dir() / settings_path()
  have(name) / exe(name) / version(name) / size_mb(name)
  install(name, progress=None) / remove(name)
  prepend_path()          把 tools/ 放到 PATH 最前面，让 subprocess 的 "ffmpeg" 直接命中
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

FFMPEG_ZIP = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
YTDLP_EXE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"

COMPONENTS = {
    "ffmpeg": {
        "label": "ffmpeg",
        "hint": "出片引擎，约 120 MB",
        "url": FFMPEG_ZIP,
        "kind": "zip",
        "files": ("ffmpeg.exe", "ffprobe.exe"),
        "hint_mb": 90,
    },
    "ytdlp": {
        "label": "yt-dlp",
        "hint": "抖音 / YouTube 等链接解析，约 25 MB",
        "url": YTDLP_EXE,
        "kind": "file",
        "files": ("yt-dlp.exe",),
        "hint_mb": 25,
    },
}
ORDER = ("ffmpeg", "ytdlp")


class ComponentError(RuntimeError):
    """下载 / 解包失败，message 可以直接给用户看。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------- 目录

def app_dir() -> str:
    """程序所在目录：打包后是 exe 所在目录，源码运行时是项目目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return HERE


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


_data_dir = ""


def data_dir() -> str:
    """存 settings.json 与 tools/ 的地方：优先程序目录，不可写就用 LOCALAPPDATA。"""
    global _data_dir
    if _data_dir:
        return _data_dir
    candidate = app_dir()
    if _writable(candidate):
        _data_dir = candidate
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        fallback = os.path.join(base, "Pic2Video")
        _writable(fallback)
        _data_dir = fallback
    return _data_dir


def tools_dir() -> str:
    d = os.path.join(data_dir(), "tools")
    os.makedirs(d, exist_ok=True)
    return d


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


# ---------------------------------------------------------------- 查询

def _target(name: str) -> list:
    return [os.path.join(tools_dir(), f) for f in COMPONENTS[name]["files"]]


def have(name: str) -> bool:
    return all(os.path.isfile(p) for p in _target(name))


def exe(name: str) -> str:
    """ffmpeg / ffprobe / yt-dlp 的可执行路径；tools/ 没有就退回 PATH。"""
    want = {"ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe",
            "ytdlp": "yt-dlp.exe"}[name]
    local = os.path.join(tools_dir(), want)
    if os.path.isfile(local):
        return local
    bare = want[:-4] if want.endswith(".exe") else want
    return shutil.which(bare) or ""


def size_mb(name: str) -> int:
    total = 0
    for p in _target(name):
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return int(round(total / (1024 * 1024)))


def version(name: str) -> str:
    path = exe(name)
    if not path:
        return ""
    args = [path, "-version"] if name == "ffmpeg" else [path, "--version"]
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return ""
    line = (p.stdout or p.stderr or "").strip().splitlines()
    if not line:
        return ""
    text = line[0]
    if name == "ffmpeg":
        for token in text.split():
            if token[:1].isdigit():
                return token
        return text[:40]
    return text.replace("yt-dlp", "").strip(" -v")


def prepend_path() -> None:
    """tools/ 放到 PATH 最前面：subprocess 里的 "ffmpeg" 会先命中最新的那份。"""
    d = tools_dir()
    if not os.path.isdir(d):
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    parts = [p for p in parts if os.path.normcase(p) != os.path.normcase(d)]
    os.environ["PATH"] = os.pathsep.join([d] + parts)


# ---------------------------------------------------------------- 下载

def _download(url: str, dest: str, progress=None, label: str = "") -> None:
    """边下边报进度（progress(文本, 百分比 0..1)）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "Pic2Video"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(dest, "wb") as fh:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(label, min(done / total, 1.0))
    except Exception as exc:                     # urllib 的异常种类很杂
        raise ComponentError(
            "下载失败：检查网络，或者稍后再试一次", detail=str(exc))


def install(name: str, progress=None) -> str:
    """下载并装好一个组件，返回版本号（失败抛 ComponentError）。"""
    if name not in COMPONENTS:
        raise ComponentError("没有这个组件")
    spec = COMPONENTS[name]
    if progress:
        progress("正在下载 %s（%s）…" % (spec["label"], spec["hint"]), 0.0)
    tmpdir = tempfile.mkdtemp(prefix="p2v_comp_")
    try:
        if spec["kind"] == "file":
            dest = os.path.join(tools_dir(), spec["files"][0])
            part = dest + ".part"
            _download(spec["url"], part, progress=progress,
                      label="正在下载 %s…" % spec["label"])
            os.replace(part, dest)
        else:
            archive = os.path.join(tmpdir, "comp.zip")
            _download(spec["url"], archive, progress=progress,
                      label="正在下载 %s…" % spec["label"])
            if progress:
                progress("正在解包 %s…" % spec["label"], 0.99)
            with zipfile.ZipFile(archive) as zf:
                members = {os.path.basename(n).lower(): n for n in zf.namelist()}
                for want in spec["files"]:
                    key = want.lower()
                    if key not in members:
                        raise ComponentError(
                            "下载包里找不到 %s（源可能变了）" % want)
                    with zf.open(members[key]) as src, \
                            open(os.path.join(tools_dir(), want), "wb") as dst:
                        shutil.copyfileobj(src, dst)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    prepend_path()
    if not have(name):
        raise ComponentError("装完了但文件不在，检查杀毒软件是否拦了")
    return version(name)


def remove(name: str) -> None:
    """删掉这个组件的文件（只删自己那几个，别的不动）。"""
    for path in _target(name):
        try:
            os.remove(path)
        except OSError:
            pass


def ensure_ffmpeg(progress=None) -> None:
    """没 ffmpeg 就下一次；已经有就什么都不做。"""
    if have("ffmpeg") or (exe("ffmpeg") and exe("ffprobe")):
        return
    install("ffmpeg", progress=progress)
