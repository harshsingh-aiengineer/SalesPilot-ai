"""SalesPilot API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.agent_routes import router as agent_router
from app.api.automation import router as automation_router
from app.api.crm import router as crm_router
from app.api.mail import router as mail_router
from app.api.system import router as system_router
from app.bootstrap import init_app
from app.config import get_settings
from app.services.leads import LeadError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_app()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length:
            try:
                size = int(length)
            except ValueError:
                size = 0
            if size > settings.request_body_limit_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "payload_too_large", "message": "Request body is too large."}},
                )
        return await call_next(request)

    @app.exception_handler(LeadError)
    async def lead_error(_request: Request, exc: LeadError):
        return JSONResponse(status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        parts = []
        for err in exc.errors():
            loc = ".".join(str(item) for item in err.get("loc", []) if item != "body")
            parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
        message = "; ".join(parts) or "Request validation failed."
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(
                {"detail": {"code": "validation_error", "message": message, "errors": exc.errors()}},
                custom_encoder={Exception: str},
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return JSONResponse(status_code=exc.status_code, content={"detail": {"code": "http_error", "message": str(exc.detail)}})

    @app.exception_handler(Exception)
    async def unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content={"detail": {"code": "internal_error", "message": "An unexpected error occurred."}},
        )

    prefix = "/api/v1"
    app.include_router(system_router, prefix=prefix)
    app.include_router(crm_router, prefix=prefix)
    app.include_router(mail_router, prefix=prefix)
    app.include_router(agent_router, prefix=prefix)
    app.include_router(automation_router, prefix=prefix)
    return app


app = create_app()
