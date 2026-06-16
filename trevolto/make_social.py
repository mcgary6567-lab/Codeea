"""Trevolto social-media logo assets (teal/smoke brand).
Builds square avatars + platform banners from the existing logo art.
Run with --sample for just two preview files, or no args for the full set."""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(HERE, "..", "Trevolto Marketing", "social", "branding")
SMOKE = (27, 31, 36)            # #1b1f24
TEAL = (45, 212, 191)
LGREEN = (74, 222, 128)
ICON = os.path.join(HERE, "logo.png")            # chevron mark (teal->violet)
WORD = os.path.join(HERE, "trevolto_logo.png")   # mark + TREVOLTO (teal)
try:
    TTF = ImageFont.truetype(os.path.join(os.path.dirname(__import__("matplotlib").__file__),
                             "mpl-data", "fonts", "ttf", "DejaVuSans.ttf"), 40)
except Exception:
    TTF = ImageFont.load_default()


def trim(im): return im.crop(im.getbbox())


def glow(size, center, radius, color, alpha=70):
    g = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    d.ellipse([center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius],
              fill=color+(alpha,))
    return g.filter(ImageFilter.GaussianBlur(radius*0.5))


def avatar(path, S=1024, frac=0.5):
    im = Image.new("RGBA", (S, S), SMOKE+(255,))
    im = Image.alpha_composite(im, glow((S, S), (S//2, S//2), int(S*0.34), TEAL, 60))
    ic = trim(Image.open(ICON).convert("RGBA"))
    h = int(S*frac); ic = ic.resize((int(ic.width*h/ic.height), h), Image.LANCZOS)
    im.alpha_composite(ic, ((S-ic.width)//2, (S-ic.height)//2))
    im.convert("RGB").save(path, quality=92); print("wrote", os.path.basename(path), im.size)


def banner(path, W, H, wm_frac=0.62, tagline=True):
    im = Image.new("RGBA", (W, H), SMOKE+(255,))
    im = Image.alpha_composite(im, glow((W, H), (W//2, int(H*0.42)), int(H*0.6), TEAL, 55))
    wm = trim(Image.open(WORD).convert("RGBA"))
    ww = int(W*wm_frac); wm = wm.resize((ww, int(wm.height*ww/wm.width)), Image.LANCZOS)
    y = int(H*0.40) - wm.height//2
    im.alpha_composite(wm, ((W-wm.width)//2, y))
    if tagline:
        d = ImageDraw.Draw(im)
        f = TTF.font_variant(size=max(20, H//15))
        t = "Automated Crypto Trading"
        tw = d.textlength(t, font=f)
        wm_x = (W-wm.width)//2
        word_cx = wm_x + int(wm.width*0.59)          # centre under the TREVOLTO text
        d.text((word_cx - tw/2, y+wm.height+int(H*0.03)), t, font=f, fill=LGREEN)
    im.convert("RGB").save(path, quality=92); print("wrote", os.path.basename(path), im.size)


if __name__ == "__main__":
    os.makedirs(OUTDIR, exist_ok=True)
    if "--sample" in sys.argv:
        avatar(os.path.join(HERE, "sample_avatar.png"), 1024)
        banner(os.path.join(HERE, "sample_banner.png"), 1500, 500)
    else:
        # Square avatars / profile pictures (same art, per-platform sizes)
        avatar(os.path.join(OUTDIR, "avatar-master-1024.png"), 1024)
        profiles = [
            ("whatsapp", 640),     # WhatsApp profile (circle crop)
            ("facebook", 500),     # Facebook profile (recommended 320+, 500 = crisp)
            ("tiktok", 400),       # TikTok profile (recommended 200+, 400 = crisp)
        ]
        for name, s in profiles:
            avatar(os.path.join(OUTDIR, f"profile-{name}-{s}.png"), s)
        # Covers / banners — (name, width, height, wordmark width fraction)
        banners = [
            ("x-1500x500", 1500, 500, 0.60),                 # X / Twitter header
            ("facebook-1640x624", 1640, 624, 0.56),          # Facebook cover
            ("linkedin-page-1536x768", 1536, 768, 0.56),     # LinkedIn page cover
            ("linkedin-personal-1584x396", 1584, 396, 0.60), # LinkedIn personal cover
            ("youtube-2560x1440", 2560, 1440, 0.42),         # YouTube art (TV-safe centre)
            ("discord-1920x480", 1920, 480, 0.58),           # Discord / generic
        ]
        for name, W, H, fr in banners:
            banner(os.path.join(OUTDIR, f"banner-{name}.png"), W, H, wm_frac=fr)
