
import json, io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = r"E:\deepseek\FM114.5\.scratch\pic2video"
extra = json.load(open(os.path.join(HERE, "probe", "i18n_extra2.json"), encoding="utf-8"))
q = os.path.join(HERE, "i18n.py")
src = open(q, encoding="utf-8").read()
if "_TR_EXTRA2" not in src:
    body = "\n\n# ---- 追加词条 2：设置窗新增行 ----\n_TR_EXTRA2 = {\n"
    for lang in ("zh-TW", "en", "ja", "ko"):
        body += '    "%s": {\n' % lang
        for k, v in extra[lang].items():
            body += "        %s: %s,\n" % (json.dumps(k, ensure_ascii=False), json.dumps(v, ensure_ascii=False))
        body += "    },\n"
    body += "}\nfor _lang, _extra2 in _TR_EXTRA2.items():\n    TR[_lang].update(_extra2)\n"
    open(q, "a", encoding="utf-8", newline="\n").write(body)
    print("appended 2")
