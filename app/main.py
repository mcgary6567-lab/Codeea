"""Online Quran College — Digital Operating System. FastAPI application factory."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings, BASE_DIR
from app.core.deps import NotAuthenticated, PermissionDenied
from app.core.templating import render
from app.database import SessionLocal, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("oqc")


class DBSessionMiddleware(BaseHTTPMiddleware):
    """One SQLAlchemy session per request, available as request.state.db."""

    async def dispatch(self, request: Request, call_next):
        request.state.db = SessionLocal()
        request.state.user = None
        try:
            response = await call_next(request)
        finally:
            request.state.db.close()
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
        return response


def _discover_routers(app: FastAPI, package: str, prefix: str = "") -> None:
    """Import every module in ``package`` exposing ``router`` and mount it."""
    pkg = importlib.import_module(package)
    for mod_info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_"):
            continue
        name = f"{package}.{mod_info.name}"
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # keep the app bootable while a module is broken
            log.exception("Failed to import %s: %s", name, exc)
            continue
        router = getattr(mod, "router", None)
        if router is not None:
            app.include_router(router, prefix=prefix)
            log.debug("Mounted %s", name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.core.scheduler import start_scheduler, stop_scheduler
    start_scheduler()
    log.info("%s ready at %s", settings.APP_NAME, settings.BASE_URL)
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, version="1.1.0", docs_url="/api/docs", redoc_url="/api/redoc",
                  openapi_url="/api/openapi.json", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(DBSessionMiddleware)
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    (BASE_DIR / "storage").mkdir(exist_ok=True)
    app.mount("/storage", StaticFiles(directory=str(BASE_DIR / "storage")), name="storage")

    _discover_routers(app, "app.web")
    _discover_routers(app, "app.api", prefix="/api/v1")

    # ------------------------------------------------------------------ error handling
    def wants_json(request: Request) -> bool:
        return request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", "")

    @app.exception_handler(NotAuthenticated)
    async def _not_auth(request: Request, exc: NotAuthenticated):
        if wants_json(request):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse(url=f"/login?next={request.url.path}", status_code=303)

    @app.exception_handler(PermissionDenied)
    async def _forbidden(request: Request, exc: PermissionDenied):
        if wants_json(request):
            return JSONResponse({"detail": f"Permission denied: {exc.perm}"}, status_code=403)
        return render(request, "error.html", {"code": 403, "title": "Access denied",
                      "message": f"Your role does not include the permission required for this page ({exc.perm})."}, status_code=403)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        if wants_json(request):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        titles = {404: "Page not found", 403: "Forbidden", 400: "Bad request", 405: "Method not allowed"}
        return render(request, "error.html", {"code": exc.status_code, "title": titles.get(exc.status_code, "Error"),
                      "message": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _server_error(request: Request, exc: Exception):
        log.exception("Unhandled error on %s", request.url.path)
        if wants_json(request):
            return JSONResponse({"detail": "Internal server error"}, status_code=500)
        return render(request, "error.html", {"code": 500, "title": "Something went wrong",
                      "message": "The error has been logged. Please try again or contact the system administrator."}, status_code=500)

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV, "version": "1.1.0"}

    return app


app = create_app()
