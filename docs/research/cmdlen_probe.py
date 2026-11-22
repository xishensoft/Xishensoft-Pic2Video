
import subprocess, sys
base=["C:\\Windows\\System32\\cmd.exe","/c","echo"]
# build command line > 32767 chars via subprocess (CreateProcess path)
args=base+["x"*100]*400   # ~40k chars
try:
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    print("OK",p.returncode,len(subprocess.list2cmdline(args)))
except OSError as e:
    print("OSError:", e.errno, getattr(e,"winerror",None), str(e)[:300], "cmdlen", len(subprocess.list2cmdline(args)))

