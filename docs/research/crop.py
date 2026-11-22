
from PIL import Image
src = r"E:\deepseek\FM114.5\.scratch\pic2video\probe\shots\shot_settings_zh-CN_pro_light_acc.png"
im = Image.open(src)
a = im.crop((160, 215, 420, 255)).resize((260*3, 40*3), Image.NEAREST)
a.save(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\crop_spin.png")
b = im.crop((160, 575, 760, 620)).resize((600*2, 45*2), Image.NEAREST)
b.save(r"E:\deepseek\FM114.5\.scratch\pic2video\probe\crop_spin2.png")
print("ok")
