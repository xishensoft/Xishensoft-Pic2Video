
import ast, sys, io, glob, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import i18n
keys = set()
for name in ("pic2video.py", "audio_source.py", "theme.py"):
    src = open(os.path.join(HERE, name), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "tr":
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                keys.add(n.args[0].value)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in ("add_row", "add_sep")):
            if n.args and isinstance(n.args[0], ast.Constant):
                keys.add(n.args[0].value)
keys |= {"跟随系统", "浅色", "深色"}
print("total", len(keys))
for lang in ("zh-TW", "en", "ja", "ko"):
    miss = sorted(k for k in keys if k not in i18n.TR[lang])
    print(lang, "missing", len(miss), miss[:8])
print("table sizes", {l: len(i18n.TR[l]) for l in i18n.TR})
print("same keysets", len({frozenset(v) for v in i18n.TR.values()}) == 1)
print("theme label sample", i18n.tr("浅色"), "/", i18n.tr("深色"), "| en")
i18n.set_locale("en")
print(i18n.tr("音乐链接"), "|", i18n.tr("已安装 {v} · {mb} MB（占地方可以随时删）", v="2026.10.26", mb=16))
i18n.set_locale("ja")
print(i18n.tr("这个平台的链接要靠 yt-dlp 解析（约 15 MB，装在工具目录里，随时可删）。现在下载吗？"))
