# -*- coding: utf-8 -*-
import io, os
p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n.py")
data = {
 "闪退 / 导出失败时先看这份记录：{path}": {
   "zh-TW": "閃退 / 匯出失敗時先看這份記錄：{path}",
   "en": "Crashes and failed exports are logged here: {path}",
   "ja": "クラッシュや書き出し失敗の記録はここ：{path}",
   "ko": "크래시 및 내보내기 실패 기록: {path}"},
 "打开日志": {"zh-TW": "開啟日誌", "en": "Open log", "ja": "ログを開く", "ko": "로그 열기"},
 "错误日志": {"zh-TW": "錯誤日誌", "en": "Error log", "ja": "エラーログ", "ko": "오류 로그"},
 "版本": {"zh-TW": "版本", "en": "Version", "ja": "バージョン", "ko": "버전"},
}
lines = ['', '', '_TR_EXTRA19 = {']
for k, trs in data.items():
    lines.append('    %r: {' % k)
    for lang, v in trs.items():
        lines.append('        %r: %r,' % (lang, v))
    lines.append('    },')
lines.append('}')
lines.append('for _k, _trs in _TR_EXTRA19.items():')
lines.append('    for _lang, _v in _trs.items():')
lines.append('        TR[_lang][_k] = _v')
lines.append('')
with io.open(p, "a", encoding="utf-8", newline="\n") as fh:
    fh.write("\n".join(lines))
print("appended to", p)
