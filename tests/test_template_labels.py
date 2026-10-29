"""Every form field a template writes by hand has a name a screen reader can announce (WCAG 1.3.1 / 4.1.2).

The ui macros label their fields already; this catches the raw <input>/<select>/<textarea> written outside them.
A field passes when it has aria-label / aria-labelledby / title, sits inside an open <label>, or has an id that a
label's for= points at. The browser scan in e2e/test_ux_a11y.py checks the same thing on rendered pages.
"""
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"
TAG = re.compile(r"<(input|select|textarea)\b((?:[^>{]|\{\{.*?\}\}|\{%.*?%\})*)>", re.S)
UNNAMED_TYPES = ("hidden", "submit", "button", "reset")


def unlabelled_fields():
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for m in TAG.finditer(text):
            attrs = m.group(2)
            kind = re.search(r'\btype="([^"]+)"', attrs)
            if kind and kind.group(1) in UNNAMED_TYPES:
                continue
            if re.search(r"aria-label|aria-labelledby|\btitle=", attrs):
                continue
            before = text[max(0, m.start() - 400):m.start()]
            if before.rfind("<label") > before.rfind("</label>"):
                continue  # wrapped by its label
            ident = re.search(r'\bid="([^"]+)"', attrs)
            if ident and f'for="{ident.group(1)}"' in text:
                continue
            found.append(f"{path.relative_to(TEMPLATES)}:{text.count(chr(10), 0, m.start()) + 1} <{m.group(1)}{attrs[:60]}")
    return found


def test_every_hand_written_field_is_labelled():
    missing = unlabelled_fields()
    assert not missing, "fields with no accessible name (add aria-label, or label for=/id):\n" + "\n".join(missing)


def test_the_check_catches_an_unlabelled_field(tmp_path, monkeypatch):
    (tmp_path / "x.html").write_text('<label>Name</label><input name="a"><label>B <input name="b"></label>'
                                     '<label for="c">C</label><input id="c" name="c"><input type="hidden" name="d">',
                                     encoding="utf-8")
    monkeypatch.setitem(globals(), "TEMPLATES", tmp_path)
    missing = unlabelled_fields()
    assert len(missing) == 1 and missing[0].startswith('x.html:1 <input name="a"')
