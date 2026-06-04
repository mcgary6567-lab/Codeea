"""System-tray helper for hands-off 24/7 running.

Wraps ``pystray`` so the app can hide to the notification area instead of
closing — the bot keeps trading in the background with a tray icon offering
*Show* / *Quit*. Both ``pystray`` and ``Pillow`` are optional: if either is
missing, :func:`available` returns ``False`` and the GUI simply disables the
"minimize to tray" option (the app still runs normally).

pystray's event loop runs on its own thread (``run_detached``); its menu
callbacks must not touch Tk directly, so they marshal back via ``root.after``.
"""

from __future__ import annotations

import os


def available() -> bool:
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - any import/backend failure => unavailable
        return False


class TrayController:
    def __init__(self, root, on_show, on_quit, image_path: str, title: str):
        self.root = root
        self.on_show = on_show
        self.on_quit = on_quit
        self.image_path = image_path
        self.title = title
        self._icon = None

    def _load_image(self):
        from PIL import Image
        if self.image_path and os.path.exists(self.image_path):
            return Image.open(self.image_path)
        # Fallback: a small solid square so there is always a visible icon.
        return Image.new("RGB", (64, 64), (45, 108, 223))

    def start(self) -> bool:
        """Create and run the tray icon (detached). Returns True on success."""
        if self._icon is not None:
            return True
        try:
            import pystray
            menu = pystray.Menu(
                pystray.MenuItem("Show", self._show, default=True),
                pystray.MenuItem("Quit", self._quit),
            )
            self._icon = pystray.Icon(
                "prometheus", self._load_image(), self.title, menu)
            self._icon.run_detached()
            return True
        except Exception:  # noqa: BLE001
            self._icon = None
            return False

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
            self._icon = None

    # -- menu callbacks (run on the tray thread) ----------------------------
    def _show(self, *_):
        self.root.after(0, self.on_show)

    def _quit(self, *_):
        self.stop()
        self.root.after(0, self.on_quit)
