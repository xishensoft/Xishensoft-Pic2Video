
import os, math, random
from PIL import Image, ImageDraw, ImageFilter

out = os.path.join(os.environ.get("TEMP", "."), "p2v_demo_imgs")
os.makedirs(out, exist_ok=True)

def grad(w, h, c1, c2, angle=90):
    img = Image.new("RGB", (w, h))
    px = img.load()
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)
    for y in range(h):
        for x in range(w):
            t = (x * dx + y * dy) / (abs(dx) * w + abs(dy) * h)
            t = max(0.0, min(1.0, t))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return img

def blur_circle(img, cx, cy, r, color, alpha=180):
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (alpha,))
    lay = lay.filter(ImageFilter.GaussianBlur(r * 0.35))
    img.alpha_composite(lay)

specs = [
    ("demo_1.png", (255, 138, 76), (60, 24, 92), "sun"),
    ("demo_2.png", (28, 176, 168), (10, 32, 66), "waves"),
    ("demo_3.png", (128, 92, 226), (18, 16, 40), "grid"),
    ("demo_4.png", (96, 200, 122), (14, 46, 38), "hills"),
]

for name, c1, c2, kind in specs:
    w, h = 1920, 1080
    base = grad(w, h, c1, c2, 105).convert("RGBA")
    d = ImageDraw.Draw(base)
    if kind == "sun":
        blur_circle(base, 1420, 330, 250, (255, 244, 196), 210)
        for i in range(6):
            y = 760 + i * 46
            d.line([(0, y), (w, y + 30)], fill=(255, 255, 255, 40 - i * 4), width=10)
    elif kind == "waves":
        for i in range(9):
            y0 = 430 + i * 62
            pts = [(x, y0 + int(38 * math.sin(x / 210.0 + i * 0.8))) for x in range(0, w + 1, 24)]
            d.line(pts, fill=(255, 255, 255, 34), width=9)
        blur_circle(base, 420, 300, 180, (200, 255, 250), 150)
    elif kind == "grid":
        for x in range(0, w + 1, 120):
            d.line([(x, 0), (x, h)], fill=(255, 255, 255, 26), width=4)
        for y in range(0, h + 1, 120):
            d.line([(0, y), (w, y)], fill=(255, 255, 255, 26), width=4)
        blur_circle(base, 1500, 800, 230, (255, 210, 245), 150)
    else:
        for i, (yy, cc) in enumerate([(700, (12, 74, 58)), (820, (10, 58, 48)), (940, (8, 44, 38))]):
            pts = [(0, h)] + [(x, yy + int(52 * math.sin(x / 330.0 + i))) for x in range(0, w + 1, 20)] + [(w, h)]
            d.polygon(pts, fill=cc + (235,))
        blur_circle(base, 1560, 250, 120, (255, 255, 220), 200)

    base.convert("RGB").save(os.path.join(out, name), quality=95)
    print("wrote", name, base.size)
print("DIR:", out)
