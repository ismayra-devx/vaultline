from contextlib import asynccontextmanager
import logging
import os
import uuid
from decimal import Decimal
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.config import Config, redact_sensitive_data
from backend.db import Database
from backend.adapter_interface import PaypalAdapter
from backend.fake_adapter import FakePaypalAdapter
from backend.services import VaultlineService
from backend.routes_agent import router as agent_router
from backend.routes_merchant import router as merchant_router
from backend.routes_v1 import router as v1_router

# Configure logging to redact secrets
class SecretRedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return str(redact_sensitive_data(msg))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vaultline")

class PureASGICorrelationMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = None
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == b"x-request-id":
                correlation_id = header_value.decode("latin-1")
                break
        if not correlation_id:
            correlation_id = f"corr_{uuid.uuid4().hex}"

        path = scope.get("path", "")
        method = scope.get("method", "")
        logger.info(f"[{correlation_id}] Started {method} {path}")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", correlation_id.encode("latin-1")))
                message["headers"] = headers
                status_code = message.get("status")
                logger.info(f"[{correlation_id}] Finished {method} {path} with status {status_code}")
            await send(message)

        await self.app(scope, receive, send_wrapper)

def create_app(
    config: Optional[Config] = None,
    db: Optional[Database] = None,
    adapter: Optional[PaypalAdapter] = None,
    service: Optional[VaultlineService] = None,
) -> FastAPI:
    cfg = config or Config()
    database = db or Database(cfg.db_path)
    database.init_schema()
    database.seed_defaults(cfg)

    if adapter is not None:
        paypal_adapter = adapter
    else:
        raw_mode = os.getenv("VAULTLINE_USE_REAL_PAYPAL")
        if raw_mode is None or raw_mode.strip() == "":
            raise RuntimeError(
                "VAULTLINE_USE_REAL_PAYPAL must be explicitly configured: "
                "set 'true' for real PayPal Sandbox adapter or 'false' for fake test double."
            )
        mode = raw_mode.strip().lower()
        if mode in ("true", "1", "yes"):
            try:
                from backend.paypal.adapter import PayPalSandboxAdapter
            except ImportError:
                try:
                    from backend.paypal import PayPalSandboxAdapter
                except ImportError as exc:
                    raise RuntimeError(
                        "Real PayPal mode requested (VAULTLINE_USE_REAL_PAYPAL=true), "
                        "but failed to import PayPalSandboxAdapter from 'backend.paypal.adapter' "
                        f"or 'backend.paypal': {exc}"
                    ) from exc
            try:
                paypal_adapter = PayPalSandboxAdapter.from_env()
            except Exception as exc:
                raise RuntimeError(
                    f"Real PayPal mode requested (VAULTLINE_USE_REAL_PAYPAL=true), "
                    f"but PayPalSandboxAdapter.from_env() initialization failed: {exc}"
                ) from exc
        elif mode in ("false", "0", "no"):
            paypal_adapter = FakePaypalAdapter()
        else:
            raise RuntimeError(
                f"Invalid VAULTLINE_USE_REAL_PAYPAL value: '{raw_mode}'. "
                "Expected 'true' (real Sandbox) or 'false' (fake double)."
            )

    # Seed default captures if supported (e.g. test doubles)
    if hasattr(paypal_adapter, "seed_capture"):
        paypal_adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
        paypal_adapter.seed_capture("CAP_B", Decimal("300.00"), "USD", "COMPLETED")
        paypal_adapter.seed_capture("CAP_C", Decimal("300.00"), "USD", "COMPLETED")

    vaultline_service = service or VaultlineService(
        db=database,
        adapter=paypal_adapter,
        config=cfg,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # Application shutdown: close PayPal adapter and other network resources
        if hasattr(paypal_adapter, "close"):
            try:
                paypal_adapter.close()
                logger.info("Closed PayPal adapter connection.")
            except Exception as exc:
                logger.warning("Error closing PayPal adapter: %s", exc)

    app = FastAPI(
        title="Vaultline Security Gateway",
        description="Authorization gateway between autonomous AI agents and PayPal.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Attach shared singletons to app state
    app.state.config = cfg
    app.state.db = database
    app.state.adapter = paypal_adapter
    app.state.service = vaultline_service

    # Middleware
    app.add_middleware(PureASGICorrelationMiddleware)

    cors_origins_raw = os.getenv("VAULTLINE_ALLOWED_ORIGINS", "").strip()
    if cors_origins_raw == "*":
        allowed_origins = ["*"]
    elif cors_origins_raw:
        allowed_origins = [o.strip().rstrip("/") for o in cors_origins_raw.split(",") if o.strip()]
    else:
        allowed_origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]

    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Token", "X-Request-ID", "Accept"],
    )

    # Health check
    @app.get("/health", tags=["Health"])
    def health_check():
        return {"status": "ok"}

    # Include routers
    app.include_router(agent_router)
    app.include_router(merchant_router)
    app.include_router(v1_router)

    return app

# Default app instance for uvicorn
app = create_app()
