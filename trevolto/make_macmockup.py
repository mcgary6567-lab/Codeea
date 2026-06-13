"""Composite the Trevolto app window into a macOS window frame on a desktop
backdrop. Run: python make_macmockup.py -> ../Trevolto Marketing/mockups/macos_main.png"""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "..", "Trevolto Marketing", "mockups")
os.makedirs(OUTDIR, exist_ok=True)

BAR = (28, 30, 38)          # titlebar
LIGHTS = ["#ff5f57", "#febc2e", "#28c840"]
# Trevolto teal->violet desktop gradient
TOP, BOT = (16, 22, 38), (40, 22, 64)


def rounded_mask(size, rad):
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], rad, fill=255)
    return m


def gradient(size):
    w, h = size
    g = Image.new("RGB", size)
    px = g.load()
    for y in range(h):
        t = y / h
        px_row = tuple(int(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3))
        for x in range(w):
            px[x, y] = px_row
    return g


def make(src_name, out_name, scale=1.0):
    shot = Image.open(os.path.join(HERE, src_name)).convert("RGBA")
    if scale != 1.0:
        shot = shot.resize((int(shot.width * scale), int(shot.height * scale)), Image.LANCZOS)
    w, h = shot.size
    tb = max(40, int(h * 0.058))
    rad = max(16, int(w * 0.012))

    # window = titlebar + screenshot
    win = Image.new("RGBA", (w, h + tb), (0, 0, 0, 0))
    bar = Image.new("RGBA", (w, tb), BAR + (255,))
    win.paste(bar, (0, 0))
    win.paste(shot, (0, tb), shot)
    d = ImageDraw.Draw(win)
    r = int(tb * 0.16)
    cy = tb // 2
    for i, col in enumerate(LIGHTS):
        cx = int(tb * 0.55) + i * int(tb * 0.5)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    d.text((w // 2 - 28, cy - 7), "Trevolto", fill=(180, 188, 200))
    win.putalpha(rounded_mask(win.size, rad))

    # backdrop with padding + soft shadow
    pad = int(w * 0.09)
    bg = gradient((w + pad * 2, h + tb + pad * 2)).convert("RGBA")
    sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    off = int(pad * 0.18)
    sd.rounded_rectangle([pad, pad + off, pad + w, pad + h + tb + off], rad,
                         fill=(0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(int(pad * 0.35)))
    bg = Image.alpha_composite(bg, sh)
    bg.paste(win, (pad, pad), win)
    out = os.path.join(OUTDIR, out_name)
    bg.convert("RGB").save(out, quality=92)
    print("wrote", os.path.normpath(out))


if __name__ == "__main__":
    make("gui_mockup.png", "macos_main.png")
    make("gui_chart.png", "macos_chart.png")
