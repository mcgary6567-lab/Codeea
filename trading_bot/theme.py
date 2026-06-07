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


class RoundedButton(tk.Canvas):
    """A flat button with rounded corners (tk.Button can't round corners).

    Supports ``.config(state=...)`` / ``text=`` / ``bg=`` like a normal button
    and redraws responsively when the widget is stretched by its geometry mgr.
    """

    def __init__(self, parent, text="", command=None, bg=ACCENT, fg="#ffffff",
                 active=None, radius=5, width=120, height=42,
                 font=("Segoe UI Semibold", 14), container_bg=PANEL):
        super().__init__(parent, width=width, height=height, bg=container_bg,
                         highlightthickness=0, bd=0)
        self._text = text
        self._bg = bg
        self._fg = fg
        self._active = active or bg
        self._radius = radius
        self._font = font
        self._command = command
        self._state = "normal"
        self._cur = bg
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _round_points(self, x1, y1, x2, y2, r):
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        r = self._radius
        fill = "#3a3f4b" if self._state == "disabled" else self._cur
        fg = TXT_DIM if self._state == "disabled" else self._fg
        self.create_polygon(self._round_points(1, 1, w - 1, h - 1, r),
                            smooth=True, splinesteps=12, fill=fill, outline=fill)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _on_enter(self, _):
        if self._state == "normal":
            self._cur = self._active
            self._draw()

    def _on_leave(self, _):
        self._cur = self._bg
        self._draw()

    def _on_press(self, _):
        if self._state == "normal":
            self._cur = self._active
            self._draw()

    def _on_release(self, _):
        if self._state == "normal" and self._command:
            self._command()

    def configure(self, **kw):  # noqa: A003 - mirror tk widget API
        if "state" in kw:
            self._state = kw.pop("state")
        if "text" in kw:
            self._text = kw.pop("text")
        if "bg" in kw:
            self._bg = self._cur = kw.pop("bg")
        if "command" in kw:
            self._command = kw.pop("command")
        self._draw()
        if kw:
            super().configure(**kw)

    config = configure


def make_check(parent, text: str = "", variable: tk.BooleanVar | None = None,
               command=None, wraplength: int = 0) -> tk.Checkbutton:
    """A dark-theme checkbox using the native indicator.

    Uses a classic ``tk.Checkbutton`` so the on/off states are an unmistakable
    ticked box vs. empty box on every Windows version — clicking reliably
    toggles ``variable`` and fires ``command``. (The ttk/clam indicator drew a
    small X-like mark, and a Unicode glyph couldn't show a clear empty box on
    all systems — hence the native widget.)

    Returns a ``tk.Checkbutton`` so callers can chain ``.grid(...)`` / ``.pack(...)``.
    """
    if variable is None:
        variable = tk.BooleanVar(value=False)
    cb = tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        onvalue=True, offvalue=False,
        bg=PANEL, fg=TXT, selectcolor=ELEV,
        activebackground=PANEL, activeforeground=ACCENT,
        anchor="w", justify="left", highlightthickness=0, bd=0,
        font=("Segoe UI", 9), cursor="hand2",
    )
    if wraplength:
        cb.configure(wraplength=wraplength)
    return cb


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


def fit_window(win, w: int, h: int, min_w: int = 0, min_h: int = 0,
               margin: int = 80) -> None:
    """Size ``win`` to (w, h) but never larger than the screen, and center it.

    Makes every window fit any monitor (small laptops included). ``min_w/min_h``
    set the resize floor, themselves capped to the actual (possibly smaller)
    window so the minimum can never exceed the screen."""
    try:
        win.update_idletasks()
        sw = win.winfo_screenwidth() or w
        sh = win.winfo_screenheight() or h
        w = min(int(w), max(320, sw - margin))
        h = min(int(h), max(240, sh - margin))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)        # a touch above center
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.minsize(min(min_w or w, w), min(min_h or h, h))
    except Exception:  # noqa: BLE001 - never let sizing crash a window
        win.geometry(f"{int(w)}x{int(h)}")
