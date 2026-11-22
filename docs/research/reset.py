import json, os, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
p = r'E:\deepseek\FM114.5\.scratch\pic2video\settings.json'
d = json.load(open(p, encoding='utf-8'))
d.update(lang='auto', ui='simple', theme='auto', accent='#3b82f6', last_export_dir='')
json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('settings:', d['lang'], d['ui'], d['theme'], d['accent'])
for n in ('probe/crop_spin.png','probe/crop_spin2.png','probe/testout/cli_bili.mp4','probe/testout/cli_local.mp4','probe/testout/shot_light.mp4','probe/yt_probe.txt','probe/yt_bili.txt','probe/i18n_extra.json','probe/i18n_extra2.json'):
    f = os.path.join(r'E:\deepseek\FM114.5\.scratch\pic2video', n)
    if os.path.exists(f):
        os.remove(f); print('rm', n)