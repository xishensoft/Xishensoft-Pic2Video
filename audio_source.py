# -*- coding: utf-8 -*-
"""audio_source —— 统一取音源：B 站 / 其它平台（yt-dlp）/ 本地文件。

用户拍板的分工：
  B 站：用仓内已验证的 bili_audio.py（纯标准库，无 Cookie，取整条音轨）；
  抖音 / YouTube / 快手 / 小红书 / 微博等：用 yt-dlp。它**不随工具发布**，
      第一次要用时在设置里点一下「下载音源组件」（pip --target 到 tools/），
      不想用了点「删除」就干净移除，工具本身永远能跑。
  本地文件：直接拿路径，不联网。

对外只有几个函数：
  kind_of(source) -> "bili" | "local" | "web"
  fetch_audio(source, out_path, progress=None) -> (path, kind)
  ytdlp_ready() / ytdlp_version() / install_ytdlp() / remove_ytdlp()
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(HERE, "tools")

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus",
              ".wma", ".m4b", ".aiff", ".ape"}

BILI_HOSTS = ("bilibili.com", "b23.tv", "bili2233.cn", "bili22.cn",
              "bili33.cn", "bili23.cn")
BILI_ID = re.compile(r"(BV[0-9A-Za-z]{10})|(^|\b)av(\d+)", re.I)


class SourceError(RuntimeError):
    """取音源失败，message 已经是可以直接给用户看的中文。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


# ---------------------------------------------------------------- 判断来源

def is_local(source: str) -> bool:
    s = (source or "").strip().strip('"')
    return bool(s) and os.path.exists(s) and os.path.isfile(s)


def is_bilibili(source: str) -> bool:
    s = (source or "").strip()
    low = s.lower()
    if any(h in low for h in BILI_HOSTS):
        return True
    return bool(BILI_ID.search(s)) and "://" not in s


def kind_of(source: str) -> str:
    if is_local(source):
        return "local"
    if is_bilibili(source):
        return "bili"
    return "web"


# ---------------------------------------------------------------- yt-dlp

def _ytdlp_path() -> str:
    return os.path.join(TOOLS_DIR, "yt_dlp")


def ytdlp_ready() -> bool:
    if os.path.isdir(_ytdlp_path()):
        return True
    try:                       # 系统里已经装过也行
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def _ytdlp_env() -> dict:
    env = dict(os.environ)
    if os.path.isdir(_ytdlp_path()):
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = _ytdlp_path() + (os.pathsep + prev if prev else "")
    return env


def _run_ytdlp(args: list, timeout: int = 600, progress=None) -> str:
    """跑一次 yt-dlp，返回合并输出（失败抛 SourceError）。"""
    cmd = [sys.executable, "-m", "yt_dlp"] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env=_ytdlp_env(), timeout=timeout)
    except FileNotFoundError:
        raise SourceError(tr("找不到 Python，无法调用 yt-dlp"))
    except subprocess.TimeoutExpired:
        raise SourceError(tr("解析超时（网络太慢或站点限流），稍后再试"))
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        raise SourceError(_friendly(out), detail=out[-2000:])
    return out


def ytdlp_version() -> str:
    if not ytdlp_ready():
        return ""
    try:
        out = _run_ytdlp(["--version"], timeout=40)
        return out.strip().splitlines()[0].strip()
    except Exception:
        return ""


def install_ytdlp(progress=None) -> str:
    """把 yt-dlp 装到 tools/（不污染系统环境）。返回版本号。"""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    if progress:
        progress(tr("正在下载音源组件（yt-dlp）…"))
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade",
           "--no-warn-script-location", "--target", TOOLS_DIR, "yt-dlp"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        raise SourceError(tr("下载音源组件超时，检查网络后再试"))
    if p.returncode != 0:
        raise SourceError(tr("下载音源组件失败（检查网络 / pip）"),
                          detail=(p.stdout or "") + (p.stderr or ""))
    ver = ytdlp_version()
    if progress:
        progress(tr("音源组件已就绪：{v}", v=ver) if ver else tr("音源组件已就绪"))
    return ver


def remove_ytdlp() -> None:
    """删掉 tools/ 下装进去的东西（只删这个目录，别的不动）。"""
    for name in os.listdir(TOOLS_DIR) if os.path.isdir(TOOLS_DIR) else []:
        path = os.path.join(TOOLS_DIR, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def _friendly(out: str) -> str:
    low = out.lower()
    if "unsupported url" in low or "no suitable extractor" in low:
        return tr("这个链接不支持（可能是私密/直播/需要登录）")
    if "sign in" in low or "login" in low or "cookies" in low:
        return tr("该站点要求登录，暂时取不到音轨")
    if "private" in low:
        return tr("视频是私密的，取不到音轨")
    if "geo" in low and "restrict" in low:
        return tr("该视频有地区限制")
    if "timed out" in low or "timeout" in low or "connection" in low:
        return tr("网络连不上该站点（可能要代理）")
    if "unable to download" in low or "http error 403" in low:
        return tr("下载被站点拒绝（可能需要登录或频繁重试）")
    return tr("取音轨失败（详情见设置里重试一次）")


# ---------------------------------------------------------------- 主入口

def fetch_audio(source: str, out_path: str = None, progress=None,
                timeout: int = 900) -> tuple:
    """把任意来源变成一条本地音轨文件。

    source: B 站链接/BV 号、其它平台链接、或本地音视频路径
    返回 (path, kind)，kind 为 "bili" / "web" / "local"
    """
    src = (source or "").strip().strip('"')
    if not src:
        raise SourceError(tr("先给个链接或本地文件"))
    kind = kind_of(src)

    if kind == "local":
        path = os.path.abspath(src)
        if os.path.splitext(path)[1].lower() in AUDIO_EXTS:
            return path, "local"
        # 本地视频：抽一条音轨出来
        import pic2video as p2v  # 复用它的 ffmpeg 封装
        if progress:
            progress(tr("正在从本地视频里抽音轨…"))
        out = out_path or os.path.splitext(path)[0] + ".m4a"
        p2v.extract_audio(path, out)
        return out, "local"

    if kind == "bili":
        from bili_audio import bili_audio
        if progress:
            progress(tr("正在取 B 站音轨…"))
        return bili_audio(src, out_path=out_path, timeout=timeout // 10), "bili"

    # 其它平台：yt-dlp
    if not ytdlp_ready():
        raise SourceError(tr("这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载"))
    if progress:
        progress(tr("正在解析链接…"))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) if out_path else HERE,
                exist_ok=True)
    stem = out_path or os.path.join(HERE, "bgm.m4a")
    base = os.path.splitext(stem)[0]
    args = [
        "-f", "bestaudio/best", "--no-playlist", "--no-warnings",
        "--extract-audio", "--audio-format", "m4a", "--audio-quality", "0",
        "-o", base + ".%(ext)s", src,
    ]
    _run_ytdlp(args, timeout=timeout, progress=progress)
    for cand in (base + ".m4a", base + ".m4a.part"):
        if os.path.exists(cand) and cand.endswith(".m4a"):
            return cand, "web"
    found = [os.path.join(os.path.dirname(base), n)
             for n in os.listdir(os.path.dirname(base) or ".")
             if n.startswith(os.path.basename(base) + ".")]
    if found:
        return found[0], "web"
    raise SourceError("音轨没落地（可能被站点拦了），换个链接试试")
