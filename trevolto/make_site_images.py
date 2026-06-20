"""Regenerate the Trevolto site's screenshots + OG card from the blue mockups.
Writes into ../site-trevolto/assets/. Run: python make_site_images.py"""
import os
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "..", "site-trevolto", "assets")
BG = (14, 17, 23)
TEAL, VIOLET = (45, 212, 191), (168, 85, 247)


def cover(im, w, h):
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = (im.width - w) // 2
    y = (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def feat(src, name, w=1100, h=619):
    im = Image.open(os.path.join(HERE, src)).convert("RGB")
    cover(im, w, h).save(os.path.join(DST, name), "WEBP", quality=88)
    print("wrote", name)


def og(name="og-trevolto.jpg", w=1200, h=630):
    # teal->violet diagonal-ish vertical gradient backdrop
    bg = Image.new("RGB", (w, h))
    px = bg.load()
    for y in range(h):
        t = y / h
        base = tuple(int(BG[i] + (VIOLET[i] - BG[i]) * 0.18 * t) for i in range(3))
        for x in range(w):
            tx = x / w
            px[x, y] = tuple(min(255, int(base[i] + (TEAL[i] - base[i]) * 0.10 * (1 - tx))) for i in range(3))
    # screenshot thumbnail with shadow, right side
    shot = Image.open(os.path.join(HERE, "gui_mockup.png")).convert("RGBA")
    tw = int(w * 0.52)
    shot = shot.resize((tw, int(shot.height * tw / shot.width)), Image.LANCZOS)
    sx, sy = w - tw - 40, (h - shot.height) // 2
    sh = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([sx + 8, sy + 12, sx + tw + 8, sy + shot.height + 12], 14, fill=(0, 0, 0, 160))
    bg = Image.alpha_composite(bg.convert("RGBA"), sh.filter(ImageFilter.GaussianBlur(18)))
    bg.paste(shot, (sx, sy), shot)
    # wordmark (use the transparent logo_wordmark on the left)
    wm = Image.open(os.path.join(HERE, "logo_wordmark.png")).convert("RGBA")
    ww = int(w * 0.42)
    wm = wm.resize((ww, int(wm.height * ww / wm.width)), Image.LANCZOS)
    bg.paste(wm, (56, 60), wm)
    d = ImageDraw.Draw(bg)
    from PIL import ImageFont
    try:
        font = ImageFont.truetype("/usr/local/lib/python3.11/dist-packages/matplotlib/mpl-data/fonts/ttf/DejaVuSans.ttf", 30)
    except Exception:
        font = ImageFont.load_default()
    d.text((64, 250), "Automated crypto trading", fill=(230, 237, 243), font=font)
    d.text((64, 292), "for Windows & macOS - start free.", fill=(176, 188, 200), font=font)
    bg.convert("RGB").save(os.path.join(DST, name), quality=90)
    print("wrote", name)


if __name__ == "__main__":
    feat("gui_chart.png", "feat-chart.webp")
    feat("gui_analytics.png", "feat-analytics.webp")
    feat("gui_backtest.png", "feat-backtest.webp")
    feat("gui_mockup.png", "feat-settings.webp")
    im = Image.open(os.path.join(HERE, "gui_chart.png")).convert("RGB")
    cover(im, 387, 375).save(os.path.join(DST, "chart-strategies.webp"), "WEBP", quality=88)
    print("wrote chart-strategies.webp")
    og()
