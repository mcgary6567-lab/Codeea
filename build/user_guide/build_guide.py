"""Build the complete user guide PDF for the Online Quran College platform.

Inputs (all in this folder): pages.json (what every page shows), shots/*.png (screenshots), icons/*.png
(Lucide icons), desc_*.json (the written descriptions). Output: OQC_User_Guide.pdf.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

from PIL import Image as PILImage, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether, ListFlowable, ListItem,
                                NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
# Screenshots, page facts and the finished PDF land here (override with GUIDE_DIR).
GUIDE_DIR = Path(os.environ.get("GUIDE_DIR", REPO_ROOT / "build" / "user_guide" / "out"))
GUIDE_DIR.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(REPO_ROOT))
from app.core import nav  # noqa: E402
from app.core.templating import STATUS_LABELS  # noqa: E402

HERE = GUIDE_DIR
TEXT_DIR = Path(__file__).resolve().parent  # the desc_*.json files live beside this script
SHOTS, ICONS, CROPS = HERE / "shots", HERE / "icons", HERE / "crops"
CROPS.mkdir(exist_ok=True)
OUT = HERE / "OQC_User_Guide.pdf"

# ----------------------------------------------------------------------------- fonts and colours
FONTS = Path(r"C:\Windows\Fonts")
pdfmetrics.registerFont(TTFont("UI", str(FONTS / "segoeui.ttf")))
pdfmetrics.registerFont(TTFont("UI-Bold", str(FONTS / "segoeuib.ttf")))
pdfmetrics.registerFont(TTFont("UI-Italic", str(FONTS / "segoeuii.ttf")))
pdfmetrics.registerFont(TTFont("UI-Light", str(FONTS / "segoeuil.ttf")))
pdfmetrics.registerFontFamily("UI", normal="UI", bold="UI-Bold", italic="UI-Italic", boldItalic="UI-Bold")

BRAND = colors.HexColor("#1d6fcf")
BRAND_DARK = colors.HexColor("#155fb3")
INK = colors.HexColor("#1e293b")
MUTED = colors.HexColor("#64748b")
LINE = colors.HexColor("#cbd5e1")
PANEL = colors.HexColor("#f1f5f9")
PANEL_BLUE = colors.HexColor("#e8f1fb")
TIP_BG = colors.HexColor("#fff8e1")
TIP_LINE = colors.HexColor("#e4cd39")
NOTE_BG = colors.HexColor("#eef7f1")
NOTE_LINE = colors.HexColor("#33b479")
WHITE = colors.white

PAGE_W, PAGE_H = A4
M_L, M_R, M_T, M_B = 18 * mm, 18 * mm, 20 * mm, 18 * mm
TEXT_W = PAGE_W - M_L - M_R

# ----------------------------------------------------------------------------- styles
def st(name, **kw):
    base = dict(fontName="UI", fontSize=10, leading=14.5, textColor=INK, spaceAfter=4)
    base.update(kw)
    return ParagraphStyle(name, **base)

S = {
    "body": st("body"),
    "small": st("small", fontSize=8.5, leading=11.5, textColor=MUTED),
    "caption": st("caption", fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER, spaceBefore=2, spaceAfter=8),
    "lead": st("lead", fontSize=11.5, leading=17, textColor=colors.HexColor("#334155")),
    "part": st("part", fontName="UI-Bold", fontSize=30, leading=36, textColor=WHITE),
    "partsub": st("partsub", fontName="UI-Light", fontSize=14, leading=19, textColor=WHITE),
    "H1": st("H1", fontName="UI-Bold", fontSize=20, leading=25, textColor=BRAND_DARK, spaceBefore=6, spaceAfter=8),
    "H2": st("H2", fontName="UI-Bold", fontSize=15, leading=19, textColor=INK, spaceBefore=12, spaceAfter=6),
    "H3": st("H3", fontName="UI-Bold", fontSize=12.5, leading=16, textColor=INK, spaceBefore=10, spaceAfter=4),
    "H4": st("H4", fontName="UI-Bold", fontSize=10, leading=13, textColor=BRAND_DARK, spaceBefore=7, spaceAfter=2),
    "kicker": st("kicker", fontName="UI-Bold", fontSize=8, leading=10, textColor=MUTED, spaceAfter=1),
    "bullet": st("bullet", leftIndent=0, spaceAfter=2),
    "cell": st("cell", fontSize=8.8, leading=11.5, spaceAfter=0),
    "cellb": st("cellb", fontName="UI-Bold", fontSize=8.8, leading=11.5, spaceAfter=0),
    "cellh": st("cellh", fontName="UI-Bold", fontSize=8.5, leading=11, textColor=colors.HexColor("#334155"), spaceAfter=0),
    "toc0": st("toc0", fontName="UI-Bold", fontSize=11, leading=16, textColor=BRAND_DARK, spaceBefore=6),
    "toc1": st("toc1", fontSize=9.5, leading=13, leftIndent=12),
    "toc2": st("toc2", fontSize=8.5, leading=11.5, leftIndent=26, textColor=colors.HexColor("#475569")),
    "cover_title": st("cover_title", fontName="UI-Bold", fontSize=34, leading=40, textColor=WHITE),
    "cover_sub": st("cover_sub", fontName="UI-Light", fontSize=17, leading=22, textColor=WHITE),
    "cover_meta": st("cover_meta", fontSize=10.5, leading=15, textColor=colors.HexColor("#dbeafe")),
}


# ----------------------------------------------------------------------------- text helpers
def esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md(t: str) -> str:
    """**bold** to <b>; plain text is escaped, but the inline tags this script writes itself survive."""
    t = esc((t or "").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">"))  # never double-escape
    t = re.sub(r"&lt;(/?(?:b|i|br/?)|img [^&]*?/)&gt;", lambda m: "<" + m.group(1) + ">", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return t


def P(text: str, style="body") -> Paragraph:
    return Paragraph(md(text), S[style])


def icon_img(name: str, size: float = 11) -> str:
    p = ICONS / f"{name}.png"
    if not p.exists():
        return ""
    return f'<img src="{p}" width="{size}" height="{size}" valign="-2"/> '


def heading(level: str, text: str, icon: str | None = None, key: str | None = None) -> Paragraph:
    inner = (icon_img(icon, {"H1": 16, "H2": 13, "H3": 12}.get(level, 11)) if icon else "") + esc(text)
    para = Paragraph(inner, S[level])
    para._toc_text = text
    para._toc_key = key or re.sub(r"[^a-z0-9]+", "-", text.lower())
    return para


def bullets(items: list[str], style="body", numbered=False) -> ListFlowable:
    kw = dict(bulletType="1", bulletFormat="%s.") if numbered else dict(bulletType="bullet", start="•")
    lf = ListFlowable([ListItem(Paragraph(md(i), S[style]), leftIndent=14) for i in items],
                      bulletFontName="UI-Bold" if numbered else "UI", bulletFontSize=9 if numbered else 8,
                      bulletColor=BRAND, leftIndent=14, **kw)
    lf.spaceAfter = 3
    return lf


def white_icon(name: str) -> Path | None:
    """A white copy of a Lucide icon, for coloured bands."""
    src = ICONS / f"{name}.png"
    if not src.exists():
        return None
    out = CROPS / f"white_{name}.png"
    if not out.exists():
        im = PILImage.open(src).convert("RGBA")
        px = im.load()
        for y in range(im.height):
            for x in range(im.width):
                r, g, b, a = px[x, y]
                px[x, y] = (255, 255, 255, min(a, 255 - min(r, g, b)))
        im.save(out)
    return out


def callout(text: str, kind="tip") -> Table:
    bg, line, icon, label = {"tip": (TIP_BG, TIP_LINE, "lightbulb", "Tip"), "note": (NOTE_BG, NOTE_LINE, "info", "Good to know"),
                             "warn": (colors.HexColor("#fdecea"), colors.HexColor("#e35a4e"), "alert-triangle", "Take care")}[kind]
    body = Paragraph(f"{icon_img(icon, 10)}<b>{label}.</b> " + md(text), S["cell"])
    t = Table([[body]], colWidths=[TEXT_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), bg), ("LINEBEFORE", (0, 0), (0, -1), 2.5, line),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    t.spaceAfter = 6
    return t


def grid(rows: list[list], widths: list[float], header=True, zebra=True, font="cell") -> Table:
    data = []
    for r_i, row in enumerate(rows):
        data.append([c if isinstance(c, (Paragraph, Image, Table)) else Paragraph(md(str(c)), S["cellh" if (header and r_i == 0) else font]) for c in row])
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
             ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), PANEL_BLUE))
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafbfd")))
    t.setStyle(TableStyle(style))
    t.spaceAfter = 8
    return t


# ----------------------------------------------------------------------------- images
def shot(name: str, width: float = TEXT_W, crop: tuple | None = None, border=True, max_h: float | None = None) -> Flowable:
    src = SHOTS / name
    if not src.exists():
        return Paragraph(f"<i>[screenshot {esc(name)} missing]</i>", S["small"])
    # every screenshot is stored once as a 256-colour PNG: the UI is flat colour, so this halves the file
    out = CROPS / (f"{src.stem}_{'_'.join(map(str, crop))}.png" if crop else f"{src.stem}_q.png")
    # rebuilt when the screenshot is newer than the cached copy, or a recapture would be shown stale
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        im = PILImage.open(src).convert("RGB")
        if crop:
            im = im.crop(crop)
        im.quantize(colors=256, method=PILImage.Quantize.MEDIANCUT, dither=PILImage.Dither.NONE).save(out, optimize=True)
    path = out
    with PILImage.open(path) as im:
        w, h = im.size
    scale = width / w
    if max_h and h * scale > max_h:
        scale = max_h / h
    img = Image(str(path), width=w * scale, height=h * scale)
    img.hAlign = "CENTER"
    if border:
        t = Table([[img]], colWidths=[w * scale + 2])
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
        t.hAlign = "CENTER"
        t.spaceAfter = 2
        return t
    return img


MAIN_CROP = (240, 48, 1440, 900)  # the content pane and breadcrumb, without the area sidebar


def annotated(name: str, points: list[tuple[int, int]], out_name: str) -> Path:
    """Draw numbered blue markers on a screenshot."""
    out = CROPS / out_name
    if out.exists():
        return out
    im = PILImage.open(SHOTS / name).convert("RGBA")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(FONTS / "segoeuib.ttf"), 22)
    for i, (x, y) in enumerate(points, 1):
        r = 17
        d.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=(255, 255, 255, 255))
        d.ellipse((x - r, y - r, x + r, y + r), fill=(227, 90, 78, 255))
        tw = d.textlength(str(i), font=font)
        d.text((x - tw / 2, y - 14), str(i), fill="white", font=font)
    im.convert("RGB").save(out)
    return out


# ----------------------------------------------------------------------------- document template
class SetPart(Flowable):
    """Zero-height flowable that tells the running header which part we are in."""
    def __init__(self, label: str):
        super().__init__()
        self.label = label
        self.width = self.height = 0

    def draw(self):
        self.canv._part_label = self.label


class Guide(BaseDocTemplate):
    def __init__(self, path, **kw):
        super().__init__(path, pagesize=A4, leftMargin=M_L, rightMargin=M_R, topMargin=M_T, bottomMargin=M_B,
                         title="Online Quran College — Complete User Guide", author="Online Quran College",
                         subject="User guide for the Digital Operating System, Release 1.1", **kw)
        frame = Frame(M_L, M_B, TEXT_W, PAGE_H - M_T - M_B, id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        full = Frame(0, 0, PAGE_W, PAGE_H, id="full", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id="cover", frames=[full], onPage=self._cover_page),
                               PageTemplate(id="plain", frames=[frame], onPageEnd=self._page)])
        self._keys = set()

    def _cover_page(self, canv, doc):
        pass

    def _startBuild(self, filename=None, canvasmaker=None):
        # multiBuild runs several passes; the bookmark keys must come out identical each time or the
        # table of contents never settles.
        self._keys = set()
        super()._startBuild(filename, canvasmaker)

    def _page(self, canv, doc):
        canv.saveState()
        part = getattr(canv, "_part_label", "")
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(M_L, PAGE_H - 13 * mm, PAGE_W - M_R, PAGE_H - 13 * mm)
        canv.setFont("UI-Bold", 8)
        canv.setFillColor(BRAND_DARK)
        canv.drawString(M_L, PAGE_H - 11.5 * mm, "Online Quran College · User Guide")
        canv.setFont("UI", 8)
        canv.setFillColor(MUTED)
        canv.drawRightString(PAGE_W - M_R, PAGE_H - 11.5 * mm, part)
        canv.line(M_L, M_B - 5 * mm, PAGE_W - M_R, M_B - 5 * mm)
        canv.drawString(M_L, M_B - 9 * mm, "Release 1.1 · " + date.today().strftime("%B %Y"))
        canv.setFont("UI-Bold", 8.5)
        canv.setFillColor(INK)
        canv.drawRightString(PAGE_W - M_R, M_B - 9 * mm, f"Page {doc.page}")
        canv.restoreState()

    def afterFlowable(self, flowable):
        if hasattr(flowable, "_toc_text"):
            level = getattr(flowable, "_toc_level", None)
            if level is None and isinstance(flowable, Paragraph):
                level = {"H1": 0, "H2": 1, "H3": 2}.get(flowable.style.name)
            if level is None:
                return
            key = flowable._toc_key
            n = 1
            base = key
            while key in self._keys:
                n += 1
                key = f"{base}-{n}"
            self._keys.add(key)
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable._toc_text, key, level, closed=level >= 1)
            self.notify("TOCEntry", (level, flowable._toc_text, self.page, key))


# ----------------------------------------------------------------------------- content: cover, front matter
class Cover(Flowable):
    def __init__(self):
        super().__init__()
        self.width, self.height = PAGE_W, PAGE_H

    def draw(self):
        c = self.canv
        c.setFillColor(colors.HexColor("#0f2b4c"))
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        c.setFillColor(BRAND)
        c.rect(0, PAGE_H * 0.42, PAGE_W, PAGE_H * 0.58, fill=1, stroke=0)
        # palette strip
        x = 0
        for col in nav.PALETTE:
            c.setFillColor(colors.HexColor(col))
            c.rect(x, PAGE_H * 0.42 - 6, PAGE_W / len(nav.PALETTE) + 1, 6, fill=1, stroke=0)
            x += PAGE_W / len(nav.PALETTE)
        # logo mark
        c.setFillColor(WHITE)
        c.roundRect(M_L, PAGE_H - 62 * mm, 22 * mm, 22 * mm, 4 * mm, fill=1, stroke=0)
        c.drawImage(str(ICONS / "book-open.png"), M_L + 3 * mm, PAGE_H - 59 * mm, 16 * mm, 16 * mm, mask="auto")
        c.setFillColor(WHITE)
        c.setFont("UI-Bold", 13)
        c.drawString(M_L + 27 * mm, PAGE_H - 48 * mm, "Online Quran College")
        c.setFont("UI-Light", 10)
        c.drawString(M_L + 27 * mm, PAGE_H - 54 * mm, "DIGITAL OPERATING SYSTEM")
        c.setFont("UI-Bold", 36)
        c.drawString(M_L, PAGE_H - 105 * mm, "Complete User Guide")
        c.setFont("UI-Light", 16)
        c.drawString(M_L, PAGE_H - 116 * mm, "Every area, every page, every button, explained in plain English")
        c.setFont("UI", 10.5)
        c.setFillColor(colors.HexColor("#dbeafe"))
        c.drawString(M_L, PAGE_H - 130 * mm, "Online Academics · Billing Management · Human Resource · Employee Self Portal · Accounts")
        c.drawString(M_L, PAGE_H - 136 * mm, "CRM & Growth · Operations · Configuration · Teacher, Family and Student portals")
        # bottom block
        c.setFillColor(WHITE)
        c.setFont("UI-Bold", 11)
        c.drawString(M_L, 40 * mm, "Release 1.1")
        c.setFont("UI", 10)
        c.setFillColor(colors.HexColor("#cbd5e1"))
        c.drawString(M_L, 34 * mm, date.today().strftime("%d %B %Y"))
        c.drawString(M_L, 28 * mm, "Prepared for the staff, teachers, families and students of Online Quran College.")
        c.drawString(M_L, 22 * mm, "Internal document. Keep it with the system and share it with every new user.")


def front_matter(story):
    story.append(NextPageTemplate("plain"))
    story.append(PageBreak())
    story.append(SetPart("About this guide"))
    story.append(heading("H1", "About this guide", "info"))
    story.append(P("This guide explains the whole Online Quran College system, one screen at a time. It is written in simple "
                   "English for people who use the system every day: office staff, heads of department, teachers, parents and students.", "lead"))
    story.append(P("Every page of the system has its own section in this guide. Each section shows a picture of the page, says what the page is for, "
                   "lists what you see on it, and gives step-by-step instructions on how to use it."))
    story.append(heading("H2", "How the guide is organised"))
    story.append(grid([
        ["Part", "What it covers"],
        ["Getting started", "Signing in, the parts of the screen, the menu, search, notifications, and the patterns every page shares."],
        ["The eight areas", "One part per area of the staff system: Online Academics, Billing Management, Human Resource, Employee Self Portal, Accounts, CRM & Growth, Operations and Configuration."],
        ["The three portals", "What a teacher, a parent and a student see when they sign in."],
        ["Appendices", "The words used for statuses, the roles and what each can see, differences from the old portal, and help when something goes wrong."],
    ], [40 * mm, TEXT_W - 40 * mm]))
    story.append(heading("H2", "How to read a page section"))
    story.append(P("Each page in the guide follows the same shape, so you always know where to look:"))
    story.append(grid([
        ["Heading", "What it holds"],
        [f"{icon_img('file-text')}Page name", "The page's icon and name, exactly as it appears in the menu and the sidebar, and where it lives (area › group)."],
        [f"{icon_img('layout-grid')}Screenshot", "A picture of the page as it looks on screen with sample data."],
        [f"{icon_img('info')}What it is for", "The job this page does, in one or two sentences."],
        [f"{icon_img('eye')}On this page", "The counters (tiles), tabs, filters and table columns you will see."],
        [f"{icon_img('mouse-pointer-click')}How to use it", "Numbered steps. Words in <b>bold</b> are labels you will find on the screen."],
        [f"{icon_img('lightbulb')}Good to know", "Rules, limits, permissions and things the system records automatically."],
    ], [40 * mm, TEXT_W - 40 * mm]))
    story.append(callout("Names, phone numbers and amounts in the pictures are sample data from the training copy of the system. They are not real families or staff.", "note"))
    story.append(callout("The picture of a page may show more or fewer rows than yours. What you can see depends on your role. Every page in this guide says which permission it needs.", "tip"))


def toc(story):
    story.append(PageBreak())
    story.append(SetPart("Contents"))
    story.append(heading("H1", "Contents", "list"))
    t = TableOfContents()
    t.levelStyles = [S["toc0"], S["toc1"], S["toc2"]]
    t.dotsMinLevel = 1
    story.append(t)


def part_page(story, title: str, subtitle: str, icon: str, color: str):
    story.append(PageBreak())
    story.append(SetPart(title))

    class Band(Flowable):
        def __init__(self):
            super().__init__()
            self.width, self.height = TEXT_W, 58 * mm

        def draw(self):
            c = self.canv
            c.setFillColor(colors.HexColor(color))
            c.roundRect(0, 0, TEXT_W, 58 * mm, 3 * mm, fill=1, stroke=0)
            c.setFillColor(colors.Color(1, 1, 1, alpha=0.22))
            c.circle(22 * mm, 29 * mm, 13 * mm, fill=1, stroke=0)
            p = white_icon(icon)
            if p:
                c.drawImage(str(p), 14 * mm, 21 * mm, 16 * mm, 16 * mm, mask="auto")
            c.setFillColor(WHITE)
            c.setFont("UI-Bold", 26)
            c.drawString(42 * mm, 33 * mm, title)
            c.setFont("UI-Light", 12.5)
            c.drawString(42 * mm, 24 * mm, subtitle)

    band = Band()
    band.spaceAfter = 10
    band._toc_text = title
    band._toc_level = 0
    band._toc_key = "part-" + re.sub(r"[^a-z0-9]+", "-", title.lower())
    story.append(band)


# ----------------------------------------------------------------------------- content: getting started
def getting_started(story, pages):
    part_page(story, "Getting started", "Signing in, the screen, the menu, and the habits every page shares", "log-in" if (ICONS / "log-in.png").exists() else "home", "#1d6fcf")

    story.append(heading("H2", "What the system is", "book-open"))
    story.append(P("The Online Quran College Digital Operating System is one website that runs the whole college. It replaces the old portal and keeps "
                   "the same areas, the same page names and the same layout, so what you learned there still works here. It also adds new tools: "
                   "a CRM for leads and WhatsApp, an Operations area for tasks and KPIs, live class monitoring, and portals for teachers, families and students."))
    story.append(P("Everyone uses the same sign-in page. What you see after signing in depends on your role:"))
    story.append(grid([
        ["Who", "What they see after signing in"],
        ["Office staff and managers", "The Home launchpad with up to eight area cards. A head of department sees the areas their role allows."],
        ["Teachers", "The Teacher portal: today's classes, students, lesson plans, evaluations, and their own attendance, pay and training."],
        ["Parents (clients)", "The Family portal: their children's classes, attendance, progress, invoices and requests."],
        ["Students", "The Student portal: their classes, lessons, progress, results and certificates."],
    ], [45 * mm, TEXT_W - 45 * mm]))

    story.append(heading("H2", "Signing in", "log-in" if (ICONS / "log-in.png").exists() else "circle-user"))
    story.append(shot("login.png", max_h=95 * mm))
    story.append(Paragraph("The sign-in page. Staff, teachers, parents and students all use this page.", S["caption"]))
    story.append(bullets([
        "Open the system address in your browser. Chrome, Edge, Safari and Firefox all work, on a computer, tablet or phone.",
        "Type your <b>Email or username</b> and your <b>Password</b>, then press <b>Sign in</b>.",
        "Tick <b>Keep me signed in</b> on your own device to stay signed in for two weeks. Do not tick it on a shared computer.",
        "If two-factor authentication is switched on for your account, a second screen asks for the 6-digit code from your authenticator app. Type it and press <b>Verify</b>.",
        "A new family who has not registered yet can press <b>New family? Register</b> to fill in the online registration form.",
    ], numbered=True))
    story.append(callout("Five wrong passwords in a row lock the account for a short time. The screen tells you how many minutes to wait. "
                         "If you have forgotten your password, ask a system administrator to reset it; they give you a temporary password and the system asks you to choose a new one at your next sign-in.", "warn"))
    story.append(callout("To sign out, open the menu under your name at the top right and choose <b>Sign out</b>. Always sign out on a shared computer.", "tip"))

    story.append(PageBreak())
    story.append(heading("H2", "The screen, piece by piece", "layout-grid"))
    story.append(P("Every staff page is built the same way. The picture below is the Client List, with the parts numbered."))
    ann = annotated("admin__clients.png", [(32, 24), (589, 24), (1133, 22), (1169, 22), (1300, 24), (200, 63), (120, 470), (326, 115),
                                            (840, 217), (840, 350), (1368, 138), (840, 700), (1404, 63)], "screen_parts.png")
    story.append(shot(ann.name if False else "../crops/screen_parts.png"))
    story.append(Paragraph("The parts of a page, numbered. See the table below.", S["caption"]))
    story.append(grid([
        ["#", "Part", "What it does"],
        ["1", f"{icon_img('menu')}Menu button", "Opens the slide-out menu listing every area and page you may open."],
        ["2", f"{icon_img('search')}Search box", "Type a page name or a record (a client code, a student, an invoice number) and press Enter."],
        ["3", f"{icon_img('bell')}Notifications bell", "Shows how many unread notices you have. Press it to read them."],
        ["4", f"{icon_img('home')}Home button", "Takes you back to the Home launchpad from anywhere."],
        ["5", f"{icon_img('circle-user')}Your name", "Opens your menu: My Profile, and Sign out."],
        ["6", "Breadcrumb", "Shows where you are: Home › Area › Group › Page. Press any part to go back up."],
        ["7", "Area sidebar", "Lists every page of the area you are in, grouped as on the area's home. The page you are on is highlighted."],
        ["8", "Page title", "The name of the page and one line saying what it is for. Buttons for related pages sit on the right."],
        ["9", "Status tiles", "Coloured counters. Press a tile to filter the list to that status. Press it again, or <b>Reset</b>, to clear."],
        ["10", "Filter bar", "Search text, drop-down lists and date ranges. Press <b>Go</b> to apply and <b>Reset</b> to clear."],
        ["11", f"{icon_img('plus')}Create button", "Opens the form to add a new record. Only shown if your role may add."],
        ["12", "The table", "The list itself. Press an ID or name to open the record. Some rows have <b>View</b>, <b>Open</b>, or a pencil to edit in place."],
        ["13", "Collapse arrow", "Hides the area sidebar to give the table more room. Press again to bring it back."],
    ], [8 * mm, 34 * mm, TEXT_W - 42 * mm]))
    story.append(callout("On a phone or a narrow window the area sidebar is hidden, and a strip of tabs above the page lists the same pages instead. Nothing is lost.", "note"))

    story.append(PageBreak())
    story.append(heading("H2", "The Home launchpad", "home"))
    story.append(shot("admin__home.png", max_h=100 * mm))
    story.append(Paragraph("Home: one card per area. Press a card to open the area's own cards.", S["caption"]))
    story.append(P("Home shows one card for each area you may use. Press a card to open the area home. An area home shows its groups as cards "
                   "(for example Human Resource shows Dashboards, Employment Management, Time and Attendance, and so on). Press a group card to see its pages. "
                   "Three presses take you from Home to any page, and the breadcrumb takes you back."))
    story.append(grid([
        ["Card", "What is inside"],
    ] + [[f"{icon_img(sec['icon'])}<b>{esc(sec['label'])}</b>", esc(sec["blurb"])] for sec in nav.ADMIN_NAV], [45 * mm, TEXT_W - 45 * mm]))

    story.append(heading("H2", "The slide-out menu", "menu"))
    story.append(shot("menu_drawer.png", max_h=90 * mm))
    story.append(Paragraph("The menu opens over the page. Press an area to unfold its pages.", S["caption"]))
    story.append(bullets([
        "Press the <b>☰</b> button at the top left. The menu slides in from the left.",
        "Press an area name to unfold its groups and pages, then press a page to open it.",
        "Press <b>Home</b> at the top of the menu to return to the launchpad, or the <b>×</b> to close the menu.",
        "The bottom of the menu shows who is signed in and their role.",
    ]))

    story.append(PageBreak())
    story.append(heading("H2", "Search", "search"))
    story.append(shot("admin__search_q_trial_balance.png", crop=MAIN_CROP, max_h=95 * mm))
    story.append(Paragraph("Search results: pages first, then matching records.", S["caption"]))
    story.append(bullets([
        "Type in the search box at the top of any page and press Enter.",
        "Pages come first. Typing <b>accounts</b> lists the Accounts area and all its pages; typing <b>trial balance</b> puts the Trial Balance Report at the top.",
        "Records come next, grouped by type: clients, students, teachers, employees, invoices, leads and so on. Type a code such as a client ID or an invoice number to jump straight to it.",
        "You only find pages and records your role may open.",
    ]))
    story.append(heading("H2", "Notifications", "bell"))
    story.append(shot("admin__notifications.png", crop=MAIN_CROP, max_h=85 * mm))
    story.append(Paragraph("The notification list. Unread items are highlighted.", S["caption"]))
    story.append(bullets([
        "The red number on the bell is your unread count. Press the bell to open the list.",
        "Each notice says what happened (a request approved, a class missed, a task assigned, a notice from People & Culture) and when.",
        "Press a notice to open the record it is about. Opening it marks it as read.",
    ]))
    story.append(heading("H2", "My Profile", "user-cog"))
    story.append(shot("admin__profile.png", crop=MAIN_CROP, max_h=85 * mm))
    story.append(Paragraph("Your own profile page.", S["caption"]))
    story.append(bullets([
        "Open it from the menu under your name at the top right, or from the Employee Self Portal.",
        "Change your display name, time zone and language, and your password.",
        "Set up two-factor authentication: scan the QR code with an authenticator app and type the code once to switch it on.",
        "See the devices where you are signed in, and sign any of them out.",
    ]))

    story.append(PageBreak())
    story.append(heading("H2", "Habits every page shares", "list-checks"))
    story.append(P("Once you know these patterns you can use any page in the system, even one this guide does not cover in detail."))
    for title, text in [
        ("Status tiles filter the list", "A row of coloured tiles at the top of a list shows how many records are in each status. Press a tile and the table shows only those records. The count is live."),
        ("Filter bar, Go and Reset", "Choose what you want in the filter bar, then press <b>Go</b>. <b>Reset</b> clears every filter. Date filters take a from date and a to date; leave one empty for an open range."),
        ("Open a record", "Press the ID or name in the first column, or the <b>View</b> / <b>Open</b> link at the end of the row. A record page shows a summary at the top and tabs or panels for its parts."),
        ("Create", "The <b>Create</b> button at the top right of a list opens a form. Fields marked with a star * are required. <b>Save</b> stores the record and returns to the list; <b>Cancel</b> throws the form away."),
        ("Edit in place", "Many tables have a pencil at the end of the row. Press it and the row turns into small fields. Press <b>Save</b> on that row to keep the change, or <b>Cancel</b> to undo it."),
        ("Actions → Change Status", "Lists that manage a process (subscriptions, requests, invoices) let you tick several rows, choose a new status and add remarks, then press <b>Apply</b>. Every change is written to the record's log."),
        ("Rationale", "Some actions ask <b>Why?</b> before they go ahead: approving a payroll run, revoking a licence, revealing a masked phone number, changing a role. What you type is stored in the audit log next to the action. Keep it short and honest."),
        ("Masked details", "Phone numbers, WhatsApp numbers, passwords and keys are shown as stars. Press <b>Reveal</b> to see the value for a moment. Every reveal is recorded with your name and the time."),
        ("Print and export", "Reports have <b>Print</b> and <b>CSV</b> buttons. Print opens a clean copy for the printer or a PDF. CSV downloads the table for Excel."),
        ("Paging", "Long lists show 25 rows at a time. Use the page numbers under the table, or a bigger rows setting where the page offers one."),
        ("Badges", "Small coloured labels show a status: green for good (Regular, Paid, Approved), amber for waiting (Pending, Trial), red for trouble (Overdue, Rejected, Black List), grey for finished (Completed, Cancelled)."),
        ("Nothing here yet", "An empty table says so plainly. It usually means the filters are too narrow, or that nothing has been created yet for this branch or month."),
    ]:
        story.append(Paragraph(f"<b>{esc(title)}.</b> {md(text)}", S["body"]))

    story.append(PageBreak())
    story.append(heading("H2", "Icons you will see", "layout-grid"))
    story.append(P("Every page and button carries a small icon. These are the ones you will meet most often."))
    legend = [
        ("menu", "Menu", "Opens the slide-out menu"), ("search", "Search", "Find a page or a record"), ("bell", "Bell", "Notifications"),
        ("home", "Home", "Back to the launchpad"), ("circle-user", "User", "Your profile and sign out"), ("plus", "Plus", "Create a new record"),
        ("pencil", "Pencil", "Edit this row or record"), ("trash-2", "Bin", "Delete (asks you to confirm)"), ("eye", "Eye", "View or reveal"),
        ("save", "Save", "Keep the change"), ("x", "Cross", "Cancel or close"), ("check", "Tick", "Approve or confirm"),
        ("printer", "Printer", "Print or save as PDF"), ("download", "Download", "Download a file or CSV"), ("upload", "Upload", "Attach or import a file"),
        ("filter", "Filter", "Narrow the list"), ("refresh-cw", "Refresh", "Reload or sync"), ("arrow-left", "Back", "Return to the previous page"),
        ("lock", "Lock", "Confidential, or closed to changes"), ("shield-check", "Shield", "Security, roles, quality"), ("alert-triangle", "Warning", "Violations, alerts, take care"),
        ("clock", "Clock", "Time, attendance, shifts"), ("calendar-days", "Calendar", "Schedules and dates"), ("receipt", "Receipt", "Invoices and receipts"),
        ("banknote", "Banknote", "Pay, payroll, money"), ("users", "People", "Clients, employees, teams"), ("graduation-cap", "Cap", "Students and learning"),
        ("gauge", "Gauge", "Dashboards"), ("settings", "Cog", "Configuration"), ("external-link", "External link", "Opens in a new tab"),
    ]
    rows = [["Icon", "Name", "Meaning", "Icon", "Name", "Meaning"]]
    half = (len(legend) + 1) // 2
    for i in range(half):
        a = legend[i]
        b = legend[i + half] if i + half < len(legend) else ("", "", "")
        rows.append([Paragraph(icon_img(a[0], 13), S["cell"]) if a[0] else "", a[1], a[2],
                     Paragraph(icon_img(b[0], 13), S["cell"]) if b[0] else "", b[1], b[2]])
    story.append(grid(rows, [12 * mm, 24 * mm, TEXT_W / 2 - 36 * mm, 12 * mm, 24 * mm, TEXT_W / 2 - 36 * mm]))


# ----------------------------------------------------------------------------- content: page sections
def facts_block(rec: dict) -> list:
    """The 'On this page' block, built from what the capture saw."""
    rows = []
    stats = [s["label"] for s in rec.get("stats", []) if s.get("label")]
    if stats:
        rows.append(["Tiles", " · ".join(dict.fromkeys(stats))])
    tabs = [t for t in rec.get("tabs", []) if t and not t.startswith("Home")]
    if tabs and len(tabs) > 1:
        rows.append(["Tabs", " · ".join(dict.fromkeys(tabs))])
    raw = rec.get("fields", [])
    labels = [f for f in raw if f != f.lower()]  # real labels carry capitals; the rest are field-name fallbacks
    norm = {re.sub(r"[^a-z0-9]", "", l.lower()) for l in labels}
    fields: list[str] = []
    for f in raw:
        key = re.sub(r"[^a-z0-9]", "", f.lower())
        if f == f.lower() and (key in norm or any(key in n or n in key for n in norm if len(key) > 3)):
            continue
        if len(key) < 2 or f.replace(" *", "*") in fields:
            continue
        fields.append(f.replace(" *", "*"))
    if fields:
        rows.append(["Filters and fields", " · ".join(fields[:18])])
    for t in rec.get("tables", [])[:3]:
        cols = t["columns"]
        if cols:
            rows.append(["Columns" if len(rec.get("tables", [])) == 1 else "Table columns", " · ".join(cols[:22])])
    buttons = []
    for b in rec.get("buttons", []):
        if re.match(r"^\d", b) or not re.search(r"[A-Za-z]", b) or b in ("Go", "Reset"):
            continue
        if b not in buttons:
            buttons.append(b)
    acts = [a for a in rec.get("rowActions", []) if not re.search(r"\d", a) and len(a) > 1]
    if buttons or acts:
        rows.append(["Buttons and actions", " · ".join(buttons[:14] + [f"(row) {a}" for a in dict.fromkeys(acts)][:8])])
    if not rows:
        return []
    return [grid([[f"<b>{a}</b>", b] for a, b in rows], [32 * mm, TEXT_W - 32 * mm], header=False, zebra=False, font="cell")]


def page_section(story, rec: dict, desc: dict, where: str, level="H3"):
    label = rec.get("label") or rec.get("title") or rec["url"]
    story.append(heading(level, label, rec.get("icon"), key=f"{rec['portal']}-{re.sub(r'[^a-z0-9]+', '-', rec['url'].lower())}"))
    meta = where
    if rec.get("perm"):
        meta += f"   ·   needs permission <b>{esc(rec['perm'])}</b>"
    if rec.get("title") and rec["title"] != label:
        meta += f"   ·   on screen: <b>{esc(rec['title'])}</b>"
    story.append(Paragraph(meta, S["small"]))
    body = [shot(rec["shot"], crop=MAIN_CROP if rec.get("kind") in ("page", "extra") else None, max_h=118 * mm)]
    if rec.get("subtitle"):
        body.append(Paragraph(esc(rec["subtitle"]), S["caption"]))
    else:
        body.append(Spacer(1, 4))
    story.append(KeepTogether([story.pop(), story.pop()][::-1] + body))
    d = desc or {}
    purpose = d.get("purpose") or "This page is part of the area above."
    story.append(Paragraph(f"{icon_img('info', 10)}<b>What it is for.</b> " + md(purpose), S["body"]))
    fb = facts_block(rec)
    if fb:
        story.append(Paragraph(f"{icon_img('eye', 10)}<b>On this page</b>", S["H4"]))
        story.extend(fb)
    steps = d.get("steps") or []
    if steps:
        story.append(Paragraph(f"{icon_img('mouse-pointer-click', 10)}<b>How to use it</b>", S["H4"]))
        story.append(bullets(steps, numbered=True))
    notes = d.get("notes") or []
    if notes:
        story.append(callout("<br/>".join(md(n) for n in notes).replace("<b>", "<b>"), "note") if len(notes) == 1 else _notes(notes))
    story.append(Spacer(1, 4))


def _notes(notes: list[str]) -> Table:
    body = [Paragraph(f"{icon_img('lightbulb', 10)}<b>Good to know</b>", S["cellb"])] + [Paragraph("• " + md(n), S["cell"]) for n in notes]
    t = Table([[body]], colWidths=[TEXT_W])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), NOTE_BG), ("LINEBEFORE", (0, 0), (0, -1), 2.5, NOTE_LINE),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                           ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    t.spaceAfter = 6
    return t


def area_part(story, pages: dict, desc: dict, portal: str, section: dict, color: str, part_no: int):
    key = f"{portal}:/home/{section['slug']}"
    home = pages.get(key, {})
    part_page(story, section["label"], section["blurb"], section["icon"], color)
    d = desc.get(key, {})
    story.append(P(d.get("purpose") or f"The {section['label']} area.", "lead"))
    story.append(shot(home.get("shot", ""), max_h=110 * mm))
    story.append(Paragraph(f"The {section['label']} home.", S["caption"]))
    if d.get("steps"):
        story.append(bullets(d["steps"]))
    if section.get("groups"):
        rows = [["Group", "Pages"]]
        for g in section["groups"]:
            rows.append([f"{icon_img(g['icon'])}<b>{esc(g['label'])}</b>", " · ".join(esc(i["label"]) for i in g["items"])])
        story.append(grid(rows, [45 * mm, TEXT_W - 45 * mm]))
        if d.get("notes"):
            story.append(_notes(d["notes"]))
        for g in section["groups"]:
            gkey = f"{portal}:/home/{section['slug']}/{g['slug']}"
            grec = pages.get(gkey, {})
            gd = desc.get(gkey, {})
            story.append(PageBreak())
            story.append(heading("H2", g["label"], g["icon"], key=f"{portal}-{section['slug']}-{g['slug']}"))
            story.append(Paragraph(f"{esc(section['label'])} › {esc(g['label'])}", S["small"]))
            story.append(P(gd.get("purpose") or g["blurb"]))
            story.append(shot(grec.get("shot", ""), crop=MAIN_CROP, max_h=70 * mm))
            story.append(Paragraph(f"The {g['label']} cards.", S["caption"]))
            story.append(grid([["Page", "What it is for"]] + [
                [f"{icon_img(i['icon'])}<b>{esc(i['label'])}</b>", md((desc.get(f'{portal}:{i["url"]}', {}).get("purpose") or "").split(". ")[0].rstrip(".") + ".")]
                for i in g["items"]], [50 * mm, TEXT_W - 50 * mm]))
            if gd.get("notes"):
                story.append(_notes(gd["notes"]))
            for i in g["items"]:
                pkey = f"{portal}:{i['url']}"
                rec = pages.get(pkey)
                if not rec:
                    continue
                story.append(PageBreak())
                page_section(story, rec, desc.get(pkey), f"{esc(section['label'])} › {esc(g['label'])}")
    else:
        rows = [["Page", "What it is for"]]
        for i in section["items"]:
            rows.append([f"{icon_img(i['icon'])}<b>{esc(i['label'])}</b>", md((desc.get(f'{portal}:{i["url"]}', {}).get("purpose") or "").split(". ")[0].rstrip(".") + ".")])
        story.append(grid(rows, [50 * mm, TEXT_W - 50 * mm]))
        if d.get("notes"):
            story.append(_notes(d["notes"]))
        for i in section["items"]:
            pkey = f"{portal}:{i['url']}"
            rec = pages.get(pkey)
            if not rec:
                continue
            story.append(PageBreak())
            page_section(story, rec, desc.get(pkey), esc(section["label"]), level="H2")


# ----------------------------------------------------------------------------- appendices
def appendices(story, pages, desc):
    part_page(story, "Appendices", "Status words, roles, differences from the old portal, and getting help", "book", "#5b62b1")

    story.append(heading("H2", "A. Status words", "badge-check"))
    story.append(P("The system uses the same status words as the old portal. This table lists every status you may see, by the kind of record."))
    kinds = {"client": "Client", "student": "Student", "subscription": "Subscription", "invoice": "Invoice", "receipt": "Receipt",
             "class": "Class", "qa": "QA review", "request": "Request", "registration": "Online registration", "task": "Task"}
    meanings = {
        "client": {"Trial": "The family's free trial classes are still running.", "Regular": "A paying family with active subscriptions.",
                   "Drop Out": "The family has left.", "Black List": "The family may not re-register without approval.",
                   "On Leave": "Classes are paused for a while.", "Pass Out": "The family's students completed their courses."},
        "student": {"Trial": "Taking trial classes.", "Regular": "Enrolled and attending.", "On Leave": "Classes paused.",
                    "Drop Out": "Left the college.", "Pass Out": "Completed the course.", "Black List": "Blocked from re-enrolment.", "Free": "Has no class today."},
        "subscription": {"Pending Approval": "Created, waiting for a manager to approve.", "Trial": "Trial period.", "Regular": "Active and billed.",
                         "Freeze": "Paused; not billed.", "Cancelled": "Ended early.", "Completed": "Ran to its end date."},
        "invoice": {"Draft": "Not yet sent.", "Pending": "Sent, not yet paid.", "Confirmed": "Checked by billing.", "Partially Paid": "Some money received.",
                    "Paid": "Fully paid.", "Overdue": "Past its due date and unpaid.", "Cancelled": "Void; does not count."},
        "receipt": {"Pending": "Recorded, not yet confirmed.", "Confirmed": "Money received and applied.", "Failed": "The payment did not go through.",
                    "Refunded": "Returned to the family.", "Cancelled": "Void."},
        "class": {"Pending": "Not started yet.", "Teacher is Available": "The teacher is in the room.", "Started": "In progress.", "Done": "Held.",
                  "Missed": "Nobody started it.", "Student Absent": "The teacher came, the student did not.", "Student On-Leave": "The student was on approved leave.",
                  "Cancelled": "Called off.", "Rescheduled": "Moved to another time.", "Free": "No class scheduled."},
        "qa": {"Pending": "Waiting for review.", "In-Progress": "A reviewer has it open.", "Completed": "Reviewed.", "Approved": "Reviewed and accepted.",
               "Flagged": "Needs attention.", "Rejected": "Not accepted."},
        "request": {"Pending": "Waiting for a decision.", "Approved": "Granted.", "Rejected": "Declined.", "Cancelled": "Withdrawn."},
        "registration": {"Pending": "New, not yet contacted.", "Forward to Verifier": "Passed to a verifier to check.", "Trial Scheduled": "A trial class is booked.",
                         "Trial Done": "The trial was held.", "Negotiation": "Talking about the package.", "Converted": "Became a client.", "Lost": "Did not join."},
        "task": {"Draft": "Not yet assigned.", "Pending": "To do.", "In Progress": "Being worked on.", "In Review": "Done, waiting to be checked.",
                 "Completed": "Finished.", "Cancelled": "Dropped.", "Blocked": "Waiting on something else."},
    }
    for k, title in kinds.items():
        labels = list(dict.fromkeys(STATUS_LABELS[k].values()))
        rows = [["Status", "Meaning"]] + [[f"<b>{esc(l)}</b>", meanings.get(k, {}).get(l, "")] for l in labels]
        story.append(Paragraph(esc(title), S["H3"]))
        story.append(grid(rows, [40 * mm, TEXT_W - 40 * mm]))

    story.append(PageBreak())
    story.append(heading("H2", "B. Roles and what they see", "shield-check"))
    story.append(P("Your role decides which areas and pages you may open, and what you may change. A system administrator assigns roles on the Users page in Configuration."))
    roles = [
        ("Super Admin / CEO", "Everything, including the CEO Command Center and confidential grievances."),
        ("System Administrator", "Everything technical: users, roles, settings, integrations, security, backups, audit log."),
        ("Manager", "All operational areas; approves requests, subscriptions and payroll."),
        ("HOD — People & Culture", "Human Resource in full: employees, attendance, leaves, recruitment, payroll."),
        ("HOD — Finance", "Billing Management and Accounts: invoices, receipts, ledgers, vouchers, statements."),
        ("HOD — Academics", "Online Academics: clients, students, subscriptions, classes, evaluations, configuration."),
        ("HOD — QA", "Quality Management: call reviews, feedback, teacher QA performance, monitoring."),
        ("HOD — Marketing", "CRM & Growth: leads, campaigns, WhatsApp inbox, marketing analytics."),
        ("HOD — Technology", "Configuration and integrations."),
        ("Supervisor", "Live monitoring, class schedules, arrangements and queries."),
        ("Academic Coordinator", "Client and student records, subscriptions, class schedules, requests."),
        ("Billing Representative", "Client lists, invoices, receipts, ledgers for the families assigned to them."),
        ("Accountant", "Accounts: vouchers, journal, reports."),
        ("QA Officer", "The QA review queue, call recordings and feedback."),
        ("HR Officer", "Employee records, attendance and leaves."),
        ("Lead Generator / Lead Closer", "Leads, verification and online registrations."),
        ("Teacher", "The Teacher portal only, plus their own Employee Self Portal pages."),
        ("Client / Parent", "The Family portal only."),
        ("Student", "The Student portal only."),
        ("External Auditor", "Read-only: audit log, security centre, reports and statements."),
    ]
    story.append(grid([["Role", "What it can open"]] + [[f"<b>{esc(a)}</b>", b] for a, b in roles], [50 * mm, TEXT_W - 50 * mm]))
    story.append(callout("Every page in this guide names the permission it needs. If you open a page and see <b>You do not have access</b>, ask an administrator to add that permission to your role, or to grant it to you alone.", "tip"))
    story.append(callout("Everyone with a staff role also has their own Employee Self Portal page, because every staff account is linked to an employee record.", "note"))

    story.append(PageBreak())
    story.append(heading("H2", "C. What is different from the old portal", "arrow-left-right"))
    story.append(P("The system was built to match the old portal page for page. A few things were changed on purpose. Here is the full list, with the reason."))
    diffs = [
        ("Online Academics", "Client list tile spelled \"Trail\"", "Spelled \"Trial\"", "The old label was a typo. Both spellings work in filters."),
        ("Online Academics", "Family passwords visible in the Credentials grid", "Masked; revealed on request and the reveal is logged", "Handling of family credentials has to be accountable."),
        ("Online Academics", "Saved reports per user", "Fixed named reports (Primary, Employee Wise Summary, Free Students List, Coming Follow Ups)", "The reports people actually use, without a report designer."),
        ("Online Academics", "Gateway receipts arrive from a live card feed", "A sync action that simulates the pull", "No payment credentials are configured yet; the flow is real."),
        ("Billing Management", "Nothing loads until a filter is applied", "The page opens on every family", "Fast enough to show the whole list; an empty screen hides the number the page exists to show."),
        ("Billing Management", "State, City and Zip Code on a lead", "Country and city only", "The lead record holds no state or postcode; the columns show a dash."),
        ("Human Resource", "Complaints carry a Secret flag in the main list", "A separate confidential channel (Grievances), plus a Secret tab with its own permission", "A flag in a shared list is read by whoever opens the list."),
        ("Human Resource", "Whole-staff attendance and leave balances open to anyone", "Those pages need the People & Culture permission", "A teacher needs their own record, not everyone's."),
        ("Human Resource", "Shift Type and Shift Code stored on the shift", "Derived from the shift group", "The pages say where the value comes from."),
        ("Employee Self Portal", "Fourteen cards, each its own page", "One page with tabs, plus linked pages, and the same fourteen entries in the sidebar", "Fewer clicks for what one person checks about themselves."),
        ("Employee Self Portal", "No confidential channel", "Raise a Grievance, separate from Complaints", "A complaint read by your own management chain is not a safe way to raise one about it."),
        ("Accounts", "Three separate voucher ledgers", "One journal carrying a voucher type", "The trial balance and the statements cannot disagree with the vouchers."),
        ("Accounts", "Cancelling edits the original voucher", "Cancelling posts a mirror reversal", "The original stays readable and the audit trail is complete."),
        ("Configuration", "Secrets shown in the settings list", "Masked, with an audited reveal", "A settings page is read by more people than the secret is meant for."),
        ("Configuration", "Currency rate feed is live", "Simulated, and says so on screen", "There is no contract with a rate provider yet."),
        ("Configuration", "The desktop recording agent is the vendor's program", "The licences, devices and captures are held; the agent program itself is not reproduced", "The agent binary is not ours to reproduce."),
        ("Everywhere", "Group pages open empty unless the area home was opened first", "Every page opens on its own", "There is no session state to lose."),
        ("Everywhere", "Statuses stored as numbers in links", "Readable words in links", "Links stay meaningful and can be shared."),
    ]
    story.append(grid([["Area", "Old portal", "This system", "Why"]] + [[f"<b>{esc(a)}</b>", b, c, d] for a, b, c, d in diffs],
                      [26 * mm, 42 * mm, 50 * mm, TEXT_W - 118 * mm]))

    story.append(PageBreak())
    story.append(heading("H2", "D. When something goes wrong", "life-buoy"))
    story.append(grid([
        ["What you see", "What it means", "What to do"],
        ["\"Invalid username or password\"", "The details do not match.", "Check caps lock. After five failures the account locks for a few minutes. Ask an administrator for a reset if needed."],
        ["\"Account locked. Try again in N minutes\"", "Too many wrong attempts.", "Wait the minutes shown, then try again."],
        ["\"This account is disabled\"", "An administrator switched the account off.", "Contact People & Culture or the system administrator."],
        ["\"You do not have access\"", "Your role lacks the page's permission.", "Ask an administrator to add the permission named at the top of the page's section in this guide."],
        ["\"No employee record\" on the Self Portal", "Your account is not linked to an employee record.", "Ask People & Culture to link your account on the Employee Record page."],
        ["A list is empty", "The filters are too narrow, or nothing exists yet.", "Press <b>Reset</b>. Check the date range and the branch."],
        ["A tile shows a number but the list is empty", "A second filter is still applied.", "Press <b>Reset</b>, then press the tile again."],
        ["\"Rationale is required\"", "The action must be explained.", "Type a short reason and press the button again."],
        ["\"Debits must equal credits\"", "A voucher is unbalanced.", "Check the lines; the total of Debit must match the total of Credit."],
        ["A page looks different on a phone", "The area sidebar is hidden on narrow screens.", "Use the tab strip above the page, or turn the phone sideways."],
        ["The bell shows 99+", "Many unread notices.", "Open the bell; reading a notice clears it."],
    ], [42 * mm, 46 * mm, TEXT_W - 88 * mm]))
    story.append(heading("H3", "Getting help"))
    story.append(bullets([
        "Ask your head of department first; most questions are about a process, not the system.",
        "For sign-in, roles and permissions, ask the system administrator (Configuration › Users).",
        "For a fault in the system, open <b>Configuration › Support Ticket</b> and describe what you did, what you expected, and what happened. Add a screenshot.",
        "The footer of every page shows the release number. Quote it when you report a problem.",
    ]))


# ----------------------------------------------------------------------------- assemble
def main() -> None:
    pages = json.loads((HERE / "pages.json").read_text(encoding="utf-8"))
    desc: dict = {}
    for f in sorted(TEXT_DIR.glob("desc_*.json")):
        desc.update(json.loads(f.read_text(encoding="utf-8")))
    print(f"{len(pages)} pages, {len(desc)} descriptions")

    story: list = [Cover()]
    front_matter(story)
    toc(story)
    getting_started(story, pages)

    for i, sec in enumerate(nav.ADMIN_NAV):
        area_part(story, pages, desc, "admin", sec, nav.palette(i), i + 2)

    portals = [("teacher", nav.TEACHER_NAV, "Teacher portal", "What a teacher sees after signing in", "user-check", "#27b7a4"),
               ("client", nav.CLIENT_NAV, "Family portal", "What a parent sees after signing in", "users", "#ee8a2b"),
               ("student", nav.STUDENT_NAV, "Student portal", "What a student sees after signing in", "graduation-cap", "#8c4ca9")]
    for portal, source, title, sub, icon, color in portals:
        part_page(story, title, sub, icon, color)
        home = pages.get(f"{portal}:/home", {})
        story.append(shot(home.get("shot", ""), max_h=90 * mm))
        story.append(Paragraph(f"The {title} home.", S["caption"]))
        story.append(P(desc.get(f"{portal}:/home", {}).get("purpose") or f"The {title}.", "lead"))
        for sec in source:
            skey = f"{portal}:/home/{sec['slug']}"
            sd = desc.get(skey, {})
            story.append(PageBreak())
            story.append(heading("H2", sec["label"], sec["icon"], key=f"{portal}-{sec['slug']}"))
            story.append(P(sd.get("purpose") or sec["blurb"]))
            story.append(shot(pages.get(skey, {}).get("shot", ""), crop=MAIN_CROP, max_h=70 * mm))
            story.append(grid([["Page", "What it is for"]] + [
                [f"{icon_img(i['icon'])}<b>{esc(i['label'])}</b>", md((desc.get(f'{portal}:{i["url"]}', {}).get("purpose") or "").split(". ")[0].rstrip(".") + ".")]
                for i in sec["items"]], [50 * mm, TEXT_W - 50 * mm]))
            for i in sec["items"]:
                pkey = f"{portal}:{i['url']}"
                rec = pages.get(pkey)
                if not rec:
                    continue
                story.append(PageBreak())
                page_section(story, rec, desc.get(pkey), f"{esc(title)} › {esc(sec['label'])}")

    # record pages
    for key, title in (("admin:client_detail", "A client record"), ("admin:employee_detail", "An employee record")):
        if key in pages:
            story.append(PageBreak())
            rec = dict(pages[key], label=title, icon="file-text", kind="extra")
            page_section(story, rec, desc.get(key), "Opened from a list by pressing the ID or name")

    appendices(story, pages, desc)

    doc = Guide(str(OUT))
    doc.multiBuild(story)
    print("wrote", OUT, OUT.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
