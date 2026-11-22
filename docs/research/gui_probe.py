
import os, sys, time, json, traceback
TMP = r"E:\deepseek\FM114.5\.scratch\pic2video\probe\tmp"
OUT = r"E:\deepseek\FM114.5\.scratch\pic2video\probe"
SAMPLE = os.path.join(TMP,"sample_filter.mp4")
res={}

# ---- tkinter ----
try:
    import tkinter as tk
    root=tk.Tk(); root.title("TK probe"); root.geometry("640x400+80+80")
    tk.Label(root,text="tkinter OK",font=("Microsoft YaHei",28)).pack(expand=True)
    root.update(); root.after(600, root.quit); root.mainloop()
    from PIL import ImageGrab
    x,y=root.winfo_rootx(),root.winfo_rooty(); w,h=root.winfo_width(),root.winfo_height()
    img=ImageGrab.grab(bbox=(x,y,x+w,y+h)); img.save(os.path.join(OUT,"tk_window.png"))
    res["tk"]={"ok":True,"tk":tk.TkVersion,"size":[w,h],"screen":[root.winfo_screenwidth(),root.winfo_screenheight()]}
    root.destroy()
except Exception as e:
    res["tk"]={"ok":False,"err":repr(e),"tb":traceback.format_exc()[-800:]}

# ---- PyQt6 + QtMultimedia ----
try:
    from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel
    from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PyQt6.QtMultimediaWidgets import QVideoWidget
    from PyQt6.QtCore import QUrl, QTimer
    from PyQt6.QtGui import QGuiApplication
    app=QApplication(sys.argv)
    win=QMainWindow(); win.setWindowTitle("Qt probe"); win.resize(960,560)
    vw=QVideoWidget(); win.setCentralWidget(vw)
    player=QMediaPlayer(); out=QAudioOutput(); player.setAudioOutput(out); player.setVideoOutput(vw)
    info={}
    player.mediaStatusChanged.connect(lambda s: info.setdefault("statuses",[]).append(str(s)))
    player.errorOccurred.connect(lambda e,msg: info.__setitem__("error",f"{e}:{msg}"))
    player.setSource(QUrl.fromLocalFile(SAMPLE))
    player.play()
    win.show()
    def finish():
        info["duration"]=player.duration(); info["position"]=player.position()
        info["state"]=str(player.playbackState()); info["mediaStatus"]=str(player.mediaStatus())
        info["hasVideo"]=player.hasVideo(); info["error_str"]=player.error()
        try:
            from PIL import ImageGrab
            g=win.frameGeometry(); ImageGrab.grab(bbox=(g.x(),g.y(),g.x()+g.width(),g.y()+g.height())).save(os.path.join(OUT,"qt_window.png"))
            info["screenshot"]=True
        except Exception as e: info["screenshot"]=repr(e)
        res["qt"]=info; app.quit()
    QTimer.singleShot(2600, finish)
    app.exec()
except Exception as e:
    res["qt"]={"ok":False,"err":repr(e),"tb":traceback.format_exc()[-1200:]}

print(json.dumps(res,ensure_ascii=False,indent=1))

