"""Modern dark theme for the Tkinter UI.

A single ``apply(root)`` call restyles every ttk widget to a sleek dark look
(TradingView-ish) and exposes the palette so the plain ``tk`` widgets
(BUY/SELL/Connect buttons, status labels) can match.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

# ---- platform UI font ----
# Use each OS's native UI font so the app doesn't look foreign. Windows keeps
# Segoe UI (incl. its real Semibold family); macOS uses Helvetica Neue (San-
# Francisco-like, always present); other platforms use DejaVu Sans. Reference
# theme.UI / theme.UI_SB instead of hardcoding a family.
if sys.platform == "darwin":
    UI = "Helvetica Neue"
    UI_SB = "Helvetica Neue"
elif sys.platform.startswith("win"):
    UI = "Segoe UI"
    UI_SB = "Segoe UI Semibold"
else:
    UI = "DejaVu Sans"
    UI_SB = "DejaVu Sans"

# ---- palette ----
BG = "#1b1f24"          # window background (smoke gray)
PANEL = "#23282e"       # cards / panels
ELEV = "#2c323a"        # inputs, elevated rows
BORDER = "#3a424b"
TXT = "#e6edf3"
TXT_DIM = "#9aa4af"
ACCENT = "#2dd4bf"      # Trevolto teal/cyan
ACCENT_DK = "#22b3a0"
ACCENT_TX = "#04231d"   # dark text for readability on teal buttons
GREEN = "#2ea043"
GREEN_HL = "#3fb950"
RED = "#da3633"
RED_HL = "#f85149"
GREY = "#6e7681"
HEADER = "#15181c"


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
                    font=(UI_SB, 10))

    # Inputs
    style.configure("TEntry", fieldbackground=ELEV, foreground=TXT,
                    insertcolor=TXT, bordercolor=BORDER, padding=4)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    # Invalid-input variant: red field so a bad number is obvious at a glance.
    style.configure("Invalid.TEntry", fieldbackground="#3a1d1d", foreground=TXT,
                    insertcolor=TXT, bordercolor=RED, padding=4)
    style.map("Invalid.TEntry", bordercolor=[("focus", RED), ("!focus", RED)])
    style.configure("TCombobox", fieldbackground=ELEV, background=ELEV,
                    foreground=TXT, arrowcolor=TXT, bordercolor=BORDER, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", ELEV)],
              bordercolor=[("focus", ACCENT)])
    root.option_add("*TCombobox*Listbox.background", ELEV)
    root.option_add("*TCombobox*Listbox.foreground", TXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#000000")

    # Every classic tk.Button defaults to teal/cyan (the brand primary) so the
    # whole app is consistent without styling each call site. Buttons that set
    # their own colour win — BUY (green), SELL/PANIC (red), or anything routed
    # through style_button below.
    root.option_add("*Button.background", ACCENT)
    root.option_add("*Button.foreground", ACCENT_TX)
    root.option_add("*Button.activeBackground", ACCENT_DK)
    root.option_add("*Button.activeForeground", ACCENT_TX)
    root.option_add("*Button.relief", "flat")
    root.option_add("*Button.borderWidth", 0)
    root.option_add("*Button.highlightThickness", 0)

    # Checkbutton
    style.configure("TCheckbutton", background=PANEL, foreground=TXT)
    style.map("TCheckbutton", background=[("active", PANEL)],
              foreground=[("active", ACCENT)],
              indicatorcolor=[("selected", ACCENT), ("!selected", ELEV)])

    # Notebook (tabs) — unselected tabs sit on the darker HEADER bar so the
    # selected (ELEV) tab reads as raised; a hover state adds polish.
    style.configure("TNotebook", background=PANEL, bordercolor=BORDER, tabmargins=(2, 5, 2, 0))
    style.configure("TNotebook.Tab", background=HEADER, foreground=TXT_DIM,
                    padding=(18, 8), font=(UI, 9), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", ELEV), ("active", PANEL)],
              foreground=[("selected", ACCENT), ("active", TXT)])

    # Treeview (tables)
    style.configure("Treeview", background=ELEV, fieldbackground=ELEV,
                    foreground=TXT, bordercolor=BORDER, rowheight=24)
    style.configure("Treeview.Heading", background=HEADER, foreground=TXT_DIM,
                    relief="flat", font=(UI_SB, 9))
    style.map("Treeview.Heading", background=[("active", PANEL)])
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#000000")])

    # Scrollbar / separators / buttons (ttk)
    style.configure("TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=TXT_DIM)
    style.configure("TSeparator", background=BORDER)
    # All standard buttons are teal/cyan (primary brand colour); BUY/SELL/PANIC
    # keep their trading colours via style_button below.
    style.configure("TButton", background=ACCENT, foreground=ACCENT_TX, bordercolor=ACCENT,
                    relief="flat", padding=6)
    style.map("TButton", background=[("active", ACCENT_DK)])

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
                 font=(UI_SB, 14), container_bg=PANEL):
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
        font=(UI, 9), cursor="hand2",
    )
    if wraplength:
        cb.configure(wraplength=wraplength)
    return cb


def style_button(btn: tk.Button, kind: str = "default") -> None:
    """Flat, modern styling for plain tk.Buttons."""
    # Teal/cyan is the primary button colour everywhere; only the directional /
    # destructive trading actions keep green & red.
    palette = {
        "buy": (GREEN, "#ffffff", GREEN_HL),
        "sell": (RED, "#ffffff", RED_HL),
        "accent": (ACCENT, ACCENT_TX, ACCENT_DK),
        "danger": (RED, "#ffffff", RED_HL),
        "ghost": (ACCENT, ACCENT_TX, ACCENT_DK),
        "default": (ACCENT, ACCENT_TX, ACCENT_DK),
    }
    bg, fg, active = palette.get(kind, palette["default"])
    btn.configure(bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                  relief="flat", bd=0, highlightthickness=0,
                  cursor="hand2", font=(UI_SB, 10))


class _Tooltip:
    """A lightweight hover tooltip — a small dark popup that appears after a
    short delay when the pointer rests on ``widget``. Lets the UI drop most of
    its inline grey hint labels in favour of on-demand help."""

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:  # noqa: BLE001
                pass
            self._after = None

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 14
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, bg=ELEV, fg=TXT, justify="left",
                 relief="solid", bd=1, padx=8, pady=5, wraplength=300,
                 font=(UI, 9), highlightbackground=BORDER).pack()

    def _hide(self, _=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def add_tooltip(widget, text: str) -> _Tooltip:
    """Attach a hover tooltip to ``widget``. Returns the controller (keep a ref
    if you want to update ``.text`` later)."""
    return _Tooltip(widget, text)


def window_header(parent, title: str, subtitle: str = "") -> dict:
    """A polished, app-style header strip shared by the secondary windows.

    A flame mark + bold title on a darker bar, an optional dim subtitle pinned
    right, finished with a thin accent underline — so the Chart / Analytics /
    Backtest windows read like the main window instead of a bare title label.
    Returns ``{"frame", "title", "subtitle"}`` so callers can update the text
    later (e.g. a live "last updated" subtitle)."""
    head = tk.Frame(parent, bg=HEADER)
    head.pack(fill="x", side="top")
    inner = tk.Frame(head, bg=HEADER)
    inner.pack(fill="x", padx=16, pady=(11, 9))
    tk.Label(inner, text="🔥", bg=HEADER, fg=ACCENT, font=(UI, 13)).pack(side="left")
    title_lbl = tk.Label(inner, text=title, bg=HEADER, fg=TXT,
                         font=(UI_SB, 14))
    title_lbl.pack(side="left", padx=(8, 0))
    sub_lbl = tk.Label(inner, text=subtitle, bg=HEADER, fg=TXT_DIM, font=(UI, 9))
    sub_lbl.pack(side="right")
    tk.Frame(head, bg=ACCENT, height=2).pack(fill="x")
    return {"frame": head, "title": title_lbl, "subtitle": sub_lbl}


def section_label(parent, text: str, bg: str = BG) -> tk.Label:
    """A consistent accent sub-heading used between panels."""
    return tk.Label(parent, text=text, bg=bg, fg=ACCENT, font=(UI_SB, 12))


def toolbar_button(parent, text: str, command=None, kind: str = "default",
                   tooltip: str = "") -> tk.Button:
    """A flat, theme-styled toolbar button (consistent padding + hover + optional
    tooltip), so every window's buttons match instead of each rolling its own."""
    btn = tk.Button(parent, text=text, command=command, padx=14, pady=5)
    style_button(btn, kind)
    if tooltip:
        add_tooltip(btn, tooltip)
    return btn


def bind_escape_close(win, on_close) -> None:
    """Let Esc close a window — a small pro touch users expect."""
    try:
        win.bind("<Escape>", lambda _e: on_close())
    except tk.TclError:
        pass


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
