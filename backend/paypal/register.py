import logging
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional
from backend.adapter_interface import PaypalAdapter

log = logging.getLogger("vaultline.paypal")


def register_capture(
    conn: sqlite3.Connection,
    adapter: PaypalAdapter,
    merchant_id: str,
    capture_id: str,
) -> Dict[str, Any]:
    """Verify a capture with PayPal and record it as refundable for this merchant.

    COMPLETED captures start at zero refunded. PARTIALLY_REFUNDED captures (e.g. after a
    free-tier restart wiped the database) start from PayPal's own refund history, so one large
    sandbox capture can serve many demo runs. Re-registering an existing capture is a no-op.
    """
    existing = conn.execute(
        "SELECT merchant_id, original_amount, refunded_amount, status FROM captures WHERE id = ?",
        (capture_id,),
    ).fetchone()
    if existing:
        if existing["merchant_id"] != merchant_id:
            raise ValueError(f"Capture '{capture_id}' is already registered to another merchant.")
        return {
            "capture_id": capture_id,
            "status": existing["status"],
            "amount": existing["original_amount"],
            "registered": False,
        }

    cap = adapter.get_capture(capture_id)
    if cap.currency != "USD":
        raise ValueError(f"Only USD captures are supported; capture has currency '{cap.currency}'.")
    if cap.status == "COMPLETED":
        refunded = Decimal("0")
    elif cap.status == "PARTIALLY_REFUNDED" and hasattr(adapter, "get_refunded_total"):
        refunded = Decimal(adapter.get_refunded_total(capture_id))
    else:
        raise ValueError(f"Capture status is {cap.status}; only fresh COMPLETED captures can be registered.")

    original = Decimal(str(cap.amount))
    if refunded >= original:
        raise ValueError("Capture has no refundable balance left.")
    status = "COMPLETED" if refunded == 0 else "PARTIALLY_REFUNDED"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO captures (id, merchant_id, original_amount, currency, refunded_amount, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (cap.capture_id, merchant_id, f"{original:.2f}", cap.currency, f"{refunded:.2f}", status, now_iso),
    )
    conn.commit()
    log.info("Registered sandbox capture %s amount=%s already_refunded=%s", cap.capture_id, original, refunded)
    return {
        "capture_id": cap.capture_id,
        "status": status,
        "amount": f"{original:.2f}",
        "registered": True,
    }


def register_captures_from_env(
    conn: sqlite3.Connection,
    adapter: PaypalAdapter,
    merchant_id: str,
    is_hosted: Optional[bool] = None,
) -> None:
    """Startup hook: register VAULTLINE_SANDBOX_CAPTURE_IDS (comma separated).

    In hosted mode (VAULTLINE_DEPLOYMENT=hosted), required demo captures must be successfully
    registered before the deployment is considered ready; any failure or missing configuration
    raises a RuntimeError to fail startup closed.
    """
    if is_hosted is None:
        deployment = os.getenv("VAULTLINE_DEPLOYMENT", "").strip().lower()
        is_hosted = deployment == "hosted"

    raw_ids = os.getenv("VAULTLINE_SANDBOX_CAPTURE_IDS", "")
    ids = [c.strip() for c in raw_ids.split(",") if c.strip()]
    if is_hosted and not ids:
        raise RuntimeError(
            "In hosted deployment (VAULTLINE_DEPLOYMENT=hosted), VAULTLINE_SANDBOX_CAPTURE_IDS "
            "must be configured with at least one valid PayPal Sandbox capture ID."
        )

    for cid in ids:
        try:
            register_capture(conn, adapter, merchant_id, cid)
        except Exception as exc:
            log.warning("Could not register sandbox capture %s: %s", cid, exc)
            if is_hosted:
                raise RuntimeError(
                    f"Hosted deployment failed: required sandbox capture '{cid}' could not be registered: {exc}"
                ) from exc
