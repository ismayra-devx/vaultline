"""PayPal Sandbox helper CLI (run on your own machine; needs internet access to PayPal).

    # 1. Check credentials and look at a capture (no money moves)
    python -m backend.paypal.sandbox_tools check [CAPTURE_ID]

    # 2. Create a fresh capture to demo with (opens a sandbox buyer approval link)
    python -m backend.paypal.sandbox_tools create-capture --amount 1000.00

Reads PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET from the environment. Sandbox host only.
This tool never issues refunds: refunds only happen through Vaultline's policy flow.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid

import httpx

from .sandbox_adapter import SANDBOX_BASE_URL, PayPalError, PayPalSandboxAdapter


def _adapter() -> PayPalSandboxAdapter:
    cid, secret = os.getenv("PAYPAL_CLIENT_ID", ""), os.getenv("PAYPAL_CLIENT_SECRET", "")
    if not cid or not secret:
        sys.exit("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (from developer.paypal.com > Apps & Credentials > Sandbox).")
    return PayPalSandboxAdapter(cid, secret)


def check(capture_id: str | None) -> None:
    a = _adapter()
    a._access_token()  # noqa: SLF001 - explicit credential check
    print("OK: PayPal Sandbox accepted the client credentials.")
    if capture_id:
        cap = a.get_capture(capture_id)
        print(f"Capture {cap.capture_id}: status={cap.status} amount={cap.amount} {cap.currency}")
        if cap.status != "COMPLETED":
            print("Note: Vaultline only registers fresh COMPLETED captures.")


def create_capture(amount: str, wait_seconds: int) -> None:
    if not re.fullmatch(r"\d{1,6}(\.\d{2})?", amount):
        sys.exit("--amount must look like 30.00")
    a = _adapter()
    headers = {"Authorization": f"Bearer {a._access_token()}", "Content-Type": "application/json"}  # noqa: SLF001
    with httpx.Client(base_url=SANDBOX_BASE_URL, timeout=30, headers=headers) as http:
        order = http.post(
            "/v2/checkout/orders",
            headers={"PayPal-Request-Id": f"vl-order-{uuid.uuid4()}"},
            json={
                "intent": "CAPTURE",
                "purchase_units": [{"amount": {"currency_code": "USD", "value": amount}, "description": "Vaultline demo order"}],
                "payment_source": {"paypal": {"experience_context": {"user_action": "PAY_NOW", "return_url": "https://example.com/return", "cancel_url": "https://example.com/cancel"}}},
            },
        )
        if order.status_code not in (200, 201):
            sys.exit(f"Order creation failed: HTTP {order.status_code} {order.text[:300]}")
        body = order.json()
        approve = next((l["href"] for l in body.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
        print(f"\nOrder {body['id']} created for ${amount} USD.")
        print("Open this link and log in with a SANDBOX PERSONAL (buyer) account, then click Pay:")
        print(f"\n  {approve}\n")
        print("(Sandbox buyer accounts: developer.paypal.com > Testing Tools > Sandbox Accounts.)")
        print("After paying you'll land on example.com - that's expected. Waiting for approval...")
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            st = http.get(f"/v2/checkout/orders/{body['id']}").json().get("status")
            if st == "APPROVED":
                break
            time.sleep(4)
        else:
            sys.exit("Timed out waiting for buyer approval. Run again.")
        cap = http.post(f"/v2/checkout/orders/{body['id']}/capture", headers={"PayPal-Request-Id": f"vl-cap-{uuid.uuid4()}"}, json={})
        if cap.status_code not in (200, 201):
            sys.exit(f"Capture failed: HTTP {cap.status_code} {cap.text[:300]}")
        capture = cap.json()["purchase_units"][0]["payments"]["captures"][0]
        print(f"\nCAPTURE ID: {capture['id']}  (status {capture['status']}, ${capture['amount']['value']} USD)")
        print("Register it in Vaultline: POST /api/v1/captures/register  {\"capture_id\": \"%s\"}" % capture["id"])
        print("or add it to VAULTLINE_SANDBOX_CAPTURE_IDS.")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m backend.paypal.sandbox_tools")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="verify credentials, optionally look up a capture")
    c.add_argument("capture_id", nargs="?")
    cc = sub.add_parser("create-capture", help="create and capture a sandbox order to demo with")
    cc.add_argument("--amount", default="1000.00")
    cc.add_argument("--wait", type=int, default=300, help="seconds to wait for buyer approval")
    args = p.parse_args()
    try:
        if args.cmd == "check":
            check(args.capture_id)
        else:
            create_capture(args.amount, args.wait)
    except PayPalError as exc:
        sys.exit(f"PayPal error: {exc}")


if __name__ == "__main__":
    main()
