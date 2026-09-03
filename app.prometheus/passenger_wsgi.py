"""cPanel / Passenger entry point.

cPanel's "Setup Python App" runs a WSGI app via Passenger, but Prometheus is an
ASGI (FastAPI) app, so we wrap it with a2wsgi. Passenger looks for a module-level
`application` object in this file.

Requires `a2wsgi` (in requirements.txt). NOTE: Passenger does NOT proxy
WebSockets — that's fine, the dashboard automatically falls back to 3s polling.
For full real-time WebSocket updates, deploy with uvicorn on a VPS instead
(see DEPLOY.md).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware  # noqa: E402
from server.main import app as _asgi_app  # noqa: E402

application = ASGIMiddleware(_asgi_app)
