"""Trevolto one-page brand guidelines sheet (visual). -> ../Trevolto Marketing/brand/brand-guidelines.png"""
import os
from PIL import Image, ImageDraw, ImageFont
HERE=os.path.dirname(os.path.abspath(__file__))
OUT=os.path.join(HERE,"..","Trevolto Marketing","brand","brand-guidelines.png")
os.makedirs(os.path.dirname(OUT),exist_ok=True)
FONTS=os.path.join(os.path.dirname(__import__("matplotlib").__file__),"mpl-data","fonts","ttf")
B=lambda s:ImageFont.truetype(os.path.join(FONTS,"DejaVuSans-Bold.ttf"),s)
R=lambda s:ImageFont.truetype(os.path.join(FONTS,"DejaVuSans.ttf"),s)
SMOKE=(27,31,36); PANEL=(35,40,46); TEAL=(45,212,191); LGREEN=(74,222,128)
WHITE=(240,243,247); DIM=(154,164,175)
def trim(im): return im.crop(im.getbbox())

W,Hh=1600,2280
im=Image.new("RGB",(W,Hh),SMOKE); d=ImageDraw.Draw(im)
x=90
d.text((x,70),"TREVOLTO",font=B(60),fill=TEAL)
d.text((x,150),"Brand Guidelines",font=R(34),fill=DIM)
d.line([(x,215),(W-90,215)],fill=(58,66,75),width=2)

def head(y,t): d.text((x,y),t,font=B(30),fill=WHITE); return y+52

# LOGO
y=head(255,"1 · Logo")
wm=trim(Image.open(os.path.join(HERE,"trevolto_logo.png")).convert("RGBA"))
ww=820; wm=wm.resize((ww,int(wm.height*ww/wm.width)),Image.LANCZOS)
card=Image.new("RGB",(W-180,260),PANEL); im.paste(card,(x,y))
im.paste(wm,(x+60,y+ (260-wm.height)//2),wm)
ic=trim(Image.open(os.path.join(HERE,"logo.png")).convert("RGBA"))
ih=180; ic=ic.resize((int(ic.width*ih/ic.height),ih),Image.LANCZOS)
im.paste(ic,(W-90-60-ic.width,y+(260-ih)//2),ic)
y+=290
d.text((x,y),"Primary lockup (mark + wordmark) and the standalone chevron mark. Keep clear space ≥ the mark's width.",font=R(22),fill=DIM); y+=70

# COLORS
y=head(y,"2 · Colour")
sw=[("Teal · Primary","#2dd4bf"),("Cyan","#22d3ee"),("Light Green","#4ade80"),
    ("Smoke · BG","#1b1f24"),("Panel","#23282e"),("Text","#ffffff")]
tw=(W-180-5*20)//6
for i,(name,hexv) in enumerate(sw):
    sx=x+i*(tw+20); col=tuple(int(hexv[j:j+2],16) for j in (1,3,5))
    d.rounded_rectangle([sx,y,sx+tw,y+150],12,fill=col,outline=(58,66,75),width=2)
    d.text((sx,y+162),name,font=B(18),fill=WHITE)
    d.text((sx,y+186),hexv,font=R(18),fill=DIM)
y+=240
d.text((x,y),"Mark gradient: #2dd4bf → #4a9eff → #a855f7 (teal→blue→violet). Don't recolour it.",font=R(22),fill=DIM); y+=70

# TYPOGRAPHY
y=head(y,"3 · Typography")
d.text((x,y),"Aa  Bb  Cc  0123456789",font=B(58),fill=WHITE); y+=86
d.text((x,y),"Headings: Segoe UI Semibold (Win) · Helvetica Neue (mac) · DejaVu Sans (fallback).",font=R(22),fill=DIM); y+=34
d.text((x,y),"Body: same family, regular. Wordmark is set in bold, letter-spaced caps.",font=R(22),fill=DIM); y+=70

# DO / DON'T
y=head(y,"4 · Do & Don't")
dos=["Use teal as the primary accent.","Place the logo on smoke / dark backgrounds.",
     "Keep generous clear space around the mark.","Keep BUY green, SELL & PANIC red in the UI."]
donts=["Recolour the chevron gradient or wordmark.","Stretch, rotate, or add effects/shadows to the logo.",
       "Place it on busy or low-contrast backgrounds.","Promise profit — always label figures illustrative."]
cw=(W-180-40)//2
d.text((x,y),"DO",font=B(24),fill=LGREEN); d.text((x+cw+40,y),"DON'T",font=B(24),fill=(248,81,73)); y+=44
for i in range(4):
    d.text((x,y),"✓ "+dos[i],font=R(21),fill=WHITE)
    d.text((x+cw+40,y),"✕ "+donts[i],font=R(21),fill=DIM)
    y+=40
d.text((x,Hh-70),"trevolto.com · support@trevolto.com · Crypto trading carries risk. Not financial advice.",font=R(20),fill=DIM)
im.save(OUT,quality=94); print("wrote",os.path.normpath(OUT),im.size)
