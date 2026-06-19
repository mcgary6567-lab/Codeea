"""Trevolto cover / hero image: logo + app mockup on the teal/smoke brand.
Run with --sample for cover_sample.png; no args writes the final into branding/."""
import os, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
MKT = os.path.join(HERE, "..", "Trevolto Marketing")
COVDIR = os.path.join(MKT, "covers")     # facebook cover etc.
HERODIR = os.path.join(MKT, "hero")      # website hero (responsive)
SMOKE=(27,31,36); TEAL=(45,212,191); LGREEN=(74,222,128); TXT=(230,237,243); DIM=(154,164,175)
FONTS=os.path.join(os.path.dirname(__import__("matplotlib").__file__),"mpl-data","fonts","ttf")
def font(sz,bold=True): return ImageFont.truetype(os.path.join(FONTS,"DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),sz)
def trim(im): return im.crop(im.getbbox())

def glow(size,center,radius,color,alpha=70):
    g=Image.new("RGBA",size,(0,0,0,0))
    ImageDraw.Draw(g).ellipse([center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius],fill=color+(alpha,))
    return g.filter(ImageFilter.GaussianBlur(radius*0.5))

def rounded_shadow(size, box, rad, blur, alpha):
    s=Image.new("RGBA",size,(0,0,0,0))
    ImageDraw.Draw(s).rounded_rectangle(box,rad,fill=(0,0,0,alpha))
    return s.filter(ImageFilter.GaussianBlur(blur))

def cover(path, W=1920, H=1080):
    im=Image.new("RGBA",(W,H),SMOKE+(255,))
    im=Image.alpha_composite(im,glow((W,H),(int(W*0.66),int(H*0.42)),int(H*0.72),TEAL,60))
    # device (app window) on the right with a soft shadow
    dev=Image.open(os.path.join(HERE,"gui_mockup.png")).convert("RGBA")
    dw=980; dev=dev.resize((dw,int(dev.height*dw/dev.width)),Image.LANCZOS)
    dx=W-dw-70; dy=(H-dev.height)//2
    im=Image.alpha_composite(im,rounded_shadow((W,H),[dx+18,dy+30,dx+dw+18,dy+dev.height+30],24,34,150))
    im.alpha_composite(dev,(dx,dy))
    # left text column
    d=ImageDraw.Draw(im)
    wm=trim(Image.open(os.path.join(HERE,"trevolto_logo.png")).convert("RGBA"))
    ww=440; wm=wm.resize((ww,int(wm.height*ww/wm.width)),Image.LANCZOS)
    x=110; im.alpha_composite(wm,(x,150))
    d.text((x+4,330),"Automated crypto",font=font(70),fill=TXT)
    d.text((x+4,410),"trading, on autopilot",font=font(70),fill=TEAL)
    d.text((x+6,540),"Set your risk once — the bot trades 24/7,",font=font(30,False),fill=DIM)
    d.text((x+6,582),"hands-free, on your own PC.",font=font(30,False),fill=DIM)
    d.text((x+6,672),"● Windows & macOS   ·   start your free trial",font=font(28,False),fill=LGREEN)
    # tiny disclaimer
    d.text((x+6,H-70),"Illustrative UI. Crypto trading carries risk.",font=font(20,False),fill=DIM)
    im.convert("RGB").save(path,quality=92); print("wrote",os.path.basename(path),im.size)

def fb_cover(path, W=1920, H=731):
    """Facebook page cover (full-HD width at FB's 820:312 ratio): chevron icon +
    white headline (left) and money-back badge + CTA (right). No mockup, content
    kept to the top so the Page profile picture (bottom-left) never collides."""
    im=Image.new("RGBA",(W,H),SMOKE+(255,))
    im=Image.alpha_composite(im,glow((W,H),(int(W*0.70),int(H*0.46)),int(H*0.95),TEAL,55))
    d=ImageDraw.Draw(im)
    # LEFT: icon + headline + subline
    x=110
    ic=trim(Image.open(os.path.join(HERE,"logo.png")).convert("RGBA"))
    ih=150; ic=ic.resize((int(ic.width*ih/ic.height),ih),Image.LANCZOS)
    im.alpha_composite(ic,(x,60)); d=ImageDraw.Draw(im)
    d.text((x+4,250),"Stop watching charts.",font=font(60),fill=TXT)
    d.text((x+4,326),"Let it trade for you.",font=font(60),fill=TXT)
    d.text((x+6,440),"Professional automated crypto trading — Windows & macOS",
           font=font(28,False),fill=(208,215,222))
    # RIGHT: money-back badge + CTA
    rx,rw=1180,640
    byy,bh=250,80
    d.rounded_rectangle([rx,byy,rx+rw,byy+bh],bh//2,outline=LGREEN,width=4)
    t="✓  14-DAY MONEY-BACK GUARANTEE"; f=font(26)
    d.text((rx+(rw-d.textlength(t,font=f))//2,byy+bh//2-16),t,font=f,fill=LGREEN)
    cy,ch=byy+bh+34,116
    d.rounded_rectangle([rx,cy,rx+rw,cy+ch],ch//2,fill=TEAL)
    cta="Get Instant Access Now  →"; f=font(34)
    d.text((rx+(rw-d.textlength(cta,font=f))//2,cy+ch//2-22),cta,font=f,fill="#04231d")
    im.convert("RGB").save(path,quality=94); print("wrote",os.path.basename(path),im.size)


def hero(path, W=1920, H=1080):
    """Website hero: the app window on a TRANSPARENT background (just a soft
    shadow), rendered with full-white text. For the site's own hero section."""
    import make_mockup as m
    old=(m.TXT, m.DIM); m.TXT="#ffffff"; m.DIM="#ffffff"
    tmp=os.path.join(HERE,"_mock_white_tmp.png")
    try:
        m.render(tmp, safe=False)
    finally:
        m.TXT, m.DIM = old
    im=Image.new("RGBA",(W,H),(0,0,0,0))
    dev=Image.open(tmp).convert("RGBA")
    dw=1480; dev=dev.resize((dw,int(dev.height*dw/dev.width)),Image.LANCZOS)
    dx=(W-dw)//2; dy=(H-dev.height)//2
    im=Image.alpha_composite(im,rounded_shadow((W,H),[dx+22,dy+40,dx+dw+22,dy+dev.height+40],26,46,150))
    im.alpha_composite(dev,(dx,dy))
    im.save(path); os.remove(tmp)
    print("wrote",os.path.basename(path),im.size)


HEAD = ["Stop watching charts.", "Let it trade for you."]
SUB = "Professional automated crypto trading — Windows & macOS"
BADGE = "✓  14-DAY MONEY-BACK GUARANTEE"
CTA = "Get Instant Access Now  →"


def _pill(d, x, y, w, h, fill=None, outline=None):
    d.rounded_rectangle([x, y, x + w, y + h], h // 2, fill=fill, outline=outline, width=4)


def _centred(d, cx, y, t, f, fill):
    d.text((cx - d.textlength(t, font=f) // 2, y), t, font=f, fill=fill)


def promo(path, W, H, mode="split"):
    """Platform cover in the Trevolto promo style (icon + white headline +
    money-back badge + 'Get Instant Access Now' CTA). 'split' = icon/headline
    left, badge/CTA right (wide banners). 'center' = centred stack (YouTube)."""
    im = Image.new("RGBA", (W, H), SMOKE + (255,))
    gx = int(W * 0.70) if mode == "split" else W // 2
    im = Image.alpha_composite(im, glow((W, H), (gx, int(H * 0.46)), int(H * 0.9), TEAL, 55))
    d = ImageDraw.Draw(im)
    ic = trim(Image.open(os.path.join(HERE, "logo.png")).convert("RGBA"))

    if mode == "center":                       # YouTube — fit inside the TV-safe centre
        cx = W // 2
        hf = font(64); bf = font(30); cf = font(40)
        lh = 76; bh = 72; ch = 104
        block = lh * 2 + 28 + bh + 24 + ch     # ~380px, fits the 423-tall safe zone
        y = H // 2 - block // 2
        _centred(d, cx, y, HEAD[0], hf, TXT); y += lh
        _centred(d, cx, y, HEAD[1], hf, TXT); y += lh + 28
        bw = 760; _pill(d, cx - bw // 2, y, bw, bh, outline=LGREEN)
        _centred(d, cx, y + bh // 2 - 18, BADGE, bf, LGREEN); y += bh + 24
        cw = 760; _pill(d, cx - cw // 2, y, cw, ch, fill=TEAL)
        _centred(d, cx, y + ch // 2 - 25, CTA, cf, "#04231d")
        im.convert("RGB").save(path, quality=94); print("wrote", os.path.basename(path), im.size); return

    k = H / 731.0                              # split layout, auto-scaled to height
    x = int(W * 0.055)
    ih = int(150 * k); ic = ic.resize((int(ic.width * ih / ic.height), ih), Image.LANCZOS)
    im.alpha_composite(ic, (x, int(58 * k))); d = ImageDraw.Draw(im)
    hf = font(int(60 * k))
    y1 = int(248 * k)
    d.text((x + 4, y1), HEAD[0], font=hf, fill=TXT)
    d.text((x + 4, y1 + int(60 * k) + int(16 * k)), HEAD[1], font=hf, fill=TXT)
    d.text((x + 6, int(452 * k)), SUB, font=font(int(27 * k), False), fill=(208, 215, 222))
    rx, rw = int(W * 0.60), int(W * 0.345)
    bh = int(80 * k); byy = int(250 * k)
    _pill(d, rx, byy, rw, bh, outline=LGREEN)
    _centred(d, rx + rw // 2, byy + bh // 2 - int(15 * k), BADGE, font(int(25 * k)), LGREEN)
    ch = int(116 * k); cy = byy + bh + int(34 * k)
    _pill(d, rx, cy, rw, ch, fill=TEAL)
    _centred(d, rx + rw // 2, cy + ch // 2 - int(20 * k), CTA, font(int(33 * k)), "#04231d")
    im.convert("RGB").save(path, quality=94); print("wrote", os.path.basename(path), im.size)


if __name__=="__main__":
    os.makedirs(COVDIR,exist_ok=True); os.makedirs(HERODIR,exist_ok=True)
    if "--sample" in sys.argv:
        cover(os.path.join(HERE,"cover_sample.png"))
    elif "--fb" in sys.argv:
        fb_cover(os.path.join(COVDIR,"facebook-cover.png"))
    elif "--banners" in sys.argv:
        for name, W, H, mode in [
            ("banner-x-1500x500", 1500, 500, "split"),
            ("banner-linkedin-personal-1584x396", 1584, 396, "split"),
            ("banner-linkedin-page-1536x768", 1536, 768, "split"),
            ("banner-discord-1920x480", 1920, 480, "split"),
            ("banner-youtube-2560x1440", 2560, 1440, "center"),
        ]:
            promo(os.path.join(COVDIR, f"{name}.png"), W, H, mode)
    else:
        base=os.path.join(HERODIR,"website-hero.png")
        hero(base)                                  # 1920x1080 master (also serves as 2x retina)
        master=Image.open(base)
        for w in (1280, 960):                       # lighter responsive sizes for fast page load
            h=int(master.height*w/master.width)
            master.resize((w, h), Image.LANCZOS).save(os.path.join(HERODIR, f"website-hero-{w}.png"))
            print("wrote", f"website-hero-{w}.png", (w, h))
