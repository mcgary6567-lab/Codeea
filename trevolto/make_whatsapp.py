"""Trevolto WhatsApp cover (clean): chevron icon + headline + 14-day money-back
guarantee + CTA, no mockup. -> ../Trevolto Marketing/covers/whatsapp-cover.png
Run: python make_whatsapp.py"""
import os
import make_cover as mc
from PIL import Image, ImageDraw

HERE = mc.HERE
OUT = os.path.join(HERE, "..", "Trevolto Marketing", "covers", "whatsapp-cover.png")
W, H = 1080, 1920
SMOKE = mc.SMOKE; TEAL = mc.TEAL; LGREEN = mc.LGREEN; TXT = (240, 243, 247); DIM = mc.DIM


def trim(im):
    return im.crop(im.getbbox())


def ctext(d, y, t, f, fill):
    d.text(((W - d.textlength(t, font=f)) // 2, y), t, font=f, fill=fill)


def build(path=OUT):
    im = Image.new("RGBA", (W, H), SMOKE + (255,))
    im = Image.alpha_composite(im, mc.glow((W, H), (W // 2, int(H * 0.30)), int(W * 0.78), TEAL, 60))
    d = ImageDraw.Draw(im)
    # chevron icon (hero — no mockup)
    ic = trim(Image.open(os.path.join(HERE, "logo.png")).convert("RGBA"))
    ih = 320; ic = ic.resize((int(ic.width * ih / ic.height), ih), Image.LANCZOS)
    im.alpha_composite(ic, ((W - ic.width) // 2, 360))
    # headline
    ctext(d, 800, "Stop watching charts.", mc.font(72), TXT)
    ctext(d, 892, "Let it trade for you.", mc.font(72), TEAL)
    ctext(d, 1018, "Automated crypto trading · Windows & macOS", mc.font(34, False), DIM)
    # 14-day money-back guarantee badge
    bw, bh = 740, 88; bx = (W - bw) // 2; byy = 1170
    d.rounded_rectangle([bx, byy, bx + bw, byy + bh], bh // 2, outline=LGREEN, width=4)
    ctext(d, byy + bh // 2 - 22, "✓  14-DAY MONEY-BACK GUARANTEE", mc.font(34), LGREEN)
    # CTA
    cy = 1340; cw, ch = 780, 120; cxp = (W - cw) // 2
    d.rounded_rectangle([cxp, cy, cxp + cw, cy + ch], 60, fill=TEAL)
    f = mc.font(42); cta = "Download  →  trevolto.com"
    d.text(((W - d.textlength(cta, font=f)) // 2, cy + ch // 2 - 27), cta, font=f, fill="#04231d")
    # disclaimer
    ctext(d, H - 72, "Crypto trading carries risk — you can lose money. Not financial advice.",
          mc.font(23, False), DIM)
    im.convert("RGB").save(path, quality=94)
    print("wrote", os.path.normpath(path), im.size)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    build()
