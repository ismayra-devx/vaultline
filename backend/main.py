import logging
import os
import uuid
from decimal import Decimal
from typing import Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

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

    if adapter:
        paypal_adapter = adapter
    elif os.getenv("VAULTLINE_USE_REAL_PAYPAL", "false").lower() in ("true", "1", "yes"):
        try:
            from backend.paypal.adapter import PayPalSandboxAdapter
            paypal_adapter = PayPalSandboxAdapter()
        except ImportError:
            paypal_adapter = FakePaypalAdapter()
    else:
        paypal_adapter = FakePaypalAdapter()

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

    app = FastAPI(
        title="Vaultline Security Gateway",
        description="Authorization gateway between autonomous AI agents and PayPal.",
        version="1.0.0",
    )

    # Attach shared singletons to app state
    app.state.config = cfg
    app.state.db = database
    app.state.adapter = paypal_adapter
    app.state.service = vaultline_service

    # Middleware
    app.add_middleware(PureASGICorrelationMiddleware)

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
