"""PayPal integration (P1): adapter selection and sandbox capture registration."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

from backend.adapter_interface import PaypalAdapter

from .sandbox_adapter import (
    SANDBOX_BASE_URL,
    PayPalError,
    PayPalOutcomeUnknown,
    PayPalSandboxAdapter,
)

log = logging.getLogger("vaultline.paypal")

__all__ = [
    "PayPalSandboxAdapter",
    "PayPalError",
    "PayPalOutcomeUnknown",
    "paypal_mode",
    "build_adapter_from_env",
    "register_capture",
]


def paypal_mode() -> str:
    """'fake' (default, local dev/tests) or 'sandbox' (real PayPal Sandbox)."""
    mode = os.getenv("VAULTLINE_PAYPAL_MODE", "fake").strip().lower()
    if mode not in {"fake", "sandbox"}:
        raise RuntimeError("VAULTLINE_PAYPAL_MODE must be 'fake' or 'sandbox'.")
    return mode


def build_adapter_from_env() -> PaypalAdapter | None:
    """Return a sandbox adapter when configured, else None (caller falls back to the fake)."""
    if paypal_mode() != "sandbox":
        return None
    base = os.getenv("PAYPAL_BASE_URL", SANDBOX_BASE_URL)
    return PayPalSandboxAdapter(
        os.getenv("PAYPAL_CLIENT_ID", "").strip(),
        os.getenv("PAYPAL_CLIENT_SECRET", "").strip(),
        base_url=base,
    )


def register_capture(conn: sqlite3.Connection, adapter: PaypalAdapter, merchant_id: str, capture_id: str) -> dict:
    """Verify a capture with PayPal and record it as refundable for this merchant.

    COMPLETED captures start at zero refunded. PARTIALLY_REFUNDED captures (e.g. after a
    free-tier restart wiped the database) start from PayPal's own refund history, so one large
    sandbox capture can serve many demo runs. Re-registering an existing capture is a no-op.
    """
    existing = conn.execute("SELECT merchant_id, original_amount, refunded_amount, status FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if existing:
        if existing["merchant_id"] != merchant_id:
            raise ValueError("Capture is already registered to another merchant.")
        return {"capture_id": capture_id, "status": existing["status"], "amount": existing["original_amount"], "registered": False}

    cap = adapter.get_capture(capture_id)
    if cap.currency != "USD":
        raise ValueError("Only USD captures are supported.")
    if cap.status == "COMPLETED":
        refunded = Decimal("0")
    elif cap.status == "PARTIALLY_REFUNDED" and hasattr(adapter, "get_refunded_total"):
        # Start from PayPal's own refund history, never from a guess.
        refunded = Decimal(adapter.get_refunded_total(capture_id))
    else:
        raise ValueError(f"Capture status is {cap.status}; it cannot be registered as refundable.")
    original = Decimal(cap.amount)
    if refunded >= original:
        raise ValueError("Capture has no refundable balance left.")
    status = "COMPLETED" if refunded == 0 else "PARTIALLY_REFUNDED"
    conn.execute(
        "INSERT INTO captures (id, merchant_id, original_amount, currency, refunded_amount, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cap.capture_id, merchant_id, f"{original:.2f}", cap.currency, f"{refunded:.2f}", status, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    log.info("registered sandbox capture %s amount=%s already_refunded=%s", cap.capture_id, original, refunded)
    return {"capture_id": cap.capture_id, "status": status, "amount": f"{original:.2f}", "registered": True}


def register_captures_from_env(conn: sqlite3.Connection, adapter: PaypalAdapter, merchant_id: str) -> None:
    """Startup hook: register VAULTLINE_SANDBOX_CAPTURE_IDS (comma separated). Failures are logged, not fatal."""
    ids = [c.strip() for c in os.getenv("VAULTLINE_SANDBOX_CAPTURE_IDS", "").split(",") if c.strip()]
    for cid in ids:
        try:
            register_capture(conn, adapter, merchant_id, cid)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not register sandbox capture %s: %s", cid, exc)
