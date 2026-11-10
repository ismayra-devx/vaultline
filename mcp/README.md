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

## Hosted mode (HTTPS, used on Render)

Set `VAULTLINE_DEPLOYMENT=hosted`. The server then speaks **streamable HTTP** at `/mcp` and enforces these rules:

- **Per-agent keys:** no agent key is configured on the server. Each agent sends `Authorization: Bearer <its key>`, and that exact key is forwarded to the backend, which validates it. Setting `VAULTLINE_AGENT_KEY` in http mode is refused, so one shared key can't impersonate every agent.
- **Bearer required:** requests to `/mcp` without a bearer get **401** before reaching any tool. `/health` is public.
- **No localhost backend:** `VAULTLINE_API_BASE_URL` must be the public **https** backend URL. Localhost, `127.*`, `0.0.0.0` and plain http are refused.
- **Host check:** DNS-rebinding protection only accepts the service's own hostname (`RENDER_EXTERNAL_HOSTNAME` or `VAULTLINE_MCP_ALLOWED_HOSTS`).

Full setup and client configs for VS Code, Cursor and MCP Inspector are in [../docs/DEPLOY.md](../docs/DEPLOY.md).

## Run locally (stdio, development)

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
pytest -q                                  # 57 tests (stdio + hosted HTTP), mocked backend, no network
```

Integration tests launch the real stdio server process against a running backend:

```bash
# terminal 1 — backend from the security-backend branch (fake PayPal adapter by default)
uvicorn backend.main:app --port 8000
# terminal 2
cd mcp
VAULTLINE_TEST_API_BASE_URL=http://localhost:8000 \
VAULTLINE_TEST_AGENT_KEY=<seeded agent key> pytest -q   # 65 passed
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
  tests/test_unit.py               mocked-backend tests
  tests/test_integration.py        stdio server -> real backend
```
