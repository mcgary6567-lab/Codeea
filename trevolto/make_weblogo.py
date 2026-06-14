"""Trevolto website logo — horizontal 'chevron mark + TREVOLTO' lockup at the
same size as Prometheus's site logo (img-01.png, 313x48), transparent bg.
Run: python make_weblogo.py  -> ../site-trevolto/assets/img-01.png"""
import os
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "site-trevolto", "assets", "img-01.png")
W, H = 313, 48                      # match Prometheus img-01.png exactly
C1, C2, C3 = "#2dd4bf", "#4a9eff", "#a855f7"
NAME = "#4a9eff"                    # blue wordmark


def chevrons(ax, cx, cy, scale=1.0, gap=0.55):
    for c, off in zip([C3, C2, C1], [0.0, gap, gap * 2]):
        pts = [[cx-1.1, cy-0.7+off], [cx, cy+0.2+off], [cx+1.1, cy-0.7+off],
               [cx+1.1, cy-0.30+off], [cx, cy+0.6+off], [cx-1.1, cy-0.30+off]]
        ax.add_patch(Polygon([[x*scale, y*scale] for x, y in pts], closed=True, fc=c, ec="none"))


def main():
    # render the horizontal lockup tight + transparent at 4x for crispness
    fig, ax = plt.subplots(figsize=(W*4/100, H*4/100), dpi=100)
    fig.patch.set_alpha(0)
    ax.set_xlim(-2.6, 13); ax.set_ylim(-1.7, 1.7); ax.axis("off")
    chevrons(ax, -1.4, -0.15, scale=0.62)
    ax.text(0.05, 0.0, "TREVOLTO", color=NAME, fontsize=58, fontweight="bold",
            va="center", ha="left", family="DejaVu Sans")
    tmp = os.path.join(HERE, "_weblogo_tmp.png")
    fig.savefig(tmp, dpi=100, transparent=True, bbox_inches="tight", pad_inches=0.06)
    plt.close(fig)
    # fit into exactly 313x48 (contain), left-aligned, transparent canvas
    im = Image.open(tmp).convert("RGBA")
    im.thumbnail((W, H), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.alpha_composite(im, (0, (H - im.height) // 2))
    canvas.save(OUT)
    os.remove(tmp)
    print(f"wrote {os.path.normpath(OUT)}  ({canvas.size[0]}x{canvas.size[1]}, transparent)")


if __name__ == "__main__":
    main()
