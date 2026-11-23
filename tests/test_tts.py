# -*- coding: utf-8 -*-
"""配音引擎（tts_engine.py）的单元检查：分段 / 折行 / 时间戳 / 缓存 / 合并 / 错误分类。

不碰界面；只有 Edge 真合成与 ffmpeg 合并会联网/起进程，失败时记 SKIP 不算红。
"""
import json
import base64
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tts_engine as T

fails = []
skips = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + ("  " + str(extra) if extra else ""))
    if not cond:
        fails.append(name)


def skip(name, why=""):
    print("SKIP " + name + ("  " + why if why else ""))
    skips.append(name)


tmp = tempfile.mkdtemp(prefix="p2v_tts_")

# ---------------- 1 提供方注册表
check("四家都在", T.TTS_PROVIDER_ORDER == ("edge", "gemini", "azure", "openai"),
      list(T.TTS_PROVIDER_ORDER))
check("注册表齐全", all(k in T.TTS_PROVIDERS for k in T.TTS_PROVIDER_ORDER))
check("每家都有名字", all(T.TTS_PROVIDERS[k]["label"] for k in T.TTS_PROVIDER_ORDER))
check("只有 Edge 不要 key", [k for k in T.TTS_PROVIDER_ORDER if not T.TTS_PROVIDERS[k]["needs_key"]] == ["edge"])
check("每家单段上限为正", all(int(T.TTS_PROVIDERS[k]["chunk"]) > 0 for k in T.TTS_PROVIDER_ORDER))
check("Edge 有时间戳", T.TTS_PROVIDERS["edge"]["timestamps"] is True)
check("Edge 中文音色 >= 10", len(T.EDGE_ZH_VOICES) >= 10, len(T.EDGE_ZH_VOICES))
check("Edge 音色带名字", all(isinstance(v, tuple) and len(v) == 2 and v[0].endswith("Neural")
                          for v in T.EDGE_ZH_VOICES))
check("音色名不重样", len({v[0] for v in T.EDGE_ZH_VOICES}) == len(T.EDGE_ZH_VOICES))

# ---------------- 2 切句 / 分段
sents = T.split_sentences("第一句。第二句！第三句？最后一句")
check("切句四句", len(sents) == 4, sents)
check("切句保留标点", sents[0] == "第一句。", sents[0])
check("小数点不切", T.split_sentences("价格是 3.5 元。第二句。") == ["价格是 3.5 元。", "第二句。"],
      T.split_sentences("价格是 3.5 元。第二句。"))
check("省略号算一句", len(T.split_sentences("等一下……我还没说完。")) == 2)
long_one = "这是一句没有标点的特别长的中文句子" * 6
pieces = T.split_sentences(long_one, max_len=40)
check("超长句被切开", len(pieces) > 1 and all(len(s) <= 40 for s in pieces), [len(s) for s in pieces])
check("切开的拼回原文", "".join(pieces).replace(" ", "") == long_one.replace(" ", ""))

text = "第一段第一句。第一段第二句。\n\n第二段第一句。第二段第二句。"
chunks = T.split_script(text, limit=1000)
check("两个段落合成一段也行", len(chunks) >= 1 and all(len(c) <= 1000 for c in chunks))
check("段落不丢字", "".join(chunks).replace(" ", "").replace("\n", "") ==
      text.replace(" ", "").replace("\n", ""))
small = T.split_script(text, limit=10)
check("小上限切成多段", len(small) >= 4 and all(len(c) <= 10 for c in small), [len(c) for c in small])
check("空文稿没有段", T.split_script("   \n\n  ") == [])
check("计划序号连续", [s["index"] for s in T.make_plan(text, "edge")] ==
      list(range(len(T.make_plan(text, "edge")))))
check("计划带后端上限", all(len(s["text"]) <= T.TTS_PROVIDERS["gemini"]["chunk"]
                          for s in T.make_plan("啦啦啦。" * 900, "gemini")))

# ---------------- 3 折行
lines = T.fold_lines("这是一句超过十八个字的中文句子需要折成两行显示吗")
check("每行不超过 18 字", all(len(x) <= 18 for x in lines), [len(x) for x in lines])
check("最多两行", len(lines) <= 2, lines)
check("英文按词折", all(len(x) <= 18 for x in T.fold_lines("hello world this is a fairly long subtitle line")))
check("空文本没有行", T.fold_lines("") == [])
sub_cues = T.split_long_cue("这是一句超过三十六字的中文句子需要拆成多条字幕来显示否则一行放不下而且会很难看", )
check("长句拆成多条字幕", len(sub_cues) >= 2, sub_cues)
check("每条不超过两行 36 字", all(len(c) <= 36 for c in sub_cues), [len(c) for c in sub_cues])

# ---------------- 4 时间戳
words = [{"text": "你好", "start": 0.10, "end": 0.60},
         {"text": "世界", "start": 0.70, "end": 1.20},
         {"text": "再见", "start": 1.40, "end": 1.90},
         {"text": "朋友", "start": 2.00, "end": 2.50}]
cues = T.cue_from_words(words, "你好世界。再见朋友。")
check("按句出两条", len(cues) == 2, cues)
check("第一句起点", abs(cues[0]["start"] - 0.10) < 1e-6, cues[0])
check("第一句终点", abs(cues[0]["end"] - 1.20) < 1e-6, cues[0])
check("第二句终点", abs(cues[1]["end"] - 2.50) < 1e-6, cues[1])
check("句子文字不丢", cues[0]["text"] == "你好世界。" and cues[1]["text"] == "再见朋友。", cues)
off = T.cue_from_words(words, "你好世界。再见朋友。", offset=5.0)
check("整体偏移可用", abs(off[0]["start"] - 5.10) < 1e-6, off[0])
check("字数不够也有尾巴", len(T.cue_from_words(words[:1], "你好世界。再见朋友。")) == 1)

bad = [{"start": 3.0, "end": 1.0, "text": "倒着"},
       {"start": -1.0, "end": 0.5, "text": "负的"},
       {"start": 0.4, "end": 0.9, "text": "正常"},
       {"start": 0.8, "end": 1.2, "text": "重叠"},
       {"start": 5.0, "end": 6.0, "text": "超出"}]
norm = T.normalize_cues(bad, total=3.0)
check("丢掉倒着的", all(c["text"] != "倒着" for c in norm))
check("负起点夹到 0", norm[0]["start"] == 0.0, norm[0])
check("重叠被前一条让出", norm[1]["start"] >= norm[0]["end"] - 1e-6, norm)
check("超出总长丢掉", all(c["end"] <= 3.0 + 1e-6 for c in norm))
check("按时间排序", [c["start"] for c in norm] == sorted(c["start"] for c in norm))
check("cues 总长", abs(T.cues_total(norm) - norm[-1]["end"]) < 1e-6)
srtt = T.cues_srt([{"start": 1.23, "end": 3.5, "text": "第一句"}])
check("SRT 时间格式", "00:00:01,230 --> 00:00:03,500" in srtt, srtt)
check("SRT 序号与正文", srtt.startswith("1\n") and srtt.rstrip().endswith("第一句"), srtt)

# ---------------- 5 缓存
key1 = T.segment_key("你好。", "edge", "zh-CN-XiaoxiaoNeural", 0)
key2 = T.segment_key("你好。", "edge", "zh-CN-YunxiNeural", 0)
key3 = T.segment_key("你好。", "edge", "zh-CN-XiaoxiaoNeural", 15)
check("同样的输入同一个 key", key1 == T.segment_key("你好。", "edge", "zh-CN-XiaoxiaoNeural", 0))
check("换音色换 key", key1 != key2)
check("换语速换 key", key1 != key3)
check("key 是十六进制", len(key1) == 40 and all(ch in "0123456789abcdef" for ch in key1), key1)

mp3 = os.path.join(tmp, "fake.mp3")
with open(mp3, "wb") as fh:
    fh.write(b"ID3" + b"\x00" * 100)
T.save_segment(tmp, key1, 0, "你好。", mp3, 1.25, [{"start": 0.0, "end": 1.25, "text": "你好。"}])
got = T.load_segment(tmp, key1, 0, "你好。")
check("缓存能读回", got is not None and abs(got["dur"] - 1.25) < 1e-6, got)
check("缓存带 cues", got and len(got["cues"]) == 1)
check("文字变了不认旧缓存", T.load_segment(tmp, key1, 0, "你好啊。") is None)
check("没存过的段读不到", T.load_segment(tmp, key1, 9, "你好。") is None)
check("缓存路径在 root 下", T.cache_dir(tmp).startswith(tmp))
check("清缓存清得掉", T.clear_cache(tmp, key1) is True and T.load_segment(tmp, key1, 0, "你好。") is None)

# ---------------- 6 错误分类
for reason in ("no-key", "auth", "quota", "network", "text", "cancelled", "provider"):
    exc = T.TtsError(reason, "细节")
    check("错误有理由 " + reason, exc.reason == reason and T.tts_error_text(exc))
check("未知理由有兜底", T.tts_error_text(T.TtsError("别的", "")))
check("429 归配额", T.classify_http(429, "Too Many Requests").reason == "quota")
check("401 归鉴权", T.classify_http(401, "").reason == "auth")
check("403 归鉴权", T.classify_http(403, "").reason == "auth")
check("500 归网络", T.classify_http(500, "").reason == "network")
check("超时归网络", T.classify_exception(TimeoutError("timed out")).reason == "network")
check("断网归网络", T.classify_exception(OSError("getaddrinfo failed")).reason == "network")
check("取消归取消", T.classify_exception(T.TtsError("cancelled", "")).reason == "cancelled")

# ---------------- 7 合并（真 ffmpeg）
a = os.path.join(tmp, "a.mp3")
b = os.path.join(tmp, "b.mp3")
made = True
for path, freq in ((a, 440), (b, 660)):
    r = T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=%d:duration=1" % freq,
                      "-c:a", "libmp3lame", "-q:a", "5", path])
    made = made and r
if made and os.path.exists(a) and os.path.exists(b):
    merged = os.path.join(tmp, "merged.m4a")
    T.merge_segments([a, b], merged, gap=0.6)
    got = T.probe_duration(merged)
    check("合并后总长 = 两段 + 间隔", abs(got - 2.6) < 0.15, round(got, 2))
    one = os.path.join(tmp, "single.m4a")
    T.merge_segments([a], one, gap=0.6)
    check("单段不加尾巴", abs(T.probe_duration(one) - 1.0) < 0.1)
else:
    skip("合并测试", "ffmpeg 不可用")

# ---------------- 8 Edge 真合成（联网；失败记 SKIP）
if os.environ.get("P2V_SKIP_NET"):
    skip("Edge 真合成", "P2V_SKIP_NET")
else:
    try:
        out = os.path.join(tmp, "edge.mp3")
        res = T.synth_edge("你好，世界。这是第二句。", "zh-CN-XiaoxiaoNeural", 0, out)
        check("Edge 出音频", os.path.exists(out) and os.path.getsize(out) > 2000,
              os.path.getsize(out) if os.path.exists(out) else -1)
        check("Edge 报出时长", res["dur"] > 1.0, round(res["dur"], 2))
        check("Edge 出两条字幕", len(res["cues"]) == 2, res["cues"])
        check("Edge 字幕在音频内", all(0 <= c["start"] < c["end"] <= res["dur"] + 0.2 for c in res["cues"]),
              res["cues"])
        check("Edge 字幕文字对得上", res["cues"][0]["text"].startswith("你好"), res["cues"][0])
    except Exception as exc:  # noqa: BLE001
        skip("Edge 真合成", "%s: %s" % (type(exc).__name__, exc))


# ---------------- 9 整篇流程（假合成，不联网）
import threading

pipe_root = os.path.join(tmp, "pipe")
plan2 = [{"index": 0, "text": "第一句。", "chars": 4},
         {"index": 1, "text": "第二句。", "chars": 4}]
calls = {"n": 0}


def fake_synth(text, voice, rate, out, api_key="", base_url="", proxy=""):
    calls["n"] += 1
    os.makedirs(os.path.dirname(out), exist_ok=True)
    T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                  "-c:a", "libmp3lame", "-q:a", "7", out])
    return {"audio": out, "dur": 1.0, "cues": [{"start": 0.0, "end": 1.0, "text": text}]}


seen = []
res = T.synth_plan(plan2, provider="edge", voice="v1", rate=0, root=pipe_root,
                   synth_fn=fake_synth, cleanup=False, gap=0.6,
                   on_progress=lambda done, total, cached: seen.append((done, total, cached)))
check("整篇合成两段都调用", calls["n"] == 2, calls["n"])
check("整篇总长 = 两段 + 间隔", abs(res["dur"] - 2.6) < 0.2, round(res["dur"], 2))
check("整篇两条字幕", len(res["cues"]) == 2, res["cues"])
check("第二段字幕被推后", abs(res["cues"][1]["start"] - 1.6) < 0.12, res["cues"][1])
check("进度回调按顺序", [x[0] for x in seen] == [1, 2] and all(x[1] == 2 for x in seen), seen)

again = T.synth_plan(plan2, provider="edge", voice="v1", rate=0, root=pipe_root,
                     synth_fn=fake_synth, cleanup=False, gap=0.6)
check("第二次全部走缓存", calls["n"] == 2 and again["cached"] == 2, [calls["n"], again["cached"]])
check("缓存复用总长一致", abs(again["dur"] - res["dur"]) < 0.02)
check("缓存复用字幕一致", len(again["cues"]) == 2)

plan3 = [{"index": 0, "text": "第一句。", "chars": 4},
         {"index": 1, "text": "换了一句。", "chars": 5}]
T.synth_plan(plan3, provider="edge", voice="v1", rate=0, root=pipe_root,
             synth_fn=fake_synth, gap=0.6)
check("只补改过的那一段", calls["n"] == 3, calls["n"])

boom = {"n": 0}


def bad_synth(text, voice, rate, out, api_key="", base_url="", proxy=""):
    boom["n"] += 1
    if boom["n"] == 2:
        raise T.TtsError("quota", "429")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                  "-c:a", "libmp3lame", "-q:a", "7", out])
    return {"audio": out, "dur": 1.0, "cues": [{"start": 0.0, "end": 1.0, "text": text}]}


try:
    T.synth_plan([{"index": 0, "text": "甲。", "chars": 2},
                  {"index": 1, "text": "乙。", "chars": 2}],
                 provider="edge", voice="v9", rate=0, root=os.path.join(tmp, "pipe2"),
                 synth_fn=bad_synth, cleanup=False, gap=0.6)
    ok = False
except T.TtsError as exc:
    ok = exc.reason == "quota"
check("中间一段失败就抛配额错误", ok)
check("失败段没有写进缓存", boom["n"] == 2)

cancel = threading.Event()
cancel.set()
try:
    T.synth_plan(plan2, provider="edge", voice="v1", rate=0, root=pipe_root,
                 synth_fn=fake_synth, cancel=cancel)
    ok = False
except T.TtsError as exc:
    ok = exc.reason == "cancelled"
check("取消能中断", ok)
check("没时间戳的提供方按时长摊字幕",
      len(T.spread_cues("第一句。第二句。第三句。", 3.0)) == 3)
check("摊出来的字幕不重叠",
      all(a["end"] <= b["start"] + 1e-6 for a, b in
          zip(T.spread_cues("第一句。第二句。第三句。", 3.0),
              T.spread_cues("第一句。第二句。第三句。", 3.0)[1:])))
try:
    T.synth_for("nope")
    ok = False
except T.TtsError as exc:
    ok = exc.reason == "provider"
check("没接的提供方给明确错误", ok)


# ---------------- 10 折行 + 跟旁白模式（工程模型）
import pic2video as p

folded = T.fold_cues([{"start": 0.0, "end": 6.0, "text": "这是" * 30}])
check("长字幕被拆条", len(folded) >= 2, len(folded))
check("每条最多两行", all(c["text"].count("\n") <= 1 for c in folded), [c["text"] for c in folded])
check("每行不超过 18 字",
      all(len(line) <= 18 for c in folded for line in c["text"].split("\n")),
      [c["text"] for c in folded])
check("折行不丢字", "".join(c["text"].replace("\n", "") for c in folded) == "这是" * 30)
check("折行后时间接得上", abs(folded[0]["start"]) < 1e-6 and abs(folded[-1]["end"] - 6.0) < 1e-6,
      (folded[0]["start"], folded[-1]["end"]))
check("折行后不重叠", all(a["end"] <= b["start"] + 1e-6 for a, b in zip(folded, folded[1:])))
check("短句不拆", len(T.fold_cues([{"start": 0, "end": 2, "text": "短句。"}])) == 1)

check("多了一种时长模式", p.MODE_LABELS.get("voice") == "跟旁白", p.MODE_LABELS)
pr = p.Project()
pr.clips = [p.Clip("a.png", 3.0), p.Clip("b.png", 3.0)]
pr.mode = "voice"
pr.narration_dur = 10.0
pr.normalize()
check("跟旁白总长 = 旁白长度", abs(pr.total() - 10.0) < 1e-6, pr.total())
check("跟旁白 target 一致", abs(pr.target() - 10.0) < 1e-6, pr.target())
before = pr.total()
pr.set_duration(0, 6.0)
check("跟旁白下改图片时长不改总长", abs(pr.total() - before) < 1e-6, (before, pr.total()))

doc = pr.to_doc()
check("工程里存了旁白", doc["narration"]["dur"] == 10.0 and doc["narration"]["path"] == "", doc["narration"])
back = p.Project.from_doc(doc)
check("旁白能读回", back.narration_dur == 10.0 and back.mode == "voice", (back.narration_dur, back.mode))
old = p.Project.from_doc({"version": p.PROJECT_VERSION, "clips": [], "audios": []})
check("老工程没有旁白字段", old.narration_dur == 0.0 and old.narration_path == "")

nar = os.path.join(tmp, "nar.m4a")
with open(nar, "wb") as fh:
    fh.write(b"\x00" * 10)
pr2 = p.Project()
pr2.clips = [p.Clip("a.png", 2.0)]
p.set_narration(pr2, nar, 5.5, [{"start": 0.1, "end": 1.0, "text": "第一句。"},
                                {"start": 1.2, "end": 2.0, "text": "第二句。"}])
check("挂旁白后进跟旁白模式", pr2.mode == "voice" and abs(pr2.narration_dur - 5.5) < 1e-6)
check("旁白变成一条音频", any(a.path == nar for a in pr2.audios), pr2.audios)
check("旁白带字幕", len(pr2.cues) == 2, pr2.cues)
check("挂旁白后总长按旁白", abs(pr2.total() - 5.5) < 1e-6, pr2.total())
idx = [i for i, a in enumerate(pr2.audios) if a.path == nar][0]
pr2.remove_audio([idx])
check("删掉旁白音频就清掉旁白", pr2.narration_dur == 0.0 and pr2.narration_path == "")

# ---------------- 11 音频清理与响度
chain = T.clean_chain()
check("清理滤镜链齐", all(k in chain for k in ("silenceremove", "adeclick", "highpass=f=65",
                                             "lowpass=f=16000", "loudnorm=I=-14.0",
                                             "alimiter", "aresample=48000")), chain)
check("清理链带上量出来的响度", "measured_I=-30.00" in T.clean_chain(
    {"input_i": -30.0, "input_tp": -12.0, "input_lra": 3.0,
     "input_thresh": -40.0, "target_offset": 0.0}))
check("目标响度可改", "loudnorm=I=-16.0" in T.clean_chain(None, -16.0))

sample = os.path.join(tmp, "tone_sil.wav")
made = T.run_ffmpeg(["-y", "-f", "lavfi", "-i",
                     "sine=frequency=440:sample_rate=48000:duration=1",
                     "-af", "adelay=600:all=1", "-t", "1.6", sample])
if not made or not os.path.exists(sample):
    skip("清理/响度实测", "这台机器上 ffmpeg 没做出测试音频")
else:
    lead = T.silence_lead(sample)
    check("量得出开头静音", abs(lead - 0.6) < 0.12, lead)
    got = T.clean_mix(sample, [{"start": 0.7, "end": 1.2, "text": "第一句。"},
                                {"start": 1.2, "end": 1.6, "text": "第二句。"}])
    check("清理跑通了", got["cleaned"] is True, got)
    check("清理后时长约 1 秒", abs(got["dur"] - 1.0) < 0.25, got["dur"])
    check("字幕跟着往前挪", bool(got["cues"]) and got["cues"][0]["start"] < 0.25, got["cues"])
    check("清理后响度 -14 上下 1.5", abs(float(got["loudness"] or 0.0) + 14.0) < 1.5, got["loudness"])

moved = T.shift_cues([{"start": 0.1, "end": 0.3, "text": "a"},
                      {"start": 0.5, "end": 1.0, "text": "b"}], 0.4, 2.0)
check("挪字幕越界就丢", [c["text"] for c in moved] == ["b"], moved)
check("挪字幕保留间隔", abs(moved[0]["start"] - 0.1) < 1e-6 and abs(moved[0]["end"] - 0.6) < 1e-6, moved)
check("不挪就原样", T.shift_cues([{"start": 0.5, "end": 1.0, "text": "b"}], 0.0, 2.0)
      == [{"start": 0.5, "end": 1.0, "text": "b"}])

broken = T.clean_mix(os.path.join(tmp, "nope.wav"), [{"start": 0, "end": 1, "text": "x"}])
check("清理失败不弄丢旁白", broken["cleaned"] is False and broken["dur"] == 0.0, broken)


def _fake_synth(text, voice, rate, out, api_key="", base_url="", proxy="", cancel=None):
    T.run_ffmpeg(["-y", "-f", "lavfi", "-i",
                  "sine=frequency=440:sample_rate=48000:duration=0.8",
                  "-af", "adelay=500:all=1", "-t", "1.3", out])
    return {"audio": out, "dur": 1.3,
            "cues": [{"start": 0.55, "end": 1.25, "text": "第一句。"}]}


plan = T.make_plan("第一句。", "edge")
raw = T.synth_plan(plan, provider="edge", voice="v1", root=os.path.join(tmp, "tts_off"),
                   synth_fn=_fake_synth, cleanup=False)
check("关掉清理就不动音频", raw["cleaned"] is False and abs(raw["dur"] - 1.3) < 0.1, raw["dur"])
check("关掉清理字幕不动", abs(raw["cues"][0]["start"] - 0.55) < 0.02, raw["cues"])
done = T.synth_plan(plan, provider="edge", voice="v1", root=os.path.join(tmp, "tts_on"),
                    synth_fn=_fake_synth, cleanup=True)
check("默认清理是开的", done["cleaned"] is True, done)
check("清理后总长变短", done["dur"] < raw["dur"] - 0.2, (raw["dur"], done["dur"]))
check("清理后字幕提前", done["cues"][0]["start"] < raw["cues"][0]["start"] - 0.2,
      (raw["cues"][0]["start"], done["cues"][0]["start"]))

# 同一个 tts/ 目录下，不同文稿的旁白不能互相覆盖
_root = os.path.join(tmp, "tts_names")
_a = T.synth_plan(T.make_plan("甲。", "edge"), provider="edge", voice="v1",
                  root=_root, synth_fn=_fake_synth, cleanup=False)
_b = T.synth_plan(T.make_plan("乙。", "edge"), provider="edge", voice="v1",
                  root=_root, synth_fn=_fake_synth, cleanup=False)
check("不同文稿的旁白分成两个文件", _a["audio"] != _b["audio"],
      (_a["audio"], _b["audio"]))
check("两份旁白都真的在", os.path.exists(_a["audio"]) and os.path.exists(_b["audio"]),
      (_a["audio"], _b["audio"]))
check("旁白文件名带内容指纹", os.path.basename(_a["audio"]).startswith("narration_"),
      os.path.basename(_a["audio"]))
_same = T.synth_plan(T.make_plan("甲。", "edge"), provider="edge", voice="v1",
                     root=_root, synth_fn=_fake_synth, cleanup=False)
check("同文稿再合一次还是同一个文件", _same["audio"] == _a["audio"],
      (_same["audio"], _a["audio"]))


# ---------------------------------------------------------------- 12 Gemini 接入
body = T.gemini_body("今天聊一聊冬天为什么容易感冒。", "Kore")
check("Gemini 请求体带模型", body.get("model") == T.GEMINI_MODEL, body.get("model"))
check("Gemini 用 interactions 的 input 结构",
      body["input"][0]["type"] == "user_input"
      and isinstance(body["input"][0]["content"], list), body.get("input"))
gem_part = body["input"][0]["content"][0]
check("Gemini 单说话人带 speech_metadata",
      gem_part["annotations"][0]["type"] == "speech_metadata", gem_part)
check("Gemini 单说话人给了 style", bool(gem_part["annotations"][0].get("style")))
check("Gemini 单说话人 speech_config 是数组",
      body["generation_config"]["speech_config"] == [{"voice": "Kore"}],
      body["generation_config"])
check("Gemini 要的是音频", body["response_format"] == {"type": "audio"})
check("Gemini 音色没填就用默认",
      T.gemini_body("x")["generation_config"]["speech_config"][0]["voice"]
      == T.GEMINI_DEFAULT_VOICE)

multi = T.gemini_body(
    "", speakers=[{"speaker": "主讲", "voice": "Puck"},
                  {"speaker": "听友", "voice": "Kore"}],
    parts=[{"speaker": "主讲", "text": "今天怎么样？"},
           {"speaker": "听友", "text": "还不错。"}])
check("多说话人每段带 speaker",
      [p["annotations"][0].get("speaker") for p in multi["input"][0]["content"]]
      == ["主讲", "听友"], multi["input"][0]["content"])
check("多说话人 speech_config 是对象",
      multi["generation_config"]["speech_config"]["speakers"][1]["voice"] == "Kore",
      multi["generation_config"])
try:
    T.gemini_body("", speakers=[{"speaker": "主讲", "voice": "Puck"}],
                  parts=[{"speaker": "路人", "text": "喂"}])
    bad_role = False
except T.TtsError as exc:
    bad_role = exc.reason == "text"
check("对白里没配音色的角色要报错", bad_role)

try:
    T.synth_gemini("你好。", "Kore", 0, os.path.join(tmp, "nokey.mp3"))
    gem_nokey = ""
except T.TtsError as exc:
    gem_nokey = exc.reason
check("没填 key 报 no-key", gem_nokey == "no-key", gem_nokey)

check("裸 PCM 能补出 RIFF 头",
      T.pcm_wav_bytes(b"\x00\x01" * 10, 24000)[:4] == b"RIFF"
      and len(T.pcm_wav_bytes(b"\x00\x01" * 10, 24000)) == 44 + 20)

tiny = [{"start": 0.0, "end": 2.0, "text": "第一句"}, {"start": 2.0, "end": 4.0, "text": "第二句"}]
scaled = T.scale_cues(tiny, 10.0)
check("模板按实际时长等比缩放",
      abs(scaled[0]["end"] - 5.0) < 0.01 and abs(scaled[1]["end"] - 10.0) < 0.01, scaled)
check("模板时长为零就返回空", T.scale_cues(tiny, 0.0) == [])

b64 = base64.b64encode(b"RIFFdata").decode()
got = []
T._walk_audio({"steps": [{"content": [{"type": "audio", "data": b64,
                                      "mime_type": "audio/wav"}]}]}, got)
check("interactions 的返回能取到音频", len(got) == 1 and got[0][1] == "audio/wav", got)
got2 = []
T._walk_audio({"candidates": [{"content": {"parts": [
    {"inlineData": {"data": b64, "mimeType": "audio/l16;rate=24000"}}]}}]}, got2)
check("老 generateContent 的返回也能取到音频",
      len(got2) == 1 and "l16" in got2[0][1], got2)
try:
    T._gemini_audio({"steps": [{"content": [{"type": "text", "text": "空"}]}]})
    no_audio = ""
except T.TtsError as exc:
    no_audio = exc.reason
check("没有音频要报 provider", no_audio == "provider", no_audio)

check("synth_for 认 gemini", T.synth_for("gemini") is T.synth_gemini)
try:
    azure_open = T.synth_for("azure") is T.synth_azure
except T.TtsError:
    azure_open = False
check("azure 接上了", azure_open)

# 429 重试：前两次报额度，第三次成功
real_post = T._post_json
real_sleep = T._sleep_cancel
real_tpl = T.template_cues

# 真转码：24k WAV → 单声道 mp3（同一份音频也用来做 429 重试的假返回）
import wave
wav_path = os.path.join(tmp, "gem_src.wav")
with wave.open(wav_path, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(24000)
    wf.writeframes(b"\x10\x27" * 12000)
with open(wav_path, "rb") as fh:
    wav_bytes = fh.read()
mp3_path = os.path.join(tmp, "gem.mp3")
T._audio_to_mp3(wav_bytes, "audio/wav", mp3_path)
check("Gemini 的 wav 能转成 mp3", os.path.exists(mp3_path)
      and abs(T.probe_duration(mp3_path) - 0.5) < 0.1,
      T.probe_duration(mp3_path) if os.path.exists(mp3_path) else "missing")

calls = {"n": 0}
wav_for_gemini = base64.b64encode(wav_bytes).decode()


def fake_post(url, payload, api_key, proxy="", timeout=180.0, headers=None):
    calls["n"] += 1
    if calls["n"] < 3:
        raise T.TtsError("quota", "429")
    return {"steps": [{"content": [{"type": "audio", "data": wav_for_gemini,
                                    "mime_type": "audio/wav"}]}]}


T._post_json = fake_post
T._sleep_cancel = lambda sec, cancel=None: None
T.template_cues = lambda text, dur, proxy="", cancel=None: []
try:
    syn = T.synth_gemini("你好，世界。", "Kore", 0, os.path.join(tmp, "retry.mp3"),
                         api_key="k")
    retried = calls["n"] == 3 and syn["dur"] > 0
finally:
    T._post_json = real_post
    T._sleep_cancel = real_sleep
    T.template_cues = real_tpl
check("429 会退避重试直到成功", retried, calls["n"])


check("key 不对会认成 auth",
      T.classify_http(400, '{"error":{"reason":"API_KEY_INVALID"}}').reason == "auth")
check("429 认成 quota", T.classify_http(429, "x").reason == "quota")
check("额度不足的错误体也认 quota",
      T.classify_http(400, '{"status":"RESOURCE_EXHAUSTED"}').reason == "quota")
check("500 认成 network", T.classify_http(500, "boom").reason == "network")



# ---------------- 13 对话模式（主讲 / 听友）
script = "开场白不算。\n主讲：第一句台词。\n接着说完。\n听友: 我插一句。\n主讲：收个尾。"
turns = T.parse_dialogue(script)
check("认出三轮对话", len(turns) == 3, turns)
check("轮次角色对",
      [t["speaker"] for t in turns] == ["主讲", "听友", "主讲"], turns)
check("续行接在上一轮", turns[0]["text"].startswith("第一句台词") and "接着说完" in turns[0]["text"], turns[0])
check("半角冒号也认", turns[1]["text"] == "我插一句。", turns[1])
check("没有标记就不是对话稿", T.parse_dialogue("普通的一段话。") == [])
check("空文稿没有轮次", T.parse_dialogue("  ") == [])
check("只有标记没文字就跳过",
      [t["text"] for t in T.parse_dialogue("主讲：\n听友：有词。")] == ["有词。"])

dplan = T.dialogue_plan(script, "edge", limit=8)
check("对话计划带 speaker", all(p.get("speaker") in ("主讲", "听友") for p in dplan), dplan)
check("对话计划段数够", len(dplan) >= 3, len(dplan))
check("单人稿没有对话计划", T.dialogue_plan("普通文稿。") == [])

seen_voices = []


def talk_synth(text, voice, rate, out, api_key="", base_url="", proxy=""):
    seen_voices.append(voice)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                  "-c:a", "libmp3lame", "-q:a", "7", out])
    return {"audio": out, "dur": 1.0,
            "cues": [{"start": 0.0, "end": 1.0, "text": text}]}


dres = T.synth_plan(
    [{"index": 0, "text": "甲。", "chars": 2, "speaker": "主讲"},
     {"index": 1, "text": "乙。", "chars": 2, "speaker": "听友"}],
    provider="edge", voice="A", rate=0, root=os.path.join(tmp, "dlg"),
    synth_fn=talk_synth, cleanup=False, gap=T.DIALOGUE_GAP,
    voices={"主讲": "A", "听友": "B"}, cue_prefix=True)
check("两个角色各用自己的音色", seen_voices == ["A", "B"], seen_voices)
check("轮间用 0.35 秒", abs(dres["dur"] - 2.35) < 0.15, round(dres["dur"], 2))
check("字幕带角色前缀",
      [c["text"] for c in dres["cues"]] == ["主讲：甲。", "听友：乙。"], dres["cues"])
check("第二轮字幕推后", abs(dres["cues"][1]["start"] - 1.35) < 0.1, dres["cues"][1])

plain = T.synth_plan([{"index": 0, "text": "甲。", "chars": 2}],
                     provider="edge", voice="A", rate=0,
                     root=os.path.join(tmp, "dlg2"), synth_fn=talk_synth,
                     cleanup=False, gap=0.6, cue_prefix=True)
check("单人不用前缀", [c["text"] for c in plain["cues"]] == ["甲。"], plain["cues"])



# ---------------- 14 Azure 语音 / OpenAI 兼容端点
_ssml = T.azure_ssml("你好 & <停顿>", "zh-CN-YunxiNeural", 10)
check("SSML 带音色", "name='zh-CN-YunxiNeural'" in _ssml, _ssml)
check("SSML 带语言", "xml:lang='zh-CN'" in _ssml)
check("SSML 转义特殊字符", "&amp;" in _ssml and "&lt;停顿&gt;" in _ssml, _ssml)
check("SSML 带语速", "rate='+10%'" in _ssml, _ssml)
check("零语速不写 rate", "rate=" not in T.azure_ssml("你好", "", 0))
check("负语速写成 -20%", "rate='-20%'" in T.azure_ssml("你好", "", -20))

_body = T.openai_body("你好", "nova", "", 50)
check("OpenAI 默认模型", _body["model"] == "tts-1", _body)
check("OpenAI 用 mp3", _body["response_format"] == "mp3")
check("OpenAI 语速折算", abs(_body["speed"] - 1.5) < 1e-6, _body)
check("OpenAI 原速不传 speed", "speed" not in T.openai_body("你好", "", "", 0))
check("OpenAI 语速上限夹住",
      abs(T.openai_body("你好", "", "", 500).get("speed", 0) - 4.0) < 1e-6)
check("OpenAI 模型可改", T.openai_body("你好", "", "CosyVoice2")["model"] == "CosyVoice2")
check("四家都接上了",
      all(T.synth_for(_p) is not None for _p in ("edge", "gemini", "azure", "openai")))
check("Azure 分派对", T.synth_for("azure") is T.synth_azure)
check("OpenAI 兼容分派对", T.synth_for("openai") is T.synth_openai)

_real_mp3 = os.path.join(tmp, "real.mp3")
T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
              "-c:a", "libmp3lame", "-q:a", "7", _real_mp3])
_mp3_bytes = open(_real_mp3, "rb").read()
_posts = []


def fake_bytes(url, body, headers, proxy="", timeout=180.0):
    _posts.append((url, body, headers))
    return _mp3_bytes


_old_bytes = T._post_bytes
_old_tpl = T.template_cues
T._post_bytes = fake_bytes
T.template_cues = lambda text, dur, proxy="", cancel=None: [
    {"start": 0.0, "end": max(0.1, dur / 2), "text": "甲。"},
    {"start": max(0.1, dur / 2), "end": dur, "text": "乙。"}]
try:
    got = T.synth_azure("甲乙。", "zh-CN-YunxiNeural", 10,
                        os.path.join(tmp, "az.mp3"), api_key="fake",
                        region="eastasia")
    check("Azure 出音频", os.path.exists(got["audio"]) and abs(got["dur"] - 1.0) < 0.2,
          round(got["dur"], 2))
    check("Azure 走对地址", _posts[-1][0].startswith(
        "https://eastasia.tts.speech.microsoft.com/cognitiveservices/v1"),
        _posts[-1][0])
    check("Azure 带订阅 key",
          _posts[-1][2].get("Ocp-Apim-Subscription-Key") == "fake")
    check("Azure body 是 SSML", b"<speak" in _posts[-1][1])
    check("Azure 有时间戳（模板）", len(got["cues"]) == 2, got["cues"])
    try:
        T.synth_azure("你好", "", 0, os.path.join(tmp, "az2.mp3"), api_key="")
        _ok = False
    except T.TtsError as exc:
        _ok = exc.reason == "no-key"
    check("Azure 没 key 报 no-key", _ok)

    op = T.synth_openai("甲乙。", "nova", 40, os.path.join(tmp, "oa.mp3"),
                        api_key="sk-test",
                        base_url="https://api.siliconflow.cn/v1")
    check("OpenAI 兼容出音频", abs(op["dur"] - 1.0) < 0.2, round(op["dur"], 2))
    check("OpenAI 兼容地址拼对",
          _posts[-1][0] == "https://api.siliconflow.cn/v1/audio/speech",
          _posts[-1][0])
    check("OpenAI 兼容是 Bearer",
          _posts[-1][2].get("Authorization") == "Bearer sk-test")
    _sent = json.loads(_posts[-1][1].decode("utf-8"))
    check("OpenAI 兼容请求体对",
          _sent["voice"] == "nova" and _sent["model"] == "tts-1"
          and abs(_sent.get("speed", 0) - 1.4) < 1e-6, _sent)
    try:
        T.synth_openai("你好", "", 0, os.path.join(tmp, "oa2.mp3"), api_key="")
        _ok = False
    except T.TtsError as exc:
        _ok = exc.reason == "no-key"
    check("OpenAI 兼容没 key 报 no-key", _ok)
finally:
    T._post_bytes = _old_bytes
    T.template_cues = _old_tpl

_boom = {"n": 0}


def flaky_bytes(url, body, headers, proxy="", timeout=180.0):
    _boom["n"] += 1
    if _boom["n"] == 1:
        raise T.TtsError("quota", "429")
    return _mp3_bytes


T._post_bytes = flaky_bytes
_old_sleep = T._sleep_cancel
T._sleep_cancel = lambda sec, cancel=None: None
T.template_cues = lambda text, dur, proxy="", cancel=None: []
try:
    ok = T.synth_openai("你好", "", 0, os.path.join(tmp, "oa3.mp3"), api_key="k")
    check("配额错误会重试一次", _boom["n"] == 2 and ok["dur"] > 0, _boom["n"])
    check("摊字幕兜底", len(ok["cues"]) >= 1)
finally:
    T._post_bytes = _old_bytes
    T._sleep_cancel = _old_sleep
    T.template_cues = _old_tpl


# ---------------- 15 base_url 只归 OpenAI 兼容端点 + 音色表
import base64 as _b64
import json as _json

check("音色表四家齐", set(T.TTS_VOICES) == {"edge", "gemini", "azure", "openai"},
      list(T.TTS_VOICES))
check("Azure 音色 4 个", len(T.TTS_VOICES["azure"]) == 4)
check("OpenAI 音色 6 个", len(T.TTS_VOICES["openai"]) == 6)
check("Edge 音色表与引擎一致",
      list(T.TTS_VOICES["edge"]) == list(T.EDGE_ZH_VOICES))

_seen = []


def _fake_bytes(url, body, headers, proxy="", timeout=180.0):
    _seen.append((url, headers, body))
    return _openai_mp3


_openai_mp3 = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "fixtures", "tone6.m4a"), "rb").read() \
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "fixtures", "tone6.m4a")) else b""
if not _openai_mp3:
    _tmp_mp3 = os.path.join(tmp, "t15.mp3")
    T.run_ffmpeg(["-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4",
                  "-c:a", "libmp3lame", "-q:a", "7", _tmp_mp3])
    _openai_mp3 = open(_tmp_mp3, "rb").read()

_old_bytes = T._post_bytes
_old_tpl = T.template_cues
T._post_bytes = _fake_bytes
T.template_cues = lambda text, dur, proxy="", cancel=None: [
    {"start": 0.0, "end": dur, "text": text}]
try:
    _seen.clear()
    T.synth_azure("测试", voice="zh-CN-XiaoxiaoNeural", out_path=os.path.join(tmp, "a15.mp3"),
                  api_key="k", base_url="https://api.siliconflow.cn/v1", region="eastasia")
    check("Azure 不拿 OpenAI 的 base_url 当地址",
          _seen and "eastasia.tts.speech.microsoft.com" in _seen[0][0], _seen[0][0] if _seen else "")

    _seen.clear()
    _gem_body = _json.dumps({
        "outputs": [{"content": [{"inlineData": {
            "mimeType": "audio/wav",
            "data": _b64.b64encode(T.pcm_wav_bytes(b"\x00\x00" * 2400)).decode()}}]}]})
    _old_post_json = T._post_json

    def _fake_post_json(url, payload, api_key, proxy="", timeout=180, headers=None):
        _seen.append((url, {}, b""))
        return _json.loads(_gem_body)

    T._post_json = _fake_post_json
    try:
        T.synth_gemini("测试", voice="Kore", out_path=os.path.join(tmp, "g15.mp3"),
                       api_key="k", base_url="https://api.siliconflow.cn/v1")
        check("Gemini 不拿 OpenAI 的 base_url 当地址",
              _seen and "generativelanguage.googleapis.com" in _seen[0][0],
              _seen[0][0] if _seen else "")
    finally:
        T._post_json = _old_post_json

    _seen.clear()
    T.synth_openai("测试", voice="alloy", out_path=os.path.join(tmp, "o15.mp3"),
                   api_key="k", base_url="https://api.siliconflow.cn/v1")
    check("OpenAI 兼容端点用自己的 base_url",
          _seen and _seen[0][0] == "https://api.siliconflow.cn/v1/audio/speech",
          _seen[0][0] if _seen else "")
finally:
    T._post_bytes = _old_bytes
    T.template_cues = _old_tpl

print()
print("RESULT:", "ALL PASS" if not fails else "%d FAIL -> %s" % (len(fails), fails),
      ("（%d skip）" % len(skips)) if skips else "")
sys.exit(1 if fails else 0)
