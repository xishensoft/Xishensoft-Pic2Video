# -*- coding: utf-8 -*-
import sys, os, io, ast
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"E:\deepseek\Xishensoft-Pic2Video"
sys.path.insert(0, ROOT)
import i18n
keys = ["撤回","还原","素材","历史","打开","调整顺序","删除素材","改时长","改节奏",
        "加音频","删音频","移音频","改音量","改设置","换背景音乐",
        "撤销上一步（Ctrl+Z）","重做（Ctrl+Shift+Z）","加图片","淡入淡出"]
for k in keys:
    miss = [l for l in i18n.TR if k not in i18n.TR[l]]
    print(("MISS " if miss else "ok   ") + k + (" " + ",".join(miss) if miss else ""))
