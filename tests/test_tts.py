# -*- coding: utf-8 -*-
"""配音引擎（tts_engine.py）的单元检查：分段 / 折行 / 时间戳 / 缓存 / 合并 / 错误分类。

不碰界面；只有 Edge 真合成与 ffmpeg 合并会联网/起进程，失败时记 SKIP 不算红。
"""
import json
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
                   synth_fn=fake_synth, gap=0.6,
                   on_progress=lambda done, total, cached: seen.append((done, total, cached)))
check("整篇合成两段都调用", calls["n"] == 2, calls["n"])
check("整篇总长 = 两段 + 间隔", abs(res["dur"] - 2.6) < 0.2, round(res["dur"], 2))
check("整篇两条字幕", len(res["cues"]) == 2, res["cues"])
check("第二段字幕被推后", abs(res["cues"][1]["start"] - 1.6) < 0.12, res["cues"][1])
check("进度回调按顺序", [x[0] for x in seen] == [1, 2] and all(x[1] == 2 for x in seen), seen)

again = T.synth_plan(plan2, provider="edge", voice="v1", rate=0, root=pipe_root,
                     synth_fn=fake_synth, gap=0.6)
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
                 synth_fn=bad_synth, gap=0.6)
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
    T.synth_for("gemini")
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

print()
print("RESULT:", "ALL PASS" if not fails else "%d FAIL -> %s" % (len(fails), fails),
      ("（%d skip）" % len(skips)) if skips else "")
sys.exit(1 if fails else 0)
