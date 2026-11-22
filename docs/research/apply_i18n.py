
import json, io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = r"E:\deepseek\FM114.5\.scratch\pic2video"

# 1) audio_source: wrap user-facing strings with tr()
p = os.path.join(HERE, "audio_source.py")
src = open(p, encoding="utf-8").read()
if "from i18n import tr" not in src:
    src = src.replace("import sys\n\nHERE = os.path.dirname",
                      "import sys\n\nfrom i18n import tr\n\nHERE = os.path.dirname", 1)
src = src.replace(
    'progress("音源组件已就绪：" + ver if ver else "音源组件已就绪")',
    'progress(tr("音源组件已就绪：{v}", v=ver) if ver else tr("音源组件已就绪"))')
keys = [
 "这个链接不支持（可能是私密/直播/需要登录）","该站点要求登录，暂时取不到音轨",
 "视频是私密的，取不到音轨","该视频有地区限制","网络连不上该站点（可能要代理）",
 "下载被站点拒绝（可能需要登录或频繁重试）","取音轨失败（详情见设置里重试一次）",
 "正在下载音源组件（yt-dlp）…","下载音源组件失败（检查网络 / pip）",
 "先给个链接或本地文件","这个平台要先下载音源组件：设置 → 「抖音 / YouTube 支持」点下载",
 "正在解析链接…","找不到 Python，无法调用 yt-dlp",
 "解析超时（网络太慢或站点限流），稍后再试","下载音源组件超时，检查网络后再试",
 "正在从本地视频里抽音轨…","正在取 B 站音轨…",
]
for k in keys:
    needle = '"%s"' % k
    if needle in src:
        src = src.replace(needle, 'tr("%s")' % k)
open(p, "w", encoding="utf-8", newline="\n").write(src)
print("audio_source wrapped; tr count", src.count("tr(\""))

# 2) i18n.py: append extra table
extra = json.load(open(os.path.join(HERE, "probe", "i18n_extra.json"), encoding="utf-8"))
q = os.path.join(HERE, "i18n.py")
src = open(q, encoding="utf-8").read()
if "_TR_EXTRA" in src:
    print("i18n already extended")
else:
    body = "\n\n# ---- 追加词条：多平台音源 / 主题配色 / 色环取色 ----\n_TR_EXTRA = {\n"
    for lang in ("zh-TW", "en", "ja", "ko"):
        body += '    "%s": {\n' % lang
        for k, v in extra[lang].items():
            body += "        %s: %s,\n" % (json.dumps(k, ensure_ascii=False),
                                           json.dumps(v, ensure_ascii=False))
        body += "    },\n"
    body += "}\nfor _lang, _extra in _TR_EXTRA.items():\n    TR[_lang].update(_extra)\n"
    open(q, "a", encoding="utf-8", newline="\n").write(body)
    print("i18n appended")
