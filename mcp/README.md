# Vaultline MCP Server (P3)

The restricted interface through which AI agents talk to Vaultline. It exposes **exactly three tools** and forwards the agent's credential to the FastAPI security backend, which makes every decision.

> The agent proposes. Vaultline authorizes. This server is an interface, **not** a security boundary.

## Tools

| Tool | Backend call | Notes |
|---|---|---|
| `request_refund(capture_id, amount, reason, currency="USD", client_request_id?)` | `POST /api/v1/refunds` | `amount` is a decimal string (`"5.00"`), floats rejected. `client_request_id` is generated if omitted and returned so the agent can retry idempotently. |
| `get_request_status(request_id)` | `GET /api/v1/refunds/{request_id}` | `request_id` must match `^[A-Za-z0-9_-]{1,64}$`, so no path injection into other routes. |
| `get_agent_policy()` | `GET /api/v1/agents/me/policy` | Returns the limits plus a plain-English summary. |

Every refund result includes a deterministic `outcome` sentence. It only says a refund happened when `execution_status` is `COMPLETED` **and** a PayPal refund ID is present. BLOCK, ESCALATE, PENDING and UNKNOWN are never described as success.

**Not exposed (by design):** approve/deny, raw PayPal calls, arbitrary HTTP, shell, database, policy editing, audit mutation, and the backend's `ai_risk_flag` input.

## Security properties

- **Credential:** `VAULTLINE_AGENT_KEY` is read from the MCP server's own environment, set by whoever deploys it. It is never a tool argument, never sent to the model, never logged, and hidden from `repr`. It is sent only as `Authorization: Bearer …` to the configured backend.
- **Identity:** the server never sends `agent_id` or `merchant_id`. If the model adds them as arguments they are dropped and never forwarded; the backend derives identity from the credential.
- **Backend allowlist:** `VAULTLINE_API_BASE_URL` must be an origin only (no path or credentials), must use `https`, and may use `http` only for localhost. Redirects are never followed, so the bearer can't be bounced to another host.
- **Input validation:** strict patterns on IDs and amounts, USD only, reason ≤ 500 chars. This is defence in depth; the backend validates again.
- **Failure handling:**
  - Backend down returns a clear tool error. Nothing is simulated and ALLOW is never returned.
  - A timeout while submitting returns `OUTCOME_UNCERTAIN` with the `client_request_id` so the same request can be retried safely. The server never resubmits by itself.
  - HTTP errors are mapped to safe messages (401/403/404/409/422/429/5xx). Stack traces and tokens are never returned.
- **Output whitelist:** only decision/status fields go back to the agent. The backend's `risk_summary` and internal counters are dropped.

## Run

```bash
cd mcp
pip install -r requirements.txt          # Python 3.11+, MCP SDK 2.x
cp .env.example .env                     # fill VAULTLINE_AGENT_KEY (never commit .env)
export $(grep -v '^#' .env | xargs)
python -m vaultline_mcp                  # stdio transport
```

Run it from inside `mcp/` (the repo folder name `mcp` would otherwise be confused with the SDK package).

### Connect an MCP host (e.g. Claude Desktop / any stdio MCP client)

```json
{
  "mcpServers": {
    "vaultline": {
      "command": "python",
      "args": ["-m", "vaultline_mcp"],
      "cwd": "/absolute/path/to/vaultline/mcp",
      "env": {
        "VAULTLINE_API_BASE_URL": "http://localhost:8000",
        "VAULTLINE_AGENT_KEY": "<agent key from the backend seed / setup script>"
      }
    }
  }
}
```

## Tests

```bash
cd mcp
pytest -q                                  # 41 unit tests, mocked backend, no network
```

Integration tests launch the real stdio server process against a running backend:

```bash
# terminal 1 — backend from the security-backend branch (fake PayPal adapter by default)
uvicorn backend.main:app --port 8000
# terminal 2
cd mcp
VAULTLINE_TEST_API_BASE_URL=http://localhost:8000 \
VAULTLINE_TEST_AGENT_KEY=<seeded agent key> pytest -q   # 49 passed
```

Verified on 10 Nov 2026 against `security-backend` with MCP SDK 2.3.0:

| Scenario | Result |
|---|---|
| Prompt-injected $250 | `BLOCK`, `execution_status NOT_STARTED`, backend `adapter_call_count 0` |
| $20 | `ESCALATE`, "Awaiting merchant approval" |
| $5 | `ALLOW` |
| Same `client_request_id` twice | Same `request_id` (idempotent) |
| Wrong agent key | Rejected |
| Unknown request | Not found |
| Tool list over stdio | Exactly the 3 tools |

## Integration notes for P2

- Uses only `/api/v1/refunds`, `/api/v1/refunds/{id}` and `/api/v1/agents/me/policy` with `Authorization: Bearer`.
- Parses both the contract error envelope `{"error": {"code", "message"}}` and FastAPI `{"detail": …}`.
- The backend schema accepts `ai_risk_flag` / `ai_risk_explanation` from the agent. MCP never sends them. P2 should consider computing risk server-side only, so an agent calling the API directly can't influence it.
- Remote transport (streamable HTTP with per-agent auth) is not implemented. Use stdio, with the MCP server running next to the agent host. Before exposing it over a network, add TLS and per-agent authentication at the transport.

## Layout

```
mcp/
  vaultline_mcp/config.py          env settings + backend URL allowlist
  vaultline_mcp/backend_client.py  HTTP client, error mapping, uncertain-outcome handling
  vaultline_mcp/server.py          the 3 tools, output whitelist, outcome text
  vaultline_agent/                 AI refund agent (Gemini extraction + MCP stdio client)
  tests/test_unit.py               mocked-backend MCP server tests
  tests/test_agent.py              AI agent + security/outage unit tests
  tests/test_integration.py        stdio server -> real backend
```

## Vaultline AI Agent (`vaultline_agent`)

`vaultline_agent` is the AI layer sitting in front of `vaultline_mcp`. It reads an untrusted customer email, calls Gemini to extract a structured refund proposal, validates the output strictly with Pydantic, and submits the proposal to Vaultline strictly through the three-tool MCP stdio interface (`get_agent_policy`, `request_refund`, `get_request_status`).

### Architecture & Responsibility Split

- **AI extracts and explains:** Gemini extracts `capture_id`, decimal-string `amount`, `currency`, `reason`, `suspicious_indicators`, and `risk_explanation`. Customer email text is wrapped as untrusted data and scanned for prompt-injection cues.
- **Deterministic backend authorizes:** The FastAPI security backend is the sole authority for `ALLOW` (up to `$10.00`), `ESCALATE` (`>$10.00` to `$25.00`), and `BLOCK` (`>$25.00`). AI output can never approve a refund, override policy thresholds, or communicate with PayPal directly.

### Environment Configuration

`vaultline_agent` loads the repository root `.env` automatically (without overriding existing process environment variables):

- `GEMINI_API_KEY`: Gemini API key (held with `repr=False`, never logged, never passed to MCP tool arguments or the MCP server subprocess).
- `GEMINI_MODEL`: Gemini model name (default: `gemini-3.8-flash`).
- `GEMINI_TIMEOUT_SECONDS`: Bounded per-request timeout in seconds (default: `15`).
- `GEMINI_MAX_RETRIES`: Bounded retry count for transient errors (`429`, `500`, `502`, `503`, `504`, timeouts; default: `2`, max: `3`).
- `VAULTLINE_API_BASE_URL`: Vaultline FastAPI backend origin for the MCP server (default: `http://localhost:8000`).
- `VAULTLINE_AGENT_KEY`: Agent bearer credential passed only to the MCP stdio server subprocess environment (never to Gemini or model arguments).

### Launch Command

Run from inside `mcp/` (so the repo `mcp/` folder does not shadow the `mcp` SDK package):

```bash
cd mcp
python -m vaultline_agent --email "Please refund \$5.00 for capture CAP_A (duplicate charge)"
```

Or from a file / status check:

```bash
python -m vaultline_agent --email-file path/to/email.txt
python -m vaultline_agent --check-status <request_id>
```

### Gemini Outage & Fail-Closed Fallback Behaviour

If Gemini is unavailable (e.g., HTTP `503` capacity outage), times out after bounded retries, or returns missing/malformed/schema-invalid output:

- **Fail closed:** The agent stops immediately and **never** calls `request_refund`.
- **Clear provenance:** The result sets `"source": "deterministic_fallback"` and `"ai_extracted": false`, explaining why processing stopped without fabricating AI extraction or submitting a refund.

### AI Disclosure (Hackathon Judging)

- **AI Provider & Model:** Google Gemini (`gemini-3.8-flash` by default, configurable via `GEMINI_MODEL`) via the Gemini `generateContent` REST API.
- **Scope of AI:** Natural-language extraction of refund proposals and risk/suspicion explanation from untrusted customer emails.
- **Deterministic Guardrails:** Strict Pydantic schema validation, deterministic prompt-injection detection, fail-closed outage handling, a restricted 3-tool MCP stdio boundary, and server-side deterministic policy enforcement (`ALLOW` / `ESCALATE` / `BLOCK`) with human merchant approval for escalations.
