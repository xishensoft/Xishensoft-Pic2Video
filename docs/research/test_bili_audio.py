# -*- coding: utf-8 -*-
import json, os, sys, traceback
sys.path.insert(0, r"E:\deepseek\FM114.5\.scratch\pic2video\probe")
from bili_audio import bili_audio, BiliError
outdir = r"E:\deepseek\FM114.5\.scratch\pic2video\probe\testout"
os.makedirs(outdir, exist_ok=True)
cases = [
 ("https://www.bilibili.com/video/BV1jQst6BEf2", "normal.m4a"),
 ("https://b23.tv/BV1jQst6BEf2", "b23bv.m4a"),
 ("https://www.bilibili.com/video/av117562180376697", "av.m4a"),
 ("https://www.bilibili.com/video/BV1NDsS6UE8D?p=2", "p2.m4a"),
 ("https://www.bilibili.com/bangumi/play/ep323085", "ep_free.m4a"),
 ("https://www.bilibili.com/bangumi/play/ep323835", "ep_vip.m4a"),
 ("https://www.bilibili.com/video/BV1ab411c7mD", "gone.m4a"),
 ("https://www.bilibili.com/video/BV1xx411c7mZ", "none.m4a"),
 ("https://live.bilibili.com/6", "live.m4a"),
 ("https://www.bilibili.com/video/BV1jQst6BEf2?p=999", "badp.m4a"),
 ("https://www.bilibili.com/video/BV1jQst6BEf2", "wav16k.wav"),
]
res=[]
for url,name in cases:
    rec={"url":url,"expect":name}
    try:
        wav = name.endswith(".wav")
        path=bili_audio(url, os.path.join(outdir,name), wav=wav)
        rec.update({"ok":True,"path":path,"bytes":os.path.getsize(path)})
    except BiliError as e:
        rec.update({"ok":False,"code":e.code,"user_message":e.user_message,"str":str(e)[:200]})
    except Exception as e:
        rec.update({"ok":False,"code":"exception","str":repr(e)[:300],"tb":traceback.format_exc()[-500:]})
    print(json.dumps(rec,ensure_ascii=False))
    res.append(rec)
open(os.path.join(outdir,"results.json"),"w",encoding="utf-8").write(json.dumps(res,ensure_ascii=False,indent=1))
