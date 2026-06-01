"""Modern dark theme for the Tkinter UI.

A single ``apply(root)`` call restyles every ttk widget to a sleek dark look
(TradingView-ish) and exposes the palette so the plain ``tk`` widgets
(BUY/SELL/Connect buttons, status labels) can match.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ---- palette ----
BG = "#0e1117"          # window background
PANEL = "#161b22"       # cards / panels
ELEV = "#1c2331"        # inputs, elevated rows
BORDER = "#2a2f3a"
TXT = "#e6edf3"
TXT_DIM = "#8b949e"
ACCENT = "#f0883e"      # Prometheus flame orange
ACCENT_DK = "#c96a25"
GREEN = "#2ea043"
GREEN_HL = "#3fb950"
RED = "#da3633"
RED_HL = "#f85149"
GREY = "#6e7681"
HEADER = "#0b0e14"


def apply(root: tk.Tk) -> dict:
    root.configure(bg=BG)
    style = ttk.Style(root)
    style.theme_use("clam")

    # Base
    style.configure(".", background=BG, foreground=TXT, fieldbackground=ELEV,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    troughcolor=PANEL, focuscolor=ACCENT)
    # Content surface is PANEL; bordered LabelFrames read as cards on top of it,
    # and the root BG shows as a thin outer margin.
    style.configure("TFrame", background=PANEL)
    style.configure("TLabel", background=PANEL, foreground=TXT)
    style.configure("Dim.TLabel", background=PANEL, foreground=TXT_DIM)
    style.configure("Accent.TLabel", background=PANEL, foreground=ACCENT)

    # Cards
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabelframe", background=PANEL, bordercolor=BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT,
                    font=("Segoe UI Semibold", 10))

    # Inputs
    style.configure("TEntry", fieldbackground=ELEV, foreground=TXT,
                    insertcolor=TXT, bordercolor=BORDER, padding=4)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure("TCombobox", fieldbackground=ELEV, background=ELEV,
                    foreground=TXT, arrowcolor=TXT, bordercolor=BORDER, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", ELEV)],
              bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", ELEV)
    root.option_add("*TCombobox*Listbox.foreground", TXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#000000")

    # Checkbutton
    style.configure("TCheckbutton", background=PANEL, foreground=TXT)
    style.map("TCheckbutton", background=[("active", PANEL)],
              foreground=[("active", ACCENT)],
              indicatorcolor=[("selected", ACCENT), ("!selected", ELEV)])

    # Notebook (tabs)
    style.configure("TNotebook", background=PANEL, bordercolor=BORDER, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=PANEL, foreground=TXT_DIM,
                    padding=(14, 6), font=("Segoe UI", 9))
    style.map("TNotebook.Tab",
              background=[("selected", ELEV)],
              foreground=[("selected", ACCENT)])

    # Treeview (tables)
    style.configure("Treeview", background=ELEV, fieldbackground=ELEV,
                    foreground=TXT, bordercolor=BORDER, rowheight=24)
    style.configure("Treeview.Heading", background=HEADER, foreground=TXT_DIM,
                    relief="flat", font=("Segoe UI Semibold", 9))
    style.map("Treeview.Heading", background=[("active", PANEL)])
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#000000")])

    # Scrollbar / separators / buttons (ttk)
    style.configure("TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=TXT_DIM)
    style.configure("TSeparator", background=BORDER)
    style.configure("TButton", background=ELEV, foreground=TXT, bordercolor=BORDER,
                    relief="flat", padding=6)
    style.map("TButton", background=[("active", BORDER)])

    return {
        "BG": BG, "PANEL": PANEL, "ELEV": ELEV, "BORDER": BORDER, "TXT": TXT,
        "TXT_DIM": TXT_DIM, "ACCENT": ACCENT, "GREEN": GREEN, "GREEN_HL": GREEN_HL,
        "RED": RED, "RED_HL": RED_HL, "GREY": GREY, "HEADER": HEADER,
    }


def style_button(btn: tk.Button, kind: str = "default") -> None:
    """Flat, modern styling for plain tk.Buttons."""
    palette = {
        "buy": (GREEN, "#ffffff", GREEN_HL),
        "sell": (RED, "#ffffff", RED_HL),
        "accent": (ACCENT, "#1a1100", "#ffa057"),
        "danger": (RED, "#ffffff", RED_HL),
        "ghost": (ELEV, TXT, BORDER),
        "default": (ELEV, TXT, BORDER),
    }
    bg, fg, active = palette.get(kind, palette["default"])
    btn.configure(bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                  relief="flat", bd=0, highlightthickness=0,
                  cursor="hand2", font=("Segoe UI Semibold", 10))
