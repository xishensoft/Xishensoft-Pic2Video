
import base64, json, os, sqlite3, shutil, tempfile, urllib.request
UA="Mozilla/5.0"
def get(u):
    req=urllib.request.Request(u,headers={"User-Agent":UA})
    with urllib.request.urlopen(req,timeout=40) as r:
        return r.read()
out={}
try:
    raw=get("https://chromium.googlesource.com/chromium/src/+/main/components/os_crypt/sync/app_bound_encryption_win.h?format=TEXT")
    txt=base64.b64decode(raw).decode("utf-8","replace")
    out["chromium_header_len"]=len(txt)
    out["chromium_header_snippet"]="\n".join(txt.splitlines()[:18])
except Exception as e:
    out["chromium_header_err"]=repr(e)

# local cookie DB metadata (no decryption, no values)
def inspect(path,name):
    if not os.path.exists(path): return {"exists":False}
    tmp=os.path.join(tempfile.gettempdir(),"ck_%s.db"%name)
    try:
        shutil.copy2(path,tmp)
    except Exception as e:
        return {"exists":True,"copy_err":repr(e)}
    try:
        con=sqlite3.connect(tmp); cur=con.cursor()
        cur.execute("select count(*) from cookies"); total=cur.fetchone()[0]
        cur.execute("select substr(hex(encrypted_value),1,4), count(*) from cookies group by 1")
        pref=cur.fetchall()
        cur.execute("select count(*) from cookies where host_key like '%bilibili%'")
        bili=cur.fetchone()[0]
        cur.execute("select name from cookies where host_key like '%bilibili%' limit 20")
        names=[r[0] for r in cur.fetchall()]
        con.close()
        return {"exists":True,"total":total,"prefix_counts":pref,"bilibili_rows":bili,"bilibili_cookie_names":sorted(set(names))}
    finally:
        try: os.remove(tmp)
        except: pass

out["chrome"]=inspect(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Network\Cookies"),"chrome")
out["edge"]=inspect(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Network\Cookies"),"edge")

# firefox cookies.sqlite
ffdir=os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles")
ff={}
if os.path.isdir(ffdir):
    for prof in os.listdir(ffdir):
        p=os.path.join(ffdir,prof,"cookies.sqlite")
        if os.path.exists(p):
            tmp=os.path.join(tempfile.gettempdir(),"ffck.db")
            shutil.copy2(p,tmp)
            try:
                con=sqlite3.connect(tmp); cur=con.cursor()
                cur.execute("select count(*) from moz_cookies"); total=cur.fetchone()[0]
                cur.execute("select count(*) from moz_cookies where host like '%bilibili%'"); bili=cur.fetchone()[0]
                cur.execute("select name,value from moz_cookies where host like '%bilibili%' and name='SESSDATA'")
                rows=cur.fetchall()
                con.close()
                ff[prof]={"total":total,"bilibili_rows":bili,"sessdata_present":bool(rows),"sessdata_len":len(rows[0][1]) if rows else 0}
            finally:
                try: os.remove(tmp)
                except: pass
out["firefox"]=ff
open(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\probe11.json","w",encoding="utf-8").write(json.dumps(out,ensure_ascii=False,indent=1))
print("ok")
