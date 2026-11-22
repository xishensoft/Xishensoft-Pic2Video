import io, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
snippet = "\n_TR_EXTRA3 = {\n    \"zh-TW\": {\n        \"本地音视频文件，或抖音 / YouTube 等视频链接\": \"本機音訊或影片檔案，或抖音 / YouTube 等影片連結\",\n        \"bilibili 链接或 BV 号（同 --audio）\": \"bilibili 連結或 BV 號（同 --audio）\",\n    },\n    \"en\": {\n        \"本地音视频文件，或抖音 / YouTube 等视频链接\": \"a local audio/video file, or a Douyin / YouTube link\",\n        \"bilibili 链接或 BV 号（同 --audio）\": \"bilibili link or BV id (same as --audio)\",\n    },\n    \"ja\": {\n        \"本地音视频文件，或抖音 / YouTube 等视频链接\": \"ローカルの音声・動画ファイル、または Douyin / YouTube のリンク\",\n        \"bilibili 链接或 BV 号（同 --audio）\": \"bilibili のリンクまたは BV 番号（--audio と同じ）\",\n    },\n    \"ko\": {\n        \"本地音视频文件，或抖音 / YouTube 等视频链接\": \"로컬 오디오/비디오 파일 또는 Douyin / YouTube 링크\",\n        \"bilibili 链接或 BV 号（同 --audio）\": \"bilibili 링크 또는 BV 번호 (--audio와 동일)\",\n    },\n}\nfor _lang, _extra3 in _TR_EXTRA3.items():\n    TR[_lang].update(_extra3)\n"
p = r'E:\deepseek\FM114.5\.scratch\pic2video\i18n.py'
src = io.open(p, encoding='utf-8').read()
assert '_TR_EXTRA3' not in src
io.open(p, 'a', encoding='utf-8', newline='\n').write(snippet)
print('appended', len(snippet))