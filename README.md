# Vaultline

### AI Financial Security Infrastructure

**Every agent has a limit. Every transaction needs authority.**

Vaultline is a PayPal-first, MCP-native financial authorization gateway that enables AI agents to request financial actions within merchant-defined limits.

AI agents can propose financial actions, but they cannot independently execute money-moving operations beyond their granted authority.

---

## Core Concept

Vaultline acts as a security gateway between autonomous AI agents and PayPal.

Every transaction passes through:
1. Agent authentication
2. Deterministic policy evaluation
3. Risk assessment
4. Merchant approval when required
5. Transaction validation
6. PayPal Sandbox execution
7. Audit logging and status verification

---

## Policy Model

| Refund amount | Decision |
|---|---|
| Up to $10.00 | ALLOW |
| Above $10.00 up to $25.00 | ESCALATE |
| Above $25.00 | BLOCK |

Deterministic backend rules are authoritative. AI risk assessment may tighten a decision (`ALLOW` $\to$ `ESCALATE`) but must never override a `BLOCK` into an `ALLOW`.

---

## Architecture Flow

```
Agent → Vaultline MCP → FastAPI Security Gateway → Policy Engine → Approval Workflow → PayPal Adapter → PayPal Sandbox
```

The merchant dashboard provides visibility and approval controls. It is not the security boundary.

---

## Team Responsibilities

| Role | Owner | Responsibility |
|---|---|---|
| **P1** | Ismayra | PayPal integration, hosting and release |
| **P2** | Ankit | Security backend, authentication and policies |
| **P3** | Mohd | MCP server, agent workflow, AI risk and audit |
| **P4** | Mohd | Frontend, merchant dashboard and AG Studio |

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
# In fake/demo mode (default)
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

## Security Invariants & Architecture

- **Architecture Baseline**: Refer to [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the frozen synchronous contract and system architecture decisions.
- **Security Invariants**: Refer to [backend/SECURITY_INVARIANTS.md](backend/SECURITY_INVARIANTS.md) for the 8 core security invariants:
  1. Money is `Decimal` / decimal strings only; schemas strictly reject floats.
  2. Identity comes only from credentials (`extra = "forbid"` blocks caller-supplied identity).
  3. Agent credentials and merchant sessions are strictly partitioned.
  4. Policy evaluation lives solely in `policy.py` and `services.py`.
  5. AI risk signals can only tighten `ALLOW` to `ESCALATE`.
  6. Money movements go strictly through the `PaypalAdapter` interface.
  7. Secrets are never stored in or read from `.env` files; secrets are redacted as `[REDACTED]`.
  8. Tests are never weakened, deleted, or skipped.

---

## VLT-01 Environment & PayPal Sandbox Configuration

The backend fails closed unless `VAULTLINE_USE_REAL_PAYPAL` and required credentials are explicitly configured:

- **PayPal Sandbox mode**: Set `VAULTLINE_USE_REAL_PAYPAL=true` with `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and `VAULTLINE_SANDBOX_CAPTURE_IDS` (comma-separated real PayPal Sandbox capture IDs).
- **Local demo mode (fake double)**: Set `VAULTLINE_USE_REAL_PAYPAL=false` and `VAULTLINE_DEMO_MODE=true`.
- **Credentials**: Set `VAULTLINE_JUDGE_USERNAME`, `VAULTLINE_JUDGE_PASSWORD`, and `VAULTLINE_AGENT_KEY` (required outside explicit demo mode).
- **Hosted deployment**: Set `VAULTLINE_DEPLOYMENT=hosted` (enforces `PayPalSandboxAdapter` and non-empty `VAULTLINE_SANDBOX_CAPTURE_IDS`).

---

## Running the P3 AI Agent (`mcp/vaultline_agent`)

From the `mcp/` directory (with `GEMINI_API_KEY` and `VAULTLINE_AGENT_KEY` configured):

```bash
python -m vaultline_agent --email "Please refund $5.00 for capture CAP_A" --dry-run
```

Omit `--dry-run` to execute the full `AI Agent -> MCP Server -> FastAPI Gateway` flow.

---

## AI Disclosure

Vaultline uses Google Gemini (`mcp/vaultline_agent/gemini_client.py`) solely to extract structured refund fields and detect untrusted prompt-injection indicators from customer emails. All refund authorization decisions (`ALLOW`, `ESCALATE`, `BLOCK`), policy thresholds, human-in-the-loop merchant approvals, and PayPal Sandbox executions are enforced deterministically by the FastAPI backend. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party licenses and service notices.
