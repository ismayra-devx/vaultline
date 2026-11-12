# Vaultline Security Invariants

Vaultline is an authorization gateway between autonomous AI agents and PayPal.
The following invariants are inviolable and must be upheld across all components:

1. **Money is Decimal / decimal strings only, never float.**
   - All monetary amounts must be handled using Python's `Decimal` type or string representations (e.g. `"10.00"`).
   - Floats are strictly rejected at the schema level to avoid IEEE 754 precision loss and rounding attacks.

2. **Identity comes only from credentials.**
   - Identity is derived exclusively from the authenticated credential (Agent API key or Merchant session token).
   - Request bodies strictly enforce `extra = "forbid"`. Fields such as `merchant_id` and `agent_id` can never be supplied by callers in request payloads.

3. **Agent credentials and merchant sessions are strictly partitioned.**
   - An Agent API key receives `403 Forbidden` on every `/api/merchant/*` route.
   - A Merchant session token receives `403 Forbidden` on every `/api/agent/*` route.
   - Cross-credential role escalation is strictly prohibited.

4. **Policy lives only in `policy.py` and `services.py`.**
   - Routes are thin controllers that authenticate requests and delegate directly to services.
   - External callers (such as teammate MCP servers) interact directly with services while preserving identical policy enforcement.

5. **AI risk signals can only tighten authorization, never loosen it.**
   - An AI risk signal can only tighten an `ALLOW` decision to `ESCALATE`.
   - AI errors, timeouts, or unparseable outputs must default to tightening and never yield `ALLOW`.
   - An AI risk signal never loosens a `BLOCK` or `ESCALATE` decision.

6. **Money-moving calls go strictly through the `PaypalAdapter` interface.**
   - All PayPal interactions pass through `adapter_interface.py` (`get_capture`, `refund_capture`, `get_refund`).
   - Adapters must support stable idempotency via `paypal_request_id`.
   - Production PayPal credentials never touch the gateway or agent layers directly.

7. **Secrets management and log sanitization.**
   - Secrets are never read from or written to `.env` files at runtime.
   - Secrets are never hardcoded or logged in plaintext.
   - Seed credentials are read from environment variables with safe defaults for testing.
   - All logs, error details, and audit payloads redact secrets as `[REDACTED]`.

8. **Test integrity is non-negotiable.**
   - Never weaken, delete, or skip a test to make it pass.
   - Fix the implementation code instead.

