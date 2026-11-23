# -*- coding: utf-8 -*-
"""配音引擎：文稿分段 → TTS 合成 → 句级时间戳 → 分段缓存 → 合并成一条旁白。

不碰界面，也不改工程；界面（配音页）和工程字段由 pic2video.py 负责。
四家提供方：Edge（免费、带时间戳）、Gemini、Azure、OpenAI 兼容端点。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import components

# ---------------------------------------------------------------- 提供方
# needs_key：要不要用户自己填 key；timestamps：能不能给出每句的起止时间。
TTS_PROVIDERS = {
    "edge": {"label": "Edge（免费）", "needs_key": False, "key_name": "",
             "timestamps": True, "chunk": 1000,
             "note": "微软 Edge 朗读接口，不用 key，中文音色多、带时间戳"},
    "gemini": {"label": "Gemini", "needs_key": True, "key_name": "gemini_key",
               "timestamps": False, "chunk": 2800,
               "note": "要自己的 API key；可多说话人；不给时间戳，字幕按字数摊"},
    "azure": {"label": "Azure 语音", "needs_key": True, "key_name": "azure_key",
              "timestamps": False, "chunk": 1500,
              "note": "要 key 和区域；Edge 的正式版；免费层每月 50 万字"},
    "openai": {"label": "OpenAI 兼容端点", "needs_key": True, "key_name": "openai_key",
               "timestamps": False, "chunk": 2000,
               "note": "填 base_url 就能接硅基流动 / 火山 / MiniMax 的兼容层"},
}
TTS_PROVIDER_ORDER = ("edge", "gemini", "azure", "openai")

EDGE_ZH_VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声，温柔"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声，活泼"),
    ("zh-CN-YunjianNeural", "云健 · 男声，浑厚"),
    ("zh-CN-YunxiNeural", "云希 · 男声，年轻"),
    ("zh-CN-YunxiaNeural", "云夏 · 男声，少年"),
    ("zh-CN-YunyangNeural", "云扬 · 男声，播音"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 女声，东北话"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 女声，陕西话"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 女声，台湾"),
    ("zh-TW-YunJheNeural", "雲哲 · 男声，台湾"),
    ("zh-TW-HsiaoYuNeural", "曉雨 · 女声，台湾"),
    ("zh-HK-HiuGaaiNeural", "曉佳 · 女声，粤语"),
    ("zh-HK-HiuMaanNeural", "曉曼 · 女声，粤语"),
    ("zh-HK-WanLungNeural", "雲龍 · 男声，粤语"),
]

TTS_GAP = 0.6            # 段与段之间的停顿（秒）
CUE_MAX_CHARS = 18       # 一行字幕最多几个字
CUE_MAX_LINES = 2        # 一条字幕最多几行
TTS_MIN_RATE, TTS_MAX_RATE = -50, 100   # 语速百分比

_END_PUNCT = "。！？!?…"
_SOFT_PUNCT = "，、；,;：: "


# ---------------------------------------------------------------- 错误
class TtsError(Exception):
    """配音链路上的可预期错误。reason 决定界面上给什么提示。"""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason
        self.message = message or reason

    def __str__(self) -> str:
        return self.message


def tts_error_text(exc) -> str:
    """把异常翻成人话（界面直接用）。"""
    reason = getattr(exc, "reason", "")
    detail = str(exc or "").strip()
    table = {
        "no-key": "还没填这个服务商的 key：去「设置 → 配音」里填一下，或者改用免费的 Edge。",
        "auth": "key 被服务商拒了：检查 key 有没有抄错、过期，或者有没有开这个服务。",
        "quota": "服务商说额度用完了（429）：过一会儿再试，或者把文稿拆短一点、换免费的 Edge。",
        "network": "连不上服务商：检查网络或代理，然后再试一次。",
        "text": "这段文稿合成不了：可能有服务商不认的字符，改一改或者拆短点。",
        "cancelled": "已经取消了。",
        "provider": "这家配音服务出了点问题：稍后再试，或者换一家。",
    }
    return table.get(reason, "配音出错了：") + (("（%s）" % detail) if detail and reason not in table else "")


def classify_http(code: int, body: str = "") -> "TtsError":
    if code == 429:
        return TtsError("quota", body or "429")
    if code in (401, 403):
        return TtsError("auth", body or str(code))
    if code == 400:
        return TtsError("text", body or "400")
    return TtsError("network", body or str(code))


def classify_exception(exc) -> "TtsError":
    if isinstance(exc, TtsError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return TtsError("network", str(exc) or "timeout")
    if isinstance(exc, OSError):
        return TtsError("network", str(exc) or "os error")
    return TtsError("provider", "%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------- 切句 / 分段
def _is_punct(ch: str) -> bool:
    return ch in _END_PUNCT


def split_sentences(text: str, max_len: int = 120) -> list:
    """按句号/问号/感叹号/省略号切句；超长的句子按逗号再切，最后硬切。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    sents = []
    buf = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        end_here = False
        if _is_punct(ch):
            end_here = True
            # …… 这种连着的标点算一处
            while i + 1 < n and _is_punct(text[i + 1]) and text[i + 1] == ch:
                i += 1
                buf.append(text[i])
            # 收尾的引号/括号跟着这一句
            while i + 1 < n and text[i + 1] in "”’」』）)】》":
                i += 1
                buf.append(text[i])
        elif ch == "." and (i + 1 >= n or text[i + 1] == " " or text[i + 1] == "\n"):
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if not (prev.isdigit() and nxt.isdigit()):
                end_here = True
        elif ch == "\n":
            # 换行也算句子结束（段落标题之类）
            end_here = len("".join(buf).strip()) > 0
        if end_here:
            piece = "".join(buf).strip()
            if piece:
                sents.append(piece)
            buf = []
        i += 1
    tail = "".join(buf).strip()
    if tail:
        sents.append(tail)
    out = []
    for s in sents:
        out.extend(_split_long(s, max_len))
    return out


def _split_long(sentence: str, max_len: int) -> list:
    if max_len <= 0 or len(sentence) <= max_len:
        return [sentence]
    out = []
    rest = sentence
    while len(rest) > max_len:
        window = rest[:max_len]
        cut = -1
        for j in range(len(window) - 1, max(0, len(window) // 3) - 1, -1):
            if window[j] in _SOFT_PUNCT:
                cut = j + 1
                break
        if cut <= 0:
            cut = max_len
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return [s for s in out if s]


def split_script(text: str, limit: int = 1000) -> list:
    """把整篇文稿切成 <= limit 字的若干段；段落优先，句子其次。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    limit = max(8, int(limit))
    chunks = []
    cur = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        for sent in split_sentences(para, max_len=limit):
            if not sent:
                continue
            sep = "" if not cur else ("\n" if cur.endswith(("。", "！", "？", "…", "!", "?", ".")) else "")
            candidate = cur + sep + sent if cur else sent
            if len(candidate) <= limit:
                cur = candidate
            else:
                if cur:
                    chunks.append(cur)
                cur = sent
    if cur:
        chunks.append(cur)
    return chunks


def make_plan(text: str, provider: str = "edge") -> list:
    """合成计划：一段一条，带序号和字数。"""
    prov = TTS_PROVIDERS.get(provider) or TTS_PROVIDERS["edge"]
    chunks = split_script(text, limit=int(prov["chunk"]))
    return [{"index": i, "text": c, "chars": len(c)} for i, c in enumerate(chunks)]


# ---------------------------------------------------------------- 折行 / 长句拆字幕
def fold_lines(text: str, max_chars: int = CUE_MAX_CHARS,
               max_lines: int = CUE_MAX_LINES) -> list:
    """把一句字幕按宽度折行（中文按字，英文按词）。"""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    max_chars = max(4, int(max_chars))
    lines = []
    rest = text
    while rest and len(lines) < max_lines:
        if len(rest) <= max_chars:
            lines.append(rest)
            rest = ""
            break
        window = rest[:max_chars]
        cut = -1
        for j in range(len(window) - 1, 0, -1):
            if window[j] == " ":
                cut = j
                break
        if cut <= 0:
            cut = max_chars
        lines.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [ln for ln in lines if ln]


def split_long_cue(text: str, max_chars: int = CUE_MAX_CHARS,
                   max_lines: int = CUE_MAX_LINES) -> list:
    """一句话太长（超过 行数×每行字数）时拆成几条字幕。"""
    budget = max(8, int(max_chars) * int(max_lines))
    return [s for s in split_sentences(text, max_len=budget) if s]


# ---------------------------------------------------------------- 时间戳
def _norm_chars(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", str(text or "")).lower()


def cue_from_words(words, text: str, offset: float = 0.0) -> list:
    """把词级边界（WordBoundary 之类）按原句归并成句级字幕。"""
    sents = split_sentences(text, max_len=100000) or ([text.strip()] if str(text).strip() else [])
    if not sents:
        return []
    norms = [_norm_chars(s) for s in sents]
    cues = []
    si = 0
    buf = 0
    cur = None
    for w in words or []:
        try:
            st = float(w.get("start", 0.0))
            en = float(w.get("end", st))
        except (TypeError, ValueError):
            continue
        if en <= st or si >= len(sents):
            continue
        if cur is None:
            cur = [st, en]
        else:
            cur[1] = max(cur[1], en)
        buf += max(1, len(_norm_chars(w.get("text", ""))))
        if buf >= len(norms[si]) * 0.92:
            cues.append({"start": cur[0] + offset, "end": cur[1] + offset, "text": sents[si]})
            si += 1
            cur = None
            buf = 0
    if cur is not None and si < len(sents):
        cues.append({"start": cur[0] + offset, "end": cur[1] + offset, "text": sents[si]})
    return cues


def normalize_cues(cues, total: float = 0.0) -> list:
    """排序、夹住越界、去掉倒着的、把重叠让开。"""
    out = []
    for c in cues or []:
        if not isinstance(c, dict):
            continue
        try:
            st = float(c.get("start", -1))
            en = float(c.get("end", -1))
        except (TypeError, ValueError):
            continue
        txt = str(c.get("text", "")).strip()
        st = max(0.0, st)
        if not txt or en <= st:
            continue
        if total and total > 0:
            if st >= total:
                continue
            en = min(en, total)
            if en <= st:
                continue
        out.append({"start": st, "end": en, "text": txt})
    out.sort(key=lambda c: (c["start"], c["end"]))
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = out[i + 1]["start"]
    out = [c for c in out if c["end"] > c["start"] + 0.01]
    return out


def cues_total(cues) -> float:
    return max([float(c.get("end", 0.0)) for c in cues or []] or [0.0])


def _srt_time(t: float) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000)) % 1000
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def cues_srt(cues) -> str:
    lines = []
    for i, c in enumerate(cues or [], 1):
        lines.append(str(i))
        lines.append("%s --> %s" % (_srt_time(c["start"]), _srt_time(c["end"])))
        lines.extend(fold_lines(c.get("text", "")) or [str(c.get("text", ""))])
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 缓存（断点续）
def _data_root(root: str = "") -> str:
    return root or components.data_dir()


def cache_dir(root: str = "") -> str:
    return os.path.join(_data_root(root), "tts")


def segment_key(text: str, provider: str, voice: str, rate: int) -> str:
    raw = json.dumps([str(text), str(provider), str(voice), int(rate)], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _seg_paths(root: str, key: str, index: int) -> tuple:
    folder = os.path.join(cache_dir(root), key)
    return (os.path.join(folder, "seg_%04d.mp3" % int(index)),
            os.path.join(folder, "seg_%04d.json" % int(index)))


def save_segment(root: str, key: str, index: int, text: str, audio: str,
                 dur: float, cues) -> dict:
    mp3, meta = _seg_paths(root, key, index)
    os.makedirs(os.path.dirname(mp3), exist_ok=True)
    if os.path.abspath(audio) != os.path.abspath(mp3):
        shutil.copyfile(audio, mp3)
    data = {"text": hashlib.sha1(str(text).encode("utf-8")).hexdigest(),
            "dur": float(dur), "cues": list(cues or []), "created": time.time()}
    with open(meta, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    return {"audio": mp3, "dur": float(dur), "cues": list(cues or [])}


def load_segment(root: str, key: str, index: int, text: str):
    mp3, meta = _seg_paths(root, key, index)
    if not (os.path.exists(mp3) and os.path.exists(meta)):
        return None
    try:
        with open(meta, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if data.get("text") != hashlib.sha1(str(text).encode("utf-8")).hexdigest():
        return None
    try:
        dur = float(data.get("dur", 0.0))
    except (TypeError, ValueError):
        return None
    if dur <= 0:
        return None
    cues = normalize_cues(data.get("cues") or [], dur)
    return {"audio": mp3, "dur": dur, "cues": cues}


def clear_cache(root: str, key: str) -> bool:
    folder = os.path.join(cache_dir(root), key)
    if not os.path.isdir(folder):
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


# ---------------------------------------------------------------- 外部工具
def prepend_tools() -> None:
    try:
        components.prepend_path()
    except Exception:  # noqa: BLE001
        pass


def _tool(name: str, env: str) -> str:
    value = os.environ.get(env, "")
    if value:
        return value
    local = os.path.join(HERE, "tools", name + ".exe")
    if os.path.exists(local):
        return local
    return name


def run_ffmpeg(args) -> bool:
    prepend_tools()
    cmd = [_tool("ffmpeg", "PIC2VIDEO_FFMPEG"), "-nostdin", "-hide_banner", "-loglevel", "error"] + list(args)
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return False
    return proc.returncode == 0


def probe_duration(path: str) -> float:
    prepend_tools()
    cmd = [_tool("ffprobe", "PIC2VIDEO_FFPROBE"), "-v", "error", "-show_entries",
           "format=duration", "-of", "json", str(path)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return 0.0
    if proc.returncode != 0:
        return 0.0
    try:
        return float(json.loads(proc.stdout.decode("utf-8", "replace"))["format"]["duration"])
    except (ValueError, KeyError, TypeError):
        return 0.0


def merge_segments(paths, out_path: str, gap: float = TTS_GAP) -> str:
    """把若干段音频按顺序拼起来，中间留 gap 秒静音，输出一条 m4a。"""
    paths = [p for p in (paths or []) if p and os.path.exists(p)]
    if not paths:
        raise TtsError("text", "没有可以合并的音频")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if len(paths) == 1:
        ok = run_ffmpeg(["-y", "-i", paths[0], "-vn", "-c:a", "aac", "-b:a", "192k",
                         "-ar", "48000", "-ac", "1", out_path])
        if not ok:
            raise TtsError("provider", "合并音频失败")
        return out_path
    args = ["-y"]
    for p in paths:
        args += ["-i", p]
    n = len(paths)
    gap_inputs = n - 1
    if gap > 0:
        for _ in range(gap_inputs):
            args += ["-f", "lavfi", "-t", "%.3f" % gap, "-i", "anullsrc=r=48000:cl=mono"]
    parts = []
    for i in range(n):
        parts.append("[%d:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
                     "asetpts=N/SR/TB[a%d]" % (i, i))
    for j in range(gap_inputs):
        parts.append("[%d:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono,"
                     "asetpts=N/SR/TB[g%d]" % (n + j, j))
    streams = []
    for i in range(n):
        streams.append("[a%d]" % i)
        if i < gap_inputs:
            streams.append("[g%d]" % i)
    parts.append("%sconcat=n=%d:v=0:a=1[aout]" % ("".join(streams), n + gap_inputs))
    args += ["-filter_complex", ";".join(parts), "-map", "[aout]", "-vn",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "1", out_path]
    if not run_ffmpeg(args):
        raise TtsError("provider", "合并音频失败")
    return out_path



# ---------------------------------------------------------------- 整篇流程
def synth_for(provider: str):
    """按提供方取合成函数；还没接的给出明确错误。"""
    table = {
        "edge": synth_edge,
    }
    fn = table.get(provider)
    if fn is None:
        raise TtsError("provider", "这家配音还没接上")
    return fn


def spread_cues(text: str, dur: float, max_chars: int = CUE_MAX_CHARS,
                max_lines: int = CUE_MAX_LINES) -> list:
    """没有时间戳的提供方：按字数把时长摊给每句字幕。"""
    pieces = []
    for sent in split_sentences(text, max_len=max_chars * max_lines):
        pieces.extend(split_long_cue(sent, max_chars, max_lines))
    pieces = [x for x in pieces if x.strip()]
    if not pieces or dur <= 0:
        return []
    weights = [max(1, len(_norm_chars(x))) for x in pieces]
    total = float(sum(weights))
    cues = []
    t = 0.0
    for piece, w in zip(pieces, weights):
        span = dur * w / total
        cues.append({"start": round(t, 3), "end": round(min(dur, t + span), 3), "text": piece})
        t += span
    if cues:
        cues[-1]["end"] = round(dur, 3)
    return normalize_cues(cues, dur)



def fold_cues(cues, max_chars: int = CUE_MAX_CHARS, max_lines: int = CUE_MAX_LINES) -> list:
    """把字幕折成「每行 <= max_chars、最多 max_lines 行」；太长的句子按时间比例拆条。"""
    out = []
    for c in cues or []:
        text = str(c.get("text", "")).strip()
        try:
            st = float(c.get("start", 0.0))
            en = float(c.get("end", st))
        except (TypeError, ValueError):
            continue
        if not text or en <= st:
            continue
        pieces = split_long_cue(text, max_chars, max_lines)
        if len(pieces) <= 1:
            out.append({"start": st, "end": en,
                        "text": "\n".join(fold_lines(text, max_chars, max_lines)) or text})
            continue
        weights = [max(1, len(x)) for x in pieces]
        total = float(sum(weights))
        t = st
        span = en - st
        for i, (piece, w) in enumerate(zip(pieces, weights)):
            part = span * w / total
            end = en if i == len(pieces) - 1 else t + part
            out.append({"start": round(t, 3), "end": round(end, 3),
                        "text": "\n".join(fold_lines(piece, max_chars, max_lines)) or piece})
            t = end
    return normalize_cues(out)

def synth_plan(plan, *, provider: str = "edge", voice: str = "", rate: int = 0,
               root: str = "", api_key: str = "", base_url: str = "",
               proxy: str = "", cancel=None, synth_fn=None,
               on_progress=None, gap: float = TTS_GAP) -> dict:
    """按计划逐段合成（有缓存就用缓存），再拼成一条旁白 + 句级字幕。"""
    plan = list(plan or [])
    if not plan:
        raise TtsError("text", "文稿是空的")
    if cancel is not None and cancel.is_set():
        raise TtsError("cancelled", "已取消")
    fn = synth_fn
    if fn is None:
        fn = synth_for(provider)
    key = segment_key("||".join(s["text"] for s in plan), provider, voice, rate)
    work_dir = os.path.join(cache_dir(root), key)
    audio_paths = []
    cues = []
    offset = 0.0
    cached = 0
    total = len(plan)
    for i, seg in enumerate(plan):
        if cancel is not None and cancel.is_set():
            raise TtsError("cancelled", "已取消")
        text = seg["text"]
        seg_key = segment_key(text, provider, voice, rate)
        got = load_segment(root, seg_key, 0, text) if root else None
        if got is None:
            out = os.path.join(work_dir, "seg_%04d.mp3" % i)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            got = fn(text, voice, rate, out, api_key=api_key, base_url=base_url, proxy=proxy)
            got = {"audio": got["audio"], "dur": float(got["dur"]),
                   "cues": list(got.get("cues") or [])}
            if root:
                save_segment(root, seg_key, 0, text, got["audio"], got["dur"], got["cues"])
        else:
            cached += 1
        seg_cues = got["cues"] or spread_cues(text, got["dur"])
        seg_cues = normalize_cues(seg_cues, got["dur"])
        for c in seg_cues:
            cues.append({"start": c["start"] + offset, "end": c["end"] + offset, "text": c["text"]})
        audio_paths.append(got["audio"])
        offset += got["dur"] + gap
        if on_progress is not None:
            try:
                on_progress(i + 1, total, cached)
            except Exception:  # noqa: BLE001
                pass
    out_path = os.path.join(work_dir, "narration.m4a")
    merge_segments(audio_paths, out_path, gap=gap)
    total_dur = probe_duration(out_path) or max(0.0, offset - gap)
    cues = normalize_cues(cues, total_dur)
    return {"audio": out_path, "dur": total_dur, "cues": cues,
            "segments": total, "cached": cached, "key": key}

# ---------------------------------------------------------------- Edge 合成
def _rate_arg(rate) -> str:
    try:
        pct = int(rate)
    except (TypeError, ValueError):
        pct = 0
    pct = max(TTS_MIN_RATE, min(TTS_MAX_RATE, pct))
    return "%+d%%" % pct


async def _edge_collect(text: str, voice: str, rate: int, out_path: str,
                        proxy: str = "", cancel=None) -> dict:
    import edge_tts  # 只有真用的时候才 import，打包缺了也不影响其他功能

    comm = edge_tts.Communicate(text, voice, rate=_rate_arg(rate),
                                boundary="WordBoundary", proxy=proxy or None)
    chunks = []
    words = []
    async for item in comm.stream():
        if cancel is not None and cancel.is_set():
            raise TtsError("cancelled", "已取消")
        kind = item.get("type")
        if kind == "audio":
            chunks.append(item["data"])
        elif kind == "WordBoundary":
            off = float(item.get("offset", 0)) / 1.0e7
            dur = float(item.get("duration", 0)) / 1.0e7
            words.append({"text": item.get("text", ""), "start": off, "end": off + dur})
    if not chunks:
        raise TtsError("provider", "服务端没有返回音频")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "wb") as fh:
        for c in chunks:
            fh.write(c)
    return {"words": words}


def synth_edge(text: str, voice: str = "zh-CN-XiaoxiaoNeural", rate: int = 0,
               out_path: str = "", proxy: str = "", cancel=None,
               api_key: str = "", base_url: str = "") -> dict:
    """Edge 不用 key；api_key/base_url 只是为了和其它提供方同一个调用形状。"""
    if not str(text or "").strip():
        raise TtsError("text", "文稿是空的")
    try:
        got = asyncio.run(_edge_collect(text, voice or "zh-CN-XiaoxiaoNeural",
                                       rate, out_path, proxy, cancel))
    except TtsError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise classify_exception(exc) from exc
    cues = cue_from_words(got["words"], text)
    dur = cues_total(cues) or probe_duration(out_path)
    if dur <= 0:
        dur = probe_duration(out_path)
    if dur <= 0:
        raise TtsError("provider", "生成的音频读不出时长")
    cues = normalize_cues(cues, dur)
    return {"audio": out_path, "dur": dur, "cues": cues}
