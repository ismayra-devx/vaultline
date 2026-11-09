# Vaultline Security Backend

Vaultline is an authorization gateway between autonomous AI agents and PayPal. An AI agent can request a refund, but never holds PayPal credentials and cannot approve its own requests. The Vaultline backend evaluates requests against a policy ladder, deciding whether to `ALLOW`, `BLOCK`, or `ESCALATE` to a human judge. Only an isolated PayPal adapter may execute money movements.

---

## Prerequisites
- Python 3.11+
- Virtual environment (recommended)

---

## Installation

Install the required dependencies:

```bash
pip install -r requirements.txt
```

---

## Running the Server

Start the FastAPI application with Uvicorn:

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive OpenAPI documentation is available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

---

## Running Tests

Run the full automated test suite using pytest:

```bash
pytest -q
```

Or for verbose test execution:

```bash
pytest -v
```

---

## Seeded Test Credentials

Upon startup, the database is initialized in WAL mode and seeded with safe test defaults:

| Entity | Identifier / Username | Credential / Secret |
| :--- | :--- | :--- |
| **Merchant** | `merch_default` | - |
| **Judge User** | `judge_merchant` | `vaultline_judge_password_123!` |
| **Agent** | `agent_default` | `vl_agent_secret_key_abc123` |
| **Policy** | `v1` (USD only) | Auto <= $10.00, Escalate <= $25.00 |
| **Captures** | `CAP_A`, `CAP_B`, `CAP_C` | $300.00 USD each (COMPLETED) |

---

## Security Invariants

Refer to [backend/SECURITY_INVARIANTS.md](backend/SECURITY_INVARIANTS.md) for the 8 core security invariants:
1. Money is `Decimal` / decimal strings only; schemas strictly reject floats.
2. Identity comes only from credentials (`extra = "forbid"` blocks caller-supplied identity).
3. Agent credentials and merchant sessions are strictly partitioned.
4. Policy evaluation lives solely in `policy.py` and `services.py`.
5. AI risk signals can only tighten `ALLOW` to `ESCALATE`.
6. Money movements go strictly through the `PaypalAdapter` interface.
7. Secrets are never stored in or read from `.env` files; secrets are redacted as `[REDACTED]`.
8. Tests are never weakened, deleted, or skipped.

