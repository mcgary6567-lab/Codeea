"""Trevolto website logo — horizontal 'chevron mark + TREVOLTO' lockup at the
same size as Prometheus's site logo (img-01.png, 313x48), transparent bg.
The mark is trimmed to its pixels and aligned to the wordmark's cap height for a
tight, balanced lockup. Run: python make_weblogo.py  -> ../site-trevolto/assets/img-01.png"""
import os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "site-trevolto", "assets", "img-01.png")
W, H = 313, 48                                  # match Prometheus img-01.png exactly
C1, C2, C3 = "#2dd4bf", "#4a9eff", "#a855f7"    # chevron gradient (teal->blue->violet)
NAME = "#2dd4bf"                                # teal/cyan wordmark


def chevrons(ax, cx, cy, scale=1.0, gap=0.55):
    for c, off in zip([C3, C2, C1], [0.0, gap, gap * 2]):
        pts = [[cx-1.1, cy-0.7+off], [cx, cy+0.2+off], [cx+1.1, cy-0.7+off],
               [cx+1.1, cy-0.30+off], [cx, cy+0.6+off], [cx-1.1, cy-0.30+off]]
        ax.add_patch(Polygon([[x*scale, y*scale] for x, y in pts], closed=True, fc=c, ec="none"))


def _render_icon(p):
    fig, ax = plt.subplots(figsize=(4, 4), dpi=300); fig.patch.set_alpha(0)
    ax.set_xlim(-1.3, 1.3); ax.set_ylim(-1.3, 1.8); ax.axis("off"); chevrons(ax, 0, 0)
    fig.savefig(p, dpi=300, transparent=True, bbox_inches="tight", pad_inches=0); plt.close(fig)


def _render_text(p):
    fig, ax = plt.subplots(figsize=(12, 3), dpi=300); fig.patch.set_alpha(0)
    ax.set_xlim(0, 12); ax.set_ylim(0, 3); ax.axis("off")
    ax.text(0.05, 1.5, "TREVOLTO", color=NAME, fontsize=90, fontweight="bold", va="center", ha="left")
    fig.savefig(p, dpi=300, transparent=True, bbox_inches="tight", pad_inches=0); plt.close(fig)


def build_lockup():
    """Return a tightly-composed transparent RGBA lockup (mark + TREVOLTO)."""
    ico_p, txt_p = os.path.join(HERE, "_ico.png"), os.path.join(HERE, "_txt.png")
    _render_icon(ico_p); _render_text(txt_p)
    ico = Image.open(ico_p).convert("RGBA"); ico = ico.crop(ico.getbbox())
    txt = Image.open(txt_p).convert("RGBA"); txt = txt.crop(txt.getbbox())
    h = int(txt.height * 1.12)                  # mark height ~= cap height
    ico = ico.resize((int(ico.width * h / ico.height), h), Image.LANCZOS)
    gap = int(ico.width * 0.34)
    lock = Image.new("RGBA", (ico.width + gap + txt.width, max(ico.height, txt.height)), (0, 0, 0, 0))
    lock.alpha_composite(ico, (0, (lock.height - ico.height) // 2))
    lock.alpha_composite(txt, (ico.width + gap, (lock.height - txt.height) // 2))
    for f in (ico_p, txt_p):
        os.remove(f)
    return lock


def main():
    lock = build_lockup()
    lock.thumbnail((W, H), Image.LANCZOS)       # fit into exactly 313x48 (contain)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(lock, (0, (H - lock.height) // 2))
    canvas.save(OUT)
    print(f"wrote {os.path.normpath(OUT)}  ({W}x{H}, transparent, teal wordmark)")


if __name__ == "__main__":
    main()
