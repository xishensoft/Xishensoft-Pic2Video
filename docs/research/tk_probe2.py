import tkinter as tk, time, json
root=tk.Tk(); root.title("TK probe"); root.geometry("640x400+100+100")
tk.Label(root,text="tkinter OK",font=("Microsoft YaHei",28)).pack(expand=True)
root.update()
print(json.dumps({"tk":tk.TkVersion,"mapped":bool(root.winfo_ismapped()),"size":[root.winfo_width(),root.winfo_height()],"screen":[root.winfo_screenwidth(),root.winfo_screenheight()],"tcl":root.tk.call("info","patchlevel")}))
root.after(800, root.destroy); root.mainloop()
print("tk loop exited cleanly")
