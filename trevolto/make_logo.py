"""Generate the Trevolto logo set (Flow-chevron mark).
Outputs: logo.png (square app mark), logo_wordmark.png, favicon.png."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

C1, C2, C3 = "#2dd4bf", "#4a9eff", "#a855f7"   # teal -> blue -> violet
TXT, DIM = "#e6edf3", "#8b949e"
NAME = "#4a9eff"   # wordmark "TREVOLTO" — signal blue
TAG = "#4ade80"    # tagline — light green


def chevrons(ax, cx, cy, scale=1.0, gap=0.55):
    for c, off in zip([C3, C2, C1], [0.0, gap, gap * 2]):
        pts = [[cx - 1.1, cy - 0.7 + off], [cx, cy + 0.2 + off], [cx + 1.1, cy - 0.7 + off],
               [cx + 1.1, cy - 0.30 + off], [cx, cy + 0.6 + off], [cx - 1.1, cy - 0.30 + off]]
        ax.add_patch(Polygon([[x * scale, y * scale] for x, y in pts], closed=True, fc=c, ec="none"))


def save_mark(path, px=512):
    fig, ax = plt.subplots(figsize=(px / 100, px / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax.set_xlim(-1.6, 1.6); ax.set_ylim(-1.7, 1.9); ax.axis("off")
    chevrons(ax, 0, -0.1)
    fig.savefig(path, dpi=100, transparent=True, bbox_inches="tight", pad_inches=0.12)
    plt.close(fig); print("wrote", path)


def save_wordmark(path):
    fig, ax = plt.subplots(figsize=(12, 3.4), dpi=120)
    fig.patch.set_alpha(0)
    ax.set_xlim(-3, 13); ax.set_ylim(-2, 2); ax.axis("off")
    # mark vertically centred against the full text block (name + tagline)
    chevrons(ax, -1.45, -0.45, scale=0.92)
    ax.text(0.35, 0.5, "TREVOLTO", color=NAME, fontsize=46, fontweight="bold", va="center", ha="left")
    ax.text(0.40, -0.72, "Automated Crypto Trading", color=TAG, fontsize=16.5, va="center", ha="left")
    fig.savefig(path, dpi=120, transparent=True, bbox_inches="tight", pad_inches=0.2)
    plt.close(fig); print("wrote", path)


if __name__ == "__main__":
    save_mark("logo.png", 512)
    save_wordmark("logo_wordmark.png")
    save_mark("favicon.png", 128)
