"""CLI entrypoint for running the Vaultline AI refund agent.

Run from `mcp/`:
    python -m vaultline_agent --email "Please refund $5.00 for capture CAP_A (duplicate charge)"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from .agent import RefundAgent
from .config import AgentConfigError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vaultline_agent",
        description="Vaultline AI refund agent (Gemini extraction + Vaultline MCP stdio client).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--email", type=str, help="Untrusted customer email text to process.")
    group.add_argument("--email-file", type=Path, help="Path to a text file containing the customer email.")
    group.add_argument("--check-status", type=str, metavar="REQUEST_ID", help="Check status of an existing refund request via MCP.")
    group.add_argument("--check-client-id", type=str, metavar="CLIENT_REQUEST_ID", help="Check status of an existing refund request by client_request_id via MCP.")
    parser.add_argument("--client-request-id", type=str, default=None, help="Optional idempotency key for request_refund.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and validate the email only; never connect to MCP or submit a refund.",
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    agent = RefundAgent()
    if args.check_status or args.check_client_id:
        if args.dry_run:
            raise AgentConfigError("--dry-run cannot be combined with status lookup.")
        status = await agent.check_status(
            args.check_status,
            client_request_id=args.check_client_id,
        )
        print(json.dumps(status, indent=2))
        return 0

    email_text = args.email
    if args.email_file is not None:
        email_text = args.email_file.read_text(encoding="utf-8")

    result = await agent.process_email(
        email_text,
        client_request_id=args.client_request_id,
        dry_run=args.dry_run,
    )
    print(result.model_dump_json(indent=2))
    return 0 if result.status in {"COMPLETED", "ESCALATED", "BLOCKED", "DRY_RUN"} else 1


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")
    parser = _build_parser()
    args = parser.parse_args()
    try:
        code = asyncio.run(_run(args))
    except AgentConfigError as exc:
        print(f"vaultline-agent: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()

