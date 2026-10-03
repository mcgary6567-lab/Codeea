"""Static asset links carry a fingerprint, so a deploy's stylesheet reaches browsers that cached the old one."""
from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.core.templating import ASSET_VERSION
from app.main import app


def test_stylesheet_and_script_links_are_versioned():
    c = TestClient(app)
    html = c.get("/login").text
    assert re.search(rf'/static/css/tailwind\.css\?v={ASSET_VERSION}"', html)
    assert re.search(rf'/static/js/app\.js\?v={ASSET_VERSION}"', html)
    assert len(ASSET_VERSION) == 10
