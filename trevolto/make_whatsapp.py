"""Trevolto WhatsApp cover (clean, horizontal): chevron icon + headline +
14-day money-back guarantee + CTA, no mockup.
-> ../Trevolto Marketing/covers/whatsapp-cover.png   Run: python make_whatsapp.py"""
import os
import make_cover as mc
from PIL import Image, ImageDraw

HERE = mc.HERE
OUT = os.path.join(HERE, "..", "Trevolto Marketing", "covers", "whatsapp-cover.png")
W, H = 1920, 1080
SMOKE = mc.SMOKE; TEAL = mc.TEAL; LGREEN = mc.LGREEN; TXT = (240, 243, 247); DIM = mc.DIM


def trim(im):
    return im.crop(im.getbbox())


def build(path=OUT):
    im = Image.new("RGBA", (W, H), SMOKE + (255,))
    im = Image.alpha_composite(im, mc.glow((W, H), (int(W * 0.70), int(H * 0.46)), int(H * 0.85), TEAL, 55))
    d = ImageDraw.Draw(im)
    # LEFT: chevron icon + headline + subline (pulled to the very top, white text)
    x = 120
    ic = trim(Image.open(os.path.join(HERE, "logo.png")).convert("RGBA"))
    ih = 168; ic = ic.resize((int(ic.width * ih / ic.height), ih), Image.LANCZOS)
    im.alpha_composite(ic, (x, 70)); d = ImageDraw.Draw(im)
    d.text((x + 4, 286), "Stop watching charts.", font=mc.font(74), fill=TXT)
    d.text((x + 4, 380), "Let it trade for you.", font=mc.font(74), fill=TXT)
    d.text((x + 6, 504), "Professional automated crypto trading — Windows & macOS",
           font=mc.font(32, False), fill=(208, 215, 222))
    # RIGHT: money-back badge + CTA (aligned with the headline, near the top)
    rx, rw = 1170, 700
    byy, bh = 286, 92
    d.rounded_rectangle([rx, byy, rx + rw, byy + bh], bh // 2, outline=LGREEN, width=4)
    t = "✓  14-DAY MONEY-BACK GUARANTEE"; f = mc.font(29)
    d.text((rx + (rw - d.textlength(t, font=f)) // 2, byy + bh // 2 - 18), t, font=f, fill=LGREEN)
    cy, ch = byy + bh + 44, 132
    d.rounded_rectangle([rx, cy, rx + rw, cy + ch], ch // 2, fill=TEAL)
    cta = "Get Instant Access Now  →"; f = mc.font(40)
    d.text((rx + (rw - d.textlength(cta, font=f)) // 2, cy + ch // 2 - 26), cta, font=f, fill="#04231d")
    im.convert("RGB").save(path, quality=94)
    print("wrote", os.path.normpath(path), im.size)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    build()
