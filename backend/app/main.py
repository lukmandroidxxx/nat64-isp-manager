from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.deps import get_client_ip
from app.core.errors import AppError
from app.core.logging import RequestContextMiddleware, configure_logging
from app.core.rate_limit import limiter
from app.models import Base
from app.services import auth as auth_service
from app.services import collector
from app.services.host import host

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Development/test convenience. Production deployments must run Alembic.
    if settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        info = auth_service.bootstrap(db)
        db.commit()
        app.state.bootstrap = info
        if info.get("admin_created"):
            log.warning("Bootstrap administrator %r created", info.get("admin_username"))
    except Exception:
        db.rollback()
        log.exception("Bootstrap failed")
        raise
    finally:
        db.close()

    task: asyncio.Task | None = None
    if settings.collector_enabled:
        task = asyncio.create_task(collector.run_forever(), name="nat64-collector")
    app.state.collector_task = task
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Control plane for a Jool 4 NAT64 gateway. Host changes are executed only through "
        "approved change requests and a root-owned typed helper; no shell is exposed to clients."
    ),
    openapi_url=f"{settings.api_prefix}/openapi.json" if settings.enable_docs else None,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        if settings.secure_cookies or settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            settings.rate_limit_enabled
            and path.startswith(settings.api_prefix)
            and not path.endswith("/health")
        ):
            key = f"{get_client_ip(request)}:{path}"
            allowed, _remaining, retry = limiter.check(
                key, settings.rate_limit_per_minute, 60
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": {
                            "code": "rate_limited",
                            "message": "Too many requests",
                            "detail": {"retry_after": retry},
                        }
                    },
                    headers={"Retry-After": str(retry)},
                )
        return await call_next(request)


app.add_middleware(RequestContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-HA-Sync-Token"],
    expose_headers=["X-Request-ID", "X-Process-Time-Ms", "Content-Disposition"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_failed",
                "message": "Request validation failed",
                "detail": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        content = {"detail": exc.detail}
    else:
        content = {
            "detail": {
                "code": "http_error",
                "message": str(exc.detail),
                "detail": None,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": "An unexpected error occurred. Check the server log using the request ID.",
                "detail": {"request_id": request.headers.get("x-request-id")},
            }
        },
    )


@app.get("/health", tags=["system"])
def health() -> dict:
    """Unauthenticated liveness/readiness endpoint used by HA peers and probes."""
    database = {"dialect": None, "ok": False, "error": None}
    config_version = None
    try:
        db = SessionLocal()
        try:
            database["dialect"] = db.get_bind().dialect.name
            from sqlalchemy import func, select

            from app.models import (
                Dns64Settings,
                FirewallSettings,
                JoolInstance,
                NetworkSettings,
            )

            def version(model):
                record = db.get(model, 1)
                return getattr(record, "config_version", 0) if record else 0

            config_version = sum(
                [
                    version(Dns64Settings),
                    version(FirewallSettings),
                    version(NetworkSettings),
                    db.scalar(select(func.count(JoolInstance.id))) or 0,
                ]
            )
            database["ok"] = True
        finally:
            db.close()
    except Exception as exc:
        database["error"] = f"{type(exc).__name__}: {exc}"

    jool_state = {
        "status": "unsupported" if not host.available else "configured",
        "version": None,
        "message": host.unavailable_reason(),
    }
    try:
        from app.services.jool import jool

        info = jool.status()
        jool_state["status"] = info.status.value
        jool_state["version"] = info.version
        jool_state["message"] = info.message
    except Exception as exc:
        jool_state["message"] = f"{type(exc).__name__}: {exc}"

    return {
        "status": "ok" if database["ok"] else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "config_version": config_version,
        "database": database,
        "host": {
            "mode": host.mode,
            "available": host.available,
            "reason": host.unavailable_reason(),
        },
        "services": {
            "jool": jool_state,
            "collector": collector.state.as_dict(),
        },
        "demo_mode": settings.demo_mode,
    }


app.include_router(api_router, prefix=settings.api_prefix)


_frontend = Path(settings.frontend_dist)
_frontend_enabled = settings.serve_frontend and (_frontend / "index.html").is_file()

if _frontend_enabled:
    _assets = _frontend / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def root() -> FileResponse:
        return FileResponse(_frontend / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        # Serve real files (favicon, robots.txt, ...) when they exist, otherwise
        # return the SPA shell so client-side routes work on a hard refresh.
        if full_path.startswith(("api/", "health")) or full_path in {"docs", "redoc", "openapi.json"}:
            raise StarletteHTTPException(status_code=404)
        resolved = (_frontend / full_path).resolve()
        if full_path and resolved.is_file() and resolved.is_relative_to(_frontend.resolve()):
            return FileResponse(resolved)
        return FileResponse(_frontend / "index.html")

    log.info("Serving frontend from %s", _frontend)
else:

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "api": settings.api_prefix,
            "health": "/health",
            "documentation": "/docs" if settings.enable_docs else None,
        }

    log.info("Frontend serving disabled or dist missing: %s", _frontend)
