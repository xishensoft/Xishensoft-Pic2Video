
import os
from PIL import Image
p = r"C:\Users\XISHENSOFT\AppData\Local\Temp\p2v_rec2\frames\h_8_0.png"
im = Image.open(p).convert("RGB")
w, h = im.size
print("size", w, h)
row = 400
print("-- row 400 near left edge --")
for x in range(60, 90):
    print(x, im.getpixel((x, row)))
print("-- row 400 near right edge --")
for x in range(1420, 1460):
    print(x, im.getpixel((x, row)))
col = 700
print("-- col 700 near bottom --")
for y in range(830, 864):
    print(y, im.getpixel((col, y)))
print("-- col 700 near top --")
for y in range(8, 26):
    print(y, im.getpixel((col, y)))
