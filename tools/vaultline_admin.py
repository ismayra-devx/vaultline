"""Merchant CLI for a hosted Vaultline (works the same on Windows, macOS and Linux).

    python tools/vaultline_admin.py --api https://vaultline-api.onrender.com register-capture 1AB23456CD789012E
    python tools/vaultline_admin.py --api https://vaultline-api.onrender.com approvals
    python tools/vaultline_admin.py --api https://vaultline-api.onrender.com approve <approval_id>
    python tools/vaultline_admin.py --api https://vaultline-api.onrender.com deny <approval_id>
    python tools/vaultline_admin.py --api https://vaultline-api.onrender.com refunds

Logs in as the merchant (judge) user. Username defaults to judge_merchant; the password is read
from VAULTLINE_JUDGE_PASSWORD or prompted (never echoed, never written to disk). Only https
APIs are accepted. This CLI cannot create or execute refunds: agents propose, Vaultline decides.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from urllib.parse import urlparse

import httpx


def api_client(base: str) -> httpx.Client:
    base = base.rstrip("/")
    u = urlparse(base)
    if u.scheme != "https" or not u.hostname or u.hostname in {"localhost", "127.0.0.1"}:
        sys.exit("--api must be the hosted https URL of vaultline-api, e.g. https://vaultline-api.onrender.com")
    return httpx.Client(base_url=base, timeout=60, follow_redirects=False)  # free tier may need ~60s to wake


def login(c: httpx.Client, username: str) -> dict:
    password = os.getenv("VAULTLINE_JUDGE_PASSWORD") or getpass.getpass(f"Password for {username}: ")
    r = c.post("/api/v1/auth/login", json={"username": username, "password": password})
    if r.status_code != 200:
        sys.exit(f"Login failed (HTTP {r.status_code}). Check the judge password in the vaultline-api Environment tab.")
    return {"Authorization": f"Bearer {r.json()['session_token']}"}


def show(r: httpx.Response) -> None:
    try:
        body = r.json()
    except ValueError:
        body = r.text
    print(f"HTTP {r.status_code}")
    print(json.dumps(body, indent=2) if not isinstance(body, str) else body)
    if r.status_code >= 400:
        sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(description="Vaultline merchant CLI")
    p.add_argument("--api", default=os.getenv("VAULTLINE_API_URL", ""), help="hosted vaultline-api URL (or set VAULTLINE_API_URL)")
    p.add_argument("--user", default=os.getenv("VAULTLINE_JUDGE_USERNAME", "judge_merchant"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    rc = sub.add_parser("register-capture", help="verify a sandbox capture with PayPal and make it refundable")
    rc.add_argument("capture_id")
    sub.add_parser("approvals", help="list pending approvals")
    for name in ("approve", "deny"):
        a = sub.add_parser(name)
        a.add_argument("approval_id")
        a.add_argument("--reason", default=None)
    sub.add_parser("refunds", help="list recent refund requests")
    sub.add_parser("audit", help="audit chain status and events")
    args = p.parse_args()
    if not args.api:
        sys.exit("Pass --api https://<your vaultline-api>.onrender.com (or set VAULTLINE_API_URL).")

    with api_client(args.api) as c:
        if args.cmd == "health":
            return show(c.get("/health"))
        auth = login(c, args.user)
        if args.cmd == "register-capture":
            show(c.post("/api/v1/captures/register", json={"capture_id": args.capture_id}, headers=auth))
        elif args.cmd == "approvals":
            show(c.get("/api/v1/approvals", headers=auth))
        elif args.cmd in ("approve", "deny"):
            body = {"reason": args.reason} if args.reason else {}
            show(c.post(f"/api/v1/approvals/{args.approval_id}/{args.cmd}", json=body, headers=auth))
        elif args.cmd == "refunds":
            show(c.get("/api/v1/dashboard/refunds", headers=auth))
        elif args.cmd == "audit":
            show(c.get("/api/v1/audit", headers=auth))


if __name__ == "__main__":
    main()
