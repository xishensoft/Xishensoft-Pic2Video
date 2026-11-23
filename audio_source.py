# -*- coding: utf-8 -*-
"""audio_source —— 统一取音源：B站 / 其它平台（yt-dlp）/ 本地文件。

用户拍板的分工：
  B站：用仓内已验证的 bili_audio.py（纯标准库，无 Cookie，取整条音轨）；
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

import components
from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = components.tools_dir()      # 兼容旧引用；真正的下载/删除交给 components

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

def ytdlp_ready() -> bool:
    return bool(components.exe("ytdlp"))


def _run_ytdlp(args: list, timeout: int = 600, progress=None) -> str:
    """跑一次 yt-dlp（独立的 yt-dlp.exe），返回合并输出（失败抛 SourceError）。"""
    path = components.exe("ytdlp")
    if not path:
        raise SourceError(tr("还没下载音源组件"))
    cmd = [path] + args
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise SourceError(tr("找不到 yt-dlp，去设置里重新下载"))
    except subprocess.TimeoutExpired:
        raise SourceError(tr("解析超时（网络太慢或站点限流），稍后再试"))
    out = (p.stdout or "") + "\n" + (p.stderr or "")
    if p.returncode != 0:
        raise SourceError(_friendly(out), detail=out[-2000:])
    return out


def ytdlp_version() -> str:
    return components.version("ytdlp") if ytdlp_ready() else ""


def install_ytdlp(progress=None) -> str:
    """下载 yt-dlp.exe 到 tools/（官方单文件版，不污染系统环境）。返回版本号。"""
    def note(text, pct=0.0):
        if progress:
            progress(text if pct <= 0 or pct >= 0.99
                     else "%s %d%%" % (text, int(pct * 100)))

    try:
        ver = components.install("ytdlp", progress=note)
    except components.ComponentError as exc:
        raise SourceError(exc.message, detail=exc.detail)
    if progress:
        progress(tr("音源组件已就绪：{v}", v=ver) if ver else tr("音源组件已就绪"))
    return ver


def remove_ytdlp() -> None:
    components.remove("ytdlp")


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

    source: B站链接/BV 号、其它平台链接、或本地音视频路径
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
            progress(tr("正在取 B站音轨…"))
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
