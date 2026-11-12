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
    if os.getenv("APP_ENV", "").strip().lower() not in ("production", "prod"):
        load_dotenv()
except ImportError:
    pass

from backend.config import Config, redact_sensitive_data
from backend.db import Database
from backend.adapter_interface import PaypalAdapter
from backend.fake_adapter import FakePaypalAdapter
from backend.paypal.register import register_captures_from_env
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
    # 1. Environment & deployment validation
    deployment = os.getenv("VAULTLINE_DEPLOYMENT", "").strip().lower()
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env and app_env not in ("production", "prod", "development", "dev", "test"):
        raise RuntimeError(
            f"Invalid APP_ENV value: '{app_env}'. Expected 'production', 'development', or 'test'."
        )
    is_production = app_env in ("production", "prod")

    demo_mode_env = os.getenv("VAULTLINE_DEMO_MODE", "").strip().lower()
    is_demo_mode = (config.demo_mode if config else False) or (demo_mode_env in ("true", "1", "yes"))

    # Safeguard 3: For hosted deployment (Render / judge environment), require real PayPal
    if deployment == "hosted":
        raw_paypal_mode = os.getenv("VAULTLINE_USE_REAL_PAYPAL", "").strip().lower()
        if raw_paypal_mode not in ("true", "1", "yes"):
            raise RuntimeError(
                "In hosted deployment (VAULTLINE_DEPLOYMENT=hosted), VAULTLINE_USE_REAL_PAYPAL must be 'true'. "
                "Fake adapter mode is strictly prohibited in the hosted judge environment."
            )

    # 2. PayPal mode validation
    if adapter is not None:
        paypal_adapter = adapter
        is_real_paypal = not isinstance(adapter, FakePaypalAdapter)
    else:
        raw_mode = os.getenv("VAULTLINE_USE_REAL_PAYPAL")
        if raw_mode is None or raw_mode.strip() == "":
            raise RuntimeError(
                "VAULTLINE_USE_REAL_PAYPAL must be explicitly configured: "
                "set 'true' for real PayPal Sandbox adapter or 'false' for fake test double."
            )
        mode = raw_mode.strip().lower()
        if mode in ("true", "1", "yes"):
            is_real_paypal = True
        elif mode in ("false", "0", "no"):
            is_real_paypal = False
        else:
            raise RuntimeError(
                f"Invalid VAULTLINE_USE_REAL_PAYPAL value: '{raw_mode}'. "
                "Expected 'true' (real Sandbox) or 'false' (fake double)."
            )

    # Contradiction check: demo mode cannot be enabled with real PayPal or in production
    if is_demo_mode and (is_production or is_real_paypal):
        raise RuntimeError(
            "Contradictory configuration: demo mode (VAULTLINE_DEMO_MODE=true) "
            "cannot be enabled in production or with real PayPal adapter."
        )

    effective_demo_mode = is_demo_mode and not is_real_paypal and not is_production

    # 3. Credential validation (FAIL CLOSED BEFORE TOUCHING DATABASE)
    cfg = config or Config(demo_mode=effective_demo_mode)
    cfg.validate_credentials(
        is_production=is_production,
        is_real_paypal=is_real_paypal,
        is_demo_mode=effective_demo_mode,
    )

    # 4. Instantiate PayPal adapter if not passed
    if adapter is None:
        if is_real_paypal:
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
        else:
            paypal_adapter = FakePaypalAdapter()
    else:
        paypal_adapter = adapter

    # Safeguard: In hosted deployment, enforce that the selected adapter is strictly PayPalSandboxAdapter
    if deployment == "hosted":
        try:
            from backend.paypal.adapter import PayPalSandboxAdapter
        except ImportError:
            try:
                from backend.paypal import PayPalSandboxAdapter
            except ImportError as exc:
                raise RuntimeError(
                    "Hosted deployment requires PayPalSandboxAdapter, but it could not be imported."
                ) from exc
        if not isinstance(paypal_adapter, PayPalSandboxAdapter):
            raise RuntimeError(
                "In hosted deployment (VAULTLINE_DEPLOYMENT=hosted), the adapter must be "
                "an instance of PayPalSandboxAdapter. FakePaypalAdapter and other test doubles "
                "are strictly prohibited in hosted environments."
            )

    # 5. Database initialization and seeding (ONLY RUNS AFTER ALL CHECKS PASS)
    database = db or Database(cfg.db_path)
    database.init_schema()
    database.seed_defaults(
        cfg,
        demo_mode=effective_demo_mode,
        is_production=is_production,
        is_real_paypal=is_real_paypal,
    )

    # Seed default captures on fake adapter if demo mode is active, or register from env
    if hasattr(paypal_adapter, "seed_capture") and effective_demo_mode:
        paypal_adapter.seed_capture("CAP_A", Decimal("300.00"), "USD", "COMPLETED")
        paypal_adapter.seed_capture("CAP_B", Decimal("300.00"), "USD", "COMPLETED")
        paypal_adapter.seed_capture("CAP_C", Decimal("300.00"), "USD", "COMPLETED")
    else:
        register_captures_from_env(
            conn=database.get_connection(),
            adapter=paypal_adapter,
            merchant_id=cfg.merchant_id,
            is_hosted=(deployment == "hosted"),
        )

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
            "http://localhost:5174",
            "http://127.0.0.1:5174",
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

    # Safe AI provider diagnostics (zero secrets, metadata only)
    @app.get("/api/v1/diagnostics/ai", tags=["v1-Diagnostics"])
    def ai_diagnostics():
        return {
            "status": "ok",
            "primary_provider": "gemini",
            "fallback_provider": "groq",
            "gemini": {
                "configured": bool(cfg.gemini_api_key),
                "model": cfg.gemini_model,
                "timeout_seconds": cfg.ai_timeout_seconds,
                "max_retries": cfg.ai_max_retries,
            },
            "groq": {
                "configured": bool(cfg.groq_api_key),
                "model": cfg.groq_model,
                "timeout_seconds": cfg.ai_timeout_seconds,
                "max_retries": cfg.ai_max_retries,
            },
        }

    # Include routers
    app.include_router(agent_router)
    app.include_router(merchant_router)
    app.include_router(v1_router)

    return app

# Default app instance for uvicorn
app = create_app()
