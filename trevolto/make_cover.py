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
    """Facebook cover (full-HD width at FB's 820:312 ratio): logo + headline +
    a large, crisp app mockup. Key content kept clear of the bottom-left where
    the Page profile picture overlaps."""
    im=Image.new("RGBA",(W,H),SMOKE+(255,))
    im=Image.alpha_composite(im,glow((W,H),(int(W*0.64),int(H*0.46)),int(H*0.95),TEAL,60))
    dev=Image.open(os.path.join(HERE,"gui_mockup.png")).convert("RGBA")
    dw=1020; dev=dev.resize((dw,int(dev.height*dw/dev.width)),Image.LANCZOS)
    dx=W-dw-60; dy=(H-dev.height)//2
    im=Image.alpha_composite(im,rounded_shadow((W,H),[dx+18,dy+26,dx+dw+18,dy+dev.height+26],22,34,150))
    im.alpha_composite(dev,(dx,dy))
    d=ImageDraw.Draw(im)
    wm=trim(Image.open(os.path.join(HERE,"trevolto_logo.png")).convert("RGBA"))
    ww=360; wm=wm.resize((ww,int(wm.height*ww/wm.width)),Image.LANCZOS)
    x=95; im.alpha_composite(wm,(x,78))
    d.text((x+4,250),"Crypto trading,",font=font(50),fill=TXT)
    d.text((x+4,308),"on autopilot",font=font(50),fill=TEAL)
    d.text((x+6,410),"The bot trades 24/7, hands-free.",font=font(24,False),fill=DIM)
    d.text((x+6,468),"● Windows & macOS",font=font(24,False),fill=LGREEN)
    d.text((x+6,H-48),"Illustrative UI. Crypto trading carries risk.",font=font(18,False),fill=DIM)
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


if __name__=="__main__":
    os.makedirs(COVDIR,exist_ok=True); os.makedirs(HERODIR,exist_ok=True)
    if "--sample" in sys.argv:
        cover(os.path.join(HERE,"cover_sample.png"))
    elif "--fb" in sys.argv:
        fb_cover(os.path.join(COVDIR,"facebook-cover.png"))
    else:
        base=os.path.join(HERODIR,"website-hero.png")
        hero(base)                                  # 1920x1080 master (also serves as 2x retina)
        master=Image.open(base)
        for w in (1280, 960):                       # lighter responsive sizes for fast page load
            h=int(master.height*w/master.width)
            master.resize((w, h), Image.LANCZOS).save(os.path.join(HERODIR, f"website-hero-{w}.png"))
            print("wrote", f"website-hero-{w}.png", (w, h))
