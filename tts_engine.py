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
               "note": "要自己的 API key；可多说话人；不给时间戳，字幕按 Edge 节奏对齐"},
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
# 旁白是单声道，96k 的 AAC 够用；别用 192k —— ffmpeg 9.x 对单声道 192k CBR 会卡死在半个帧上。
TTS_BITRATE = "96k"
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
    low = str(body or "").lower()
    if code == 429 or "resource_exhausted" in low:
        return TtsError("quota", body or "429")
    # Gemini 的「key 不对」是 400 + API_KEY_INVALID，不能当成文稿问题
    if (code in (401, 403) or "api key not valid" in low
            or "api_key_invalid" in low or "unauthenticated" in low):
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


# ---------------------------------------------------------------- 对话模式
DIALOGUE_SPEAKERS = ("主讲", "听友")
DIALOGUE_GAP = 0.35        # 轮与轮之间的停顿（比普通段间 0.6 秒短）
_SPEAKER_RE = re.compile(r"^\s*(主讲|听友)\s*[：:]\s*(.*)$")


def parse_dialogue(text: str) -> list:
    """把「主讲：…」「听友：…」逐轮的文稿拆成 [{"speaker","text"}]。

    没有轮次标记就返回空列表（调用方按单人处理）。第一句标记之前的文字
    当作旁白忽略掉，避免把标题、说明也念出来。
    """
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    turns = []
    speaker = ""
    buf = []
    for raw in text.split("\n"):
        line = raw.strip()
        hit = _SPEAKER_RE.match(line)
        if hit:
            if speaker and "".join(buf).strip():
                turns.append({"speaker": speaker, "text": " ".join(
                    x for x in buf if x).strip()})
            speaker = hit.group(1)
            buf = [hit.group(2).strip()]
            continue
        if speaker:
            buf.append(line)
    if speaker and "".join(buf).strip():
        turns.append({"speaker": speaker, "text": " ".join(
            x for x in buf if x).strip()})
    return [t for t in turns if t["text"]]


def dialogue_plan(text: str, provider: str = "edge", limit: int = 0) -> list:
    """对话稿的合成计划：一轮一条（太长再按句子拆），带 speaker。"""
    turns = parse_dialogue(text)
    if not turns:
        return []
    prov = TTS_PROVIDERS.get(provider) or TTS_PROVIDERS["edge"]
    cap = int(limit or prov["chunk"])
    plan = []
    for turn in turns:
        for piece in split_script(turn["text"], limit=cap):
            plan.append({"index": len(plan), "text": piece,
                         "chars": len(piece), "speaker": turn["speaker"]})
    return plan



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


FFMPEG_TIMEOUT = 600.0   # 单次转码兜底上限（秒）；卡住就杀掉重来，别让整条流程挂死


def run_ffmpeg(args, timeout: float = FFMPEG_TIMEOUT) -> bool:
    prepend_tools()
    cmd = [_tool("ffmpeg", "PIC2VIDEO_FFMPEG"), "-nostdin", "-hide_banner", "-loglevel", "error"] + list(args)
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=timeout or None,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def run_ffmpeg_retry(args, timeout: float = FFMPEG_TIMEOUT, tries: int = 2) -> bool:
    """偶尔会碰上 ffmpeg 卡在某个输入上不动；杀掉重来一次通常就好了。"""
    for i in range(max(1, int(tries))):
        if run_ffmpeg(args, timeout):
            return True
    return False


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
        ok = run_ffmpeg_retry(["-y", "-i", paths[0], "-vn",
                               "-af", "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=mono",
                               "-c:a", "aac", "-b:a", TTS_BITRATE,
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
             "-c:a", "aac", "-b:a", TTS_BITRATE, "-ar", "48000", "-ac", "1", out_path]
    if not run_ffmpeg_retry(args):
        raise TtsError("provider", "合并音频失败")
    return out_path




LOUDNESS_TARGET = -14.0
CLEAN_THRESHOLD_DB = -45.0
CLEAN_SILENCE = 0.12


def _ffmpeg_stderr(args) -> tuple:
    """跑一次 ffmpeg，返回 (成败, stderr 文本)；清理/测量要用 stderr 里的数字。"""
    prepend_tools()
    cmd = [_tool("ffmpeg", "PIC2VIDEO_FFMPEG"), "-nostdin", "-hide_banner"] + list(args)
    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


def silence_lead(path: str, threshold: float = CLEAN_THRESHOLD_DB) -> float:
    """开头那段静音有多长（秒）；没有就是 0。"""
    ok, text = _ffmpeg_stderr(["-i", str(path), "-af",
                               "silencedetect=noise=%sdB:d=0.06" % int(threshold),
                               "-f", "null", "-"])
    if not ok:
        return 0.0
    starts, ends = [], []
    for line in text.splitlines():
        line = line.strip()
        if "silence_start:" in line:
            try:
                starts.append(float(line.split("silence_start:")[1].split()[0]))
            except (ValueError, IndexError):
                pass
        elif "silence_end:" in line:
            try:
                ends.append(float(line.split("silence_end:")[1].split()[0]))
            except (ValueError, IndexError):
                pass
    if starts and starts[0] <= 0.02 and ends:
        return max(0.0, ends[0])
    return 0.0


def measure_loudness(path: str) -> dict:
    """量一遍响度（EBU R128）；量不出来回空字典。"""
    ok, text = _ffmpeg_stderr(["-i", str(path), "-af", "loudnorm=print_format=json",
                               "-f", "null", "-"])
    if not ok:
        return {}
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        got = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    out = {}
    for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        try:
            out[k] = float(got.get(k))
        except (TypeError, ValueError):
            return {}
    return out


def clean_chain(measured: dict = None, target: float = LOUDNESS_TARGET) -> str:
    """去咔哒 / 去噗声 / 剪首尾静音 / 响度拉到 target LUFS / 兜底限幅。"""
    parts = [
        "silenceremove=start_periods=1:start_threshold=%sdB:start_silence=0.0:"
        "stop_periods=-1:stop_threshold=%sdB:stop_silence=%.2f"
        % (int(CLEAN_THRESHOLD_DB), int(CLEAN_THRESHOLD_DB), CLEAN_SILENCE),
        "adeclick=w=30:o=75",
        "highpass=f=65",
        "lowpass=f=16000",
    ]
    ln = ("loudnorm=I=%.1f:TP=-1.5:LRA=11" % float(target))
    if measured:
        ln += (":measured_I=%.2f:measured_TP=%.2f:measured_LRA=%.2f"
               ":measured_thresh=%.2f:offset=%.2f:linear=true"
               % (measured["input_i"], measured["input_tp"], measured["input_lra"],
                  measured["input_thresh"], measured["target_offset"]))
    parts.append(ln)
    parts.append("alimiter=limit=0.891")
    parts.append("aresample=48000")
    return ",".join(parts)


def shift_cues(cues, by: float, total: float = 0.0):
    """整条字幕往前挪 by 秒（剪掉开头静音时用），挪没了就丢。"""
    out = []
    try:
        by = float(by)
    except (TypeError, ValueError):
        by = 0.0
    if by <= 0:
        return normalize_cues(cues, total)
    for c in cues or []:
        try:
            st = float(c.get("start", 0.0)) - by
            en = float(c.get("end", st)) - by
        except (TypeError, ValueError):
            continue
        text = str(c.get("text", ""))
        if en <= 0.0 or not text:
            continue
        out.append({"start": round(max(0.0, st), 3), "end": round(max(0.02, en), 3),
                    "text": text})
    return normalize_cues(out, total)


def clean_mix(path: str, cues=None, target: float = LOUDNESS_TARGET) -> dict:
    """把合成好的旁白过一遍清理：剪静音 + 去咔哒/噗声 + 响度归一。
    清理失败不算致命：退回原文件，把 cleaned 标成 False，别把已经合成好的旁白弄丢。"""
    path = str(path)
    dur0 = probe_duration(path)
    lead = silence_lead(path)
    measured = measure_loudness(path)
    tmp = path + ".clean.m4a"
    af = clean_chain(measured, target)
    ok = run_ffmpeg_retry(["-y", "-i", path, "-vn", "-af", af, "-c:a", "aac", "-b:a", TTS_BITRATE,
                           "-ar", "48000", "-ac", "1", tmp])
    if not ok or not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"audio": path, "dur": dur0, "lead": 0.0, "cleaned": False,
                "loudness": measured.get("input_i", 0.0), "cues": normalize_cues(cues, dur0)}
    try:
        os.replace(tmp, path)
    except OSError:
        return {"audio": path, "dur": dur0, "lead": 0.0, "cleaned": False,
                "loudness": measured.get("input_i", 0.0), "cues": normalize_cues(cues, dur0)}
    dur = probe_duration(path) or max(0.0, dur0 - lead)
    after = measure_loudness(path)
    shifted = shift_cues(cues, lead, dur)
    return {"audio": path, "dur": dur, "lead": lead, "cleaned": True,
            "loudness": after.get("input_i", 0.0), "cues": shifted}


# ---------------------------------------------------------------- 整篇流程
def synth_for(provider: str):
    """按提供方取合成函数；还没接的给出明确错误。"""
    table = {
        "edge": synth_edge,
        "gemini": synth_gemini,
        "azure": synth_azure,
        "openai": synth_openai,
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
               on_progress=None, gap: float = TTS_GAP,
               cleanup: bool = True, loudness: float = LOUDNESS_TARGET,
               voices=None, cue_prefix: bool = False,
               extra=None) -> dict:
    """按计划逐段合成（有缓存就用缓存），再拼成一条旁白 + 句级字幕。

    voices 是 {角色: 音色}，对话稿每段按 seg["speaker"] 挑音色；cue_prefix
    为真时给字幕加「主讲：」这样的前缀。
    """
    plan = list(plan or [])
    if not plan:
        raise TtsError("text", "文稿是空的")
    if cancel is not None and cancel.is_set():
        raise TtsError("cancelled", "已取消")
    fn = synth_fn
    if fn is None:
        fn = synth_for(provider)
    voice_tag = voice if not voices else (voice + "|" + "|".join(
        "%s=%s" % (k, v) for k, v in sorted((voices or {}).items())))
    key = segment_key("||".join(s["text"] for s in plan), provider, voice_tag, rate)
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
        seg_voice = (voices or {}).get(seg.get("speaker") or "", voice)
        seg_key = segment_key(text, provider, seg_voice, rate)
        got = load_segment(root, seg_key, 0, text) if root else None
        if got is None:
            out = os.path.join(work_dir, "seg_%04d.mp3" % i)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            got = fn(text, seg_voice, rate, out, api_key=api_key,
                     base_url=base_url, proxy=proxy, **(extra or {}))
            got = {"audio": got["audio"], "dur": float(got["dur"]),
                   "cues": list(got.get("cues") or [])}
            if root:
                save_segment(root, seg_key, 0, text, got["audio"], got["dur"], got["cues"])
        else:
            cached += 1
        seg_cues = got["cues"] or spread_cues(text, got["dur"])
        seg_cues = normalize_cues(seg_cues, got["dur"])
        if cue_prefix and seg.get("speaker"):
            seg_cues = [dict(c, text="%s：%s" % (seg["speaker"], c["text"]))
                        for c in seg_cues]
        for c in seg_cues:
            cues.append({"start": c["start"] + offset, "end": c["end"] + offset, "text": c["text"]})
        audio_paths.append(got["audio"])
        offset += got["dur"] + gap
        if on_progress is not None:
            try:
                on_progress(i + 1, total, cached)
            except Exception:  # noqa: BLE001
                pass
    # 合成结果固定放在 tts/ 根下，名字一眼看得懂，别埋进按内容起的哈希目录里；
    # 文件名再带一小段内容指纹：两个工程各自合成旁白时不会互相覆盖。
    os.makedirs(cache_dir(root), exist_ok=True)
    out_path = os.path.join(cache_dir(root), "narration_%s.m4a" % key[:10])
    merge_segments(audio_paths, out_path, gap=gap)
    total_dur = probe_duration(out_path) or max(0.0, offset - gap)
    cues = normalize_cues(cues, total_dur)
    cleaned = False
    loudness_in = 0.0
    if cleanup:
        if cancel is not None and cancel.is_set():
            raise TtsError("cancelled", "已取消")
        got = clean_mix(out_path, cues, loudness)
        cues = got["cues"]
        total_dur = got["dur"] or total_dur
        cleaned = bool(got.get("cleaned"))
        loudness_in = float(got.get("loudness") or 0.0)
    return {"audio": out_path, "dur": total_dur, "cues": cues,
            "segments": total, "cached": cached, "key": key,
            "cleaned": cleaned, "loudness": loudness_in}

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
               api_key: str = "", base_url: str = "", **kwargs) -> dict:
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


# ---------------------------------------------------------------- Gemini 合成
GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-3.8-flash-tts"
GEMINI_DEFAULT_VOICE = "Kore"
GEMINI_STYLE = "warm and friendly, natural narration"
GEMINI_RETRY_DELAYS = (2.0, 4.0, 8.0)     # 429 时等这么久再试

GEMINI_VOICES = [
    ("Kore", "Kore · 女声，温柔"),
    ("Puck", "Puck · 男声，活泼"),
    ("Aoede", "Aoede · 女声，轻快"),
    ("Charon", "Charon · 男声，低沉"),
    ("Fenrir", "Fenrir · 男声，有力"),
    ("Leda", "Leda · 女声，年轻"),
]


def gemini_part(text: str, style: str = "", speaker: str = "") -> dict:
    """一个 part：text + annotations 里的 speech_metadata（字段名是下划线）。"""
    meta = {"type": "speech_metadata"}
    if style:
        meta["style"] = style
    if speaker:
        meta["speaker"] = speaker
    return {"type": "text", "text": str(text or ""), "annotations": [meta]}


def gemini_body(text: str, voice: str = "", style: str = GEMINI_STYLE,
                speakers=None, parts=None) -> dict:
    """拼 /v1beta/interactions 的请求体。

    speakers 给了就是多说话人：内容用 parts（每段带 speaker），speech_config 变成
    {"speakers": [...]}；否则单说话人，speech_config 是数组 [{"voice":...}]。
    """
    content = []
    if speakers:
        by_name = {str(s.get("speaker", "")): str(s.get("voice", "")) for s in speakers}
        for piece in (parts or [{"text": text, "speaker": speakers[0].get("speaker", "")}]):
            name = str(piece.get("speaker", ""))
            if name not in by_name:
                raise TtsError("text", "有台词的角色没配到音色")
            content.append(gemini_part(piece.get("text", ""), style, name))
        speech_config = {"speakers": [{"speaker": s.get("speaker", ""),
                                       "voice": s.get("voice", "")} for s in speakers]}
    else:
        content.append(gemini_part(text, style))
        speech_config = [{"voice": str(voice or GEMINI_DEFAULT_VOICE)}]
    return {
        "model": GEMINI_MODEL,
        "input": [{"type": "user_input", "content": content}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": speech_config},
    }


def pcm_wav_bytes(data, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    """裸 PCM 补个 RIFF 头（3.1 及更早的 unary 默认给 audio/l16）。"""
    import struct
    n = len(data)
    head = b"RIFF" + struct.pack("<I", 36 + n) + b"WAVEfmt "
    head += struct.pack("<IHHIIHH", 16, 1, max(1, channels), rate,
                        rate * max(1, channels) * max(1, width),
                        max(1, channels) * max(1, width), max(1, width) * 8)
    head += b"data" + struct.pack("<I", n)
    return head + bytes(data)


def _walk_audio(obj, out: list) -> None:
    """在返回 JSON 里找 base64 音频：interactions 是 steps[].content[].data，
    老 generateContent 是 candidates[].content.parts[].inlineData。"""
    if isinstance(obj, dict):
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            out.append((inline["data"], inline.get("mimeType")
                        or inline.get("mime_type") or "audio/wav"))
            return
        data = obj.get("data")
        if isinstance(data, str) and data:
            mime = obj.get("mime_type") or obj.get("mimeType") or ""
            if "audio" in str(mime):
                out.append((data, mime))
                return
        for v in obj.values():
            _walk_audio(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_audio(v, out)


def _gemini_audio(obj) -> tuple:
    found = []
    _walk_audio(obj, found)
    if not found:
        raise TtsError("provider", "服务商没返回音频")
    import base64
    data, mime = found[0]
    try:
        raw = base64.b64decode(data)
    except Exception as exc:  # noqa: BLE001
        raise TtsError("provider", "返回的音频解不开：%s" % exc) from exc
    if not raw:
        raise TtsError("provider", "返回的音频是空的")
    return raw, str(mime or "")


def _post_json(url: str, payload: dict, api_key: str, proxy: str = "",
               timeout: float = 180.0, headers=None) -> dict:
    import urllib.error
    import urllib.request
    head = {"Content-Type": "application/json"}
    if api_key:
        head["x-goog-api-key"] = api_key
    head.update(headers or {})
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=head, method="POST")
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read()[:600].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        raise classify_http(int(getattr(exc, "code", 0) or 0), detail) from exc
    except (TimeoutError, OSError) as exc:
        raise TtsError("network", str(exc) or "连不上") from exc
    try:
        return json.loads(raw.decode("utf-8", "replace") or "{}")
    except ValueError as exc:
        raise TtsError("provider", "返回的不是 JSON：%s" % exc) from exc


def _sleep_cancel(sec: float, cancel=None) -> None:
    """等一会儿，但每 0.25 秒看一眼有没有被取消。"""
    end = time.time() + max(0.0, float(sec))
    while time.time() < end:
        if cancel is not None and cancel.is_set():
            raise TtsError("cancelled", "已取消")
        time.sleep(min(0.25, max(0.0, end - time.time())))


def _audio_to_mp3(raw: bytes, mime: str, out_path: str) -> None:
    """把服务商给的音频（wav / 裸 PCM / mp3）转成统一的单声道 mp3。"""
    mime = str(mime or "").lower()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    if "l16" in mime or "pcm" in mime:
        raw = pcm_wav_bytes(raw, 24000)
        ext = ".wav"
    elif "wav" in mime or raw[:4] == b"RIFF":
        ext = ".wav"
    elif "mpeg" in mime or "mp3" in mime or raw[:3] == b"ID3":
        ext = ".mp3"
    else:
        ext = ".wav" if raw[:4] == b"RIFF" else ".mp3"
    tmp = out_path + ".src" + ext
    with open(tmp, "wb") as fh:
        fh.write(raw)
    try:
        ok = run_ffmpeg_retry(["-y", "-nostdin", "-hide_banner", "-loglevel", "error",
                               "-i", tmp, "-vn", "-c:a", "libmp3lame",
                               "-b:a", TTS_BITRATE, "-ar", "48000", "-ac", "1",
                               out_path])
        if not ok:
            raise TtsError("provider", "音频转码失败")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def scale_cues(cues, dur: float) -> list:
    """把一套字幕按比例缩放到实际时长（Edge 当模板时用）。"""
    cues = normalize_cues(cues, 0.0)
    span = cues_total(cues)
    if span <= 0 or dur <= 0:
        return []
    k = float(dur) / span
    return normalize_cues([{"start": c["start"] * k, "end": c["end"] * k,
                            "text": c["text"]} for c in cues], dur)


def template_cues(text: str, dur: float, proxy: str = "", cancel=None) -> list:
    """没有时间戳的提供方：用同一段文本跑一遍 Edge，拿它的断句节奏当模板。

    失败就返回空（调用方会退回按字数摊），不把配音搞挂。
    """
    plain = re.sub(r"<[^>]{1,20}>", "", str(text or ""))
    if not plain.strip() or dur <= 0:
        return []
    tmp = ""
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(prefix="p2v_tpl_", suffix=".mp3")
        os.close(fd)
        got = asyncio.run(_edge_collect(plain, "zh-CN-XiaoxiaoNeural", 0, tmp,
                                        proxy, cancel))
        cues = cue_from_words(got.get("words") or [], plain)
        return scale_cues(cues, dur)
    except Exception:  # noqa: BLE001
        return []
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def synth_gemini(text: str, voice: str = "", rate: int = 0, out_path: str = "",
                 api_key: str = "", base_url: str = "", proxy: str = "",
                 cancel=None, speakers=None, parts=None, **kwargs) -> dict:
    """Gemini TTS：/v1beta/interactions。rate 不生效（这家没有语速参数）。"""
    if not str(text or "").strip() and not parts:
        raise TtsError("text", "文稿是空的")
    if not api_key:
        raise TtsError("no-key", "Gemini 要自己的 API key")
    root = GEMINI_API_ROOT    # base_url 只属于 OpenAI 兼容端点，别串味
    body = gemini_body(text, voice, GEMINI_STYLE, speakers, parts)
    data = None
    for i, delay in enumerate(GEMINI_RETRY_DELAYS + (0.0,)):
        try:
            data = _post_json(root + "/interactions", body, api_key, proxy)
            break
        except TtsError as exc:
            if exc.reason != "quota" or i >= len(GEMINI_RETRY_DELAYS):
                raise
            _sleep_cancel(delay, cancel)
    raw, mime = _gemini_audio(data or {})
    _audio_to_mp3(raw, mime, out_path)
    dur = probe_duration(out_path)
    if dur <= 0:
        raise TtsError("provider", "生成的音频读不出时长")
    cues = template_cues(text, dur, proxy, cancel) if not speakers else []
    if not cues:
        cues = spread_cues(str(text or ""), dur)
    return {"audio": out_path, "dur": dur, "cues": cues}


# ---------------------------------------------------------------- Azure 语音
AZURE_OUTPUT_FORMAT = "audio-24khz-48kbitrate-mono-mp3"
AZURE_ENDPOINT = "https://%s.tts.speech.microsoft.com/cognitiveservices/v1"
AZURE_DEFAULT_REGION = "eastasia"
AZURE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
AZURE_RETRY_DELAYS = (1.0, 2.0, 4.0)
AZURE_MAX_CHARS = 9000       # 单请求语音上限约 10 分钟，字数再留点余量


def azure_ssml(text: str, voice: str = "", rate: int = 0,
               lang: str = "zh-CN") -> str:
    """Azure REST 只吃 SSML，把纯文本包成 <speak><voice><prosody>。"""
    from xml.sax.saxutils import escape
    body = escape(str(text or ""))
    name = str(voice or AZURE_DEFAULT_VOICE)
    pct = int(round(max(-50, min(200, int(rate or 0)))))
    rate_attr = ""
    if pct:
        rate_attr = " rate='%+d%%'" % pct
    return ("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            "xml:lang='%s'><voice name='%s'><prosody%s>%s</prosody>"
            "</voice></speak>" % (lang, name, rate_attr, body))


def _post_bytes(url: str, body: bytes, headers: dict, proxy: str = "",
                timeout: float = 180.0) -> bytes:
    """POST 一份原始字节，返回响应字节；HTTP 错误按状态码分类。"""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read()[:600].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        raise classify_http(int(getattr(exc, "code", 0) or 0), detail) from exc
    except (TimeoutError, OSError) as exc:
        raise TtsError("network", str(exc) or "连不上") from exc


def synth_azure(text: str, voice: str = "", rate: int = 0, out_path: str = "",
                api_key: str = "", base_url: str = "", proxy: str = "",
                cancel=None, region: str = "", **kwargs) -> dict:
    """Azure 语音：REST + SSML。REST 不给时间戳，字幕走 Edge 对齐模板。"""
    if not str(text or "").strip():
        raise TtsError("text", "文稿是空的")
    if not api_key:
        raise TtsError("no-key", "Azure 语音要自己的 key（还有区域）")
    if len(str(text)) > AZURE_MAX_CHARS:
        raise TtsError("text", "这一段太长，Azure 单次最多约 %d 字" % AZURE_MAX_CHARS)
    region = str(region or "").strip() or AZURE_DEFAULT_REGION
    url = AZURE_ENDPOINT % region    # base_url 只属于 OpenAI 兼容端点，别串味
    body = azure_ssml(text, voice, rate).encode("utf-8")
    head = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": AZURE_OUTPUT_FORMAT,
        "User-Agent": "Pic2Video",
    }
    raw = b""
    for i, delay in enumerate(AZURE_RETRY_DELAYS + (0.0,)):
        try:
            raw = _post_bytes(url, body, head, proxy)
            break
        except TtsError as exc:
            if exc.reason != "quota" or i >= len(AZURE_RETRY_DELAYS):
                raise
            _sleep_cancel(delay, cancel)
    if not raw:
        raise TtsError("provider", "Azure 没返回音频")
    _audio_to_mp3(raw, "audio/mpeg", out_path)
    dur = probe_duration(out_path)
    if dur <= 0:
        raise TtsError("provider", "生成的音频读不出时长")
    cues = template_cues(text, dur, proxy, cancel) or spread_cues(text, dur)
    return {"audio": out_path, "dur": dur, "cues": cues}


# ---------------------------------------------------------------- OpenAI 兼容端点
OPENAI_ROOT = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "tts-1"
OPENAI_DEFAULT_VOICE = "alloy"
OPENAI_RETRY_DELAYS = (1.0, 2.0, 4.0)


def openai_body(text: str, voice: str = "", model: str = "",
                rate: int = 0) -> dict:
    """OpenAI /v1/audio/speech 的请求体；语速折算成 speed（0.25～4.0）。"""
    body = {
        "model": str(model or "").strip() or OPENAI_DEFAULT_MODEL,
        "input": str(text or ""),
        "voice": str(voice or "").strip() or OPENAI_DEFAULT_VOICE,
        "response_format": "mp3",
    }
    if int(rate or 0):
        body["speed"] = round(min(4.0, max(0.25, 1.0 + int(rate) / 100.0)), 3)
    return body


def synth_openai(text: str, voice: str = "", rate: int = 0, out_path: str = "",
                 api_key: str = "", base_url: str = "", proxy: str = "",
                 cancel=None, model: str = "", **kwargs) -> dict:
    """OpenAI 形状的 /audio/speech：OpenAI 官方、硅基流动、火山兼容层都吃这套。"""
    if not str(text or "").strip():
        raise TtsError("text", "文稿是空的")
    if not api_key:
        raise TtsError("no-key", "这家要自己的 key")
    root = str(base_url or "").strip().rstrip("/") or OPENAI_ROOT
    url = root + "/audio/speech"
    payload = json.dumps(openai_body(text, voice, model, rate),
                         ensure_ascii=False).encode("utf-8")
    head = {"Authorization": "Bearer " + api_key,
            "Content-Type": "application/json"}
    raw = b""
    for i, delay in enumerate(OPENAI_RETRY_DELAYS + (0.0,)):
        try:
            raw = _post_bytes(url, payload, head, proxy)
            break
        except TtsError as exc:
            if exc.reason != "quota" or i >= len(OPENAI_RETRY_DELAYS):
                raise
            _sleep_cancel(delay, cancel)
    if not raw:
        raise TtsError("provider", "接口没返回音频")
    _audio_to_mp3(raw, "audio/mpeg", out_path)
    dur = probe_duration(out_path)
    if dur <= 0:
        raise TtsError("provider", "生成的音频读不出时长")
    cues = template_cues(text, dur, proxy, cancel) or spread_cues(text, dur)
    return {"audio": out_path, "dur": dur, "cues": cues}

# ---------------------------------------------------------------- 音色表
TTS_VOICES = {
    "edge": list(EDGE_ZH_VOICES),
    "gemini": list(GEMINI_VOICES),
    "azure": [("zh-CN-XiaoxiaoNeural", "晓晓（女声）"),
              ("zh-CN-YunxiNeural", "云希（男声）"),
              ("zh-CN-YunyangNeural", "云扬（男声）"),
              ("zh-CN-XiaoyiNeural", "晓伊（女声）")],
    "openai": [("alloy", "alloy"), ("nova", "nova"),
               ("shimmer", "shimmer"), ("echo", "echo"),
               ("onyx", "onyx"), ("fable", "fable")],
}
