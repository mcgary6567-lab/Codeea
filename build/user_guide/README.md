# User guide builder

Builds `OQC_User_Guide.pdf`, the complete illustrated user guide: every launchpad page of every portal,
with a screenshot, what the page is for, what is on it, how to use it and what to know.

Three inputs:

- `capture.py` signs in to a running dev copy (http://localhost:8000, seeded with `seed.py --reset`) as the
  CEO, a teacher, a parent and a student, walks every entry in `app/core/nav.py`, and writes one screenshot
  per page plus `pages.json` (title, tiles, columns, tabs, fields, buttons) and a PNG of every Lucide icon
  the navigation uses. It drives the installed Chrome or Edge through Playwright (`pip install playwright`;
  no browser download is needed).
- `desc_*.json` hold the written text per page, keyed `portal:url` exactly as in `pages.json`
  (`purpose`, `steps`, `notes`). When a page is added to `nav.py`, add its entry here; the build falls back
  to a one-line placeholder for a page with no entry.
- `build_guide.py` lays the book out with reportlab (Segoe UI from `C:\Windows\Fonts`) and writes the PDF
  to `build/user_guide/out/`.

```bash
.venv/Scripts/python.exe build/user_guide/capture.py      # needs the app running on port 8000
.venv/Scripts/python.exe build/user_guide/build_guide.py  # writes out/OQC_User_Guide.pdf
```

`out/` is ignored by git; the PDF is about 27 MB and is distributed, not versioned.
