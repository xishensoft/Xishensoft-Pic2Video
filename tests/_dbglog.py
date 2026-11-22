# -*- coding: utf-8 -*-
import os, sys, tempfile
sys.path.insert(0, r"E:\deepseek\Xishensoft-Pic2Video")
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="p2v_dbglog_")
import pic2video as p
p._start_log()
p.log("hello-log")
try:
    raise ValueError("boom-test")
except ValueError:
    p._log_traceback("sys", *sys.exc_info()[1:])
print("PATH", p.log_path())
print(open(p.log_path(), encoding="utf-8", errors="replace").read()[-500:])
