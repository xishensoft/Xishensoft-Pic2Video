# -*- coding: utf-8 -*-
import io, json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = "E:/deepseek/Xishensoft-Pic2Video/i18n.py"
data = json.loads(r'''{"撤回":["撤回","Undo","元に戻す","실행 취소"],"素材":["素材","Media","素材","소재"],"历史":["歷史","History","履歴","기록"],"打开":["開啟","Opened","開く","열기"],"调整顺序":["調整順序","Reorder","順序を変更","순서 조정"],"删除素材":["刪除素材","Remove media","素材を削除","소재 삭제"],"改时长":["改時長","Change duration","長さを変更","길이 변경"],"改节奏":["改節奏","Change timing","タイミング変更","타이밍 변경"],"加音频":["加音訊","Add audio","音声を追加","오디오 추가"],"删音频":["刪音訊","Remove audio","音声を削除","오디오 삭제"],"移音频":["移音訊","Move audio","音声を移動","오디오 이동"],"改音量":["改音量","Change volume","音量を変更","볼륨 변경"],"改设置":["改設定","Change settings","設定を変更","설정 변경"],"换背景音乐":["換背景音樂","Change BGM","BGM を変更","배경 음악 변경"],"撤销上一步（Ctrl+Z）":["復原上一步（Ctrl+Z）","Undo the last step (Ctrl+Z)","直前の操作を取り消す（Ctrl+Z）","이전 단계 실행 취소 (Ctrl+Z)"],"重做（Ctrl+Shift+Z）":["重做（Ctrl+Shift+Z）","Redo (Ctrl+Shift+Z)","やり直し（Ctrl+Shift+Z）","다시 실행 (Ctrl+Shift+Z)"]}''')
langs = ["zh-TW", "en", "ja", "ko"]
lines = ["", "_TR_EXTRA20 = {"]
for k, vals in data.items():
    lines.append("    %r: {" % k)
    for lang, v in zip(langs, vals):
        lines.append("        %r: %r," % (lang, v))
    lines.append("    },")
lines.append("}")
lines.append("")
lines.append("for _k, _trs in _TR_EXTRA20.items():")
lines.append("    for _lang, _v in _trs.items():")
lines.append("        TR[_lang].setdefault(_k, _v)")
lines.append("")
src = io.open(P, encoding="utf-8").read()
if "_TR_EXTRA20" not in src:
    src = src.rstrip("\n") + "\n" + "\n".join(lines) + "\n"
    io.open(P, "w", encoding="utf-8", newline="\n").write(src)
    print("appended")
else:
    print("already there")
sys.path.insert(0, "E:/deepseek/Xishensoft-Pic2Video")
import i18n
print({l: len(i18n.TR[l]) for l in i18n.TR})
