
# Vaultline API Contract

Version: 0.1 — Initial integration contract

## 1. Purpose

Define the shared interfaces between the FastAPI security backend, PayPal adapter, MCP server and merchant dashboard.

P2 owns authorization decisions. P1 owns PayPal execution. P3 owns MCP integration. P4 owns presentation and merchant-facing approval UX.

## 2. Core Refund Request

The agent submits a refund request through the MCP tool `request_refund`.

Example request:

```json
{
  "capture_id": "SANDBOX_CAPTURE_ID",
  "amount": "5.00",
  "currency": "USD",
  "reason": "Customer requested refund"
}
```

Identity must be derived from the authenticated agent credential, not trusted from request-body fields.

## 3. Authorization Decisions

The backend returns one of:

- ALLOW
- BLOCK
- ESCALATE

Policy:

| Amount | Decision |
|---|---|
| Up to $10 | ALLOW |
| Above $10 through $25 | ESCALATE |
| Above $25 | BLOCK |

Hard security rules always take precedence.

AI risk assessment may tighten a decision but must never turn BLOCK into ALLOW.

## 4. Decision Response

Illustrative response:

```json
{
  "request_id": "req_example",
  "decision": "ALLOW",
  "reason_codes": [],
  "policy_version": "1",
  "execution_status": "NOT_STARTED"
}
```

The backend must distinguish authorization decisions from PayPal execution outcomes.

## 5. Approval Workflow

For ESCALATE:

1. Create a pending approval record.
2. Bind approval to the exact transaction payload.
3. Include merchant, agent, capture, amount, currency, policy version, expiry and one-time nonce.
4. Merchant approves or denies through authenticated backend endpoints.
5. Revalidate the exact payload and policy immediately before execution.
6. Reject expired, replayed or modified approvals.

Agents must never receive approval or denial permissions.

## 6. PayPal Adapter Handoff

P2 authorizes the refund and passes an approved execution instruction to P1's PayPal adapter.

The adapter must:

- Use server-side PayPal credentials.
- Restrict requests to PayPal Sandbox during the MVP.
- Use USD and decimal-string money values.
- Use a stable PayPal-Request-Id for each logical refund.
- Persist execution intent before making the money-moving request.
- Inspect the returned refund resource status.
- Never treat an ambiguous timeout as a successful refund.

P1 owns PayPal API execution. P2 owns authorization and approval validation.

## 7. Execution Statuses

Use explicit statuses:

- NOT_STARTED
- PENDING
- COMPLETED
- FAILED
- UNKNOWN

A PayPal HTTP 2xx response alone does not prove the refund completed. Inspect the actual refund resource status.

## 8. MCP Tools

Exactly three agent-facing tools:

1. `request_refund`
2. `get_request_status`
3. `get_agent_policy`

No agent-facing approve, deny, raw PayPal or arbitrary HTTP tools.

## 9. Ownership

| Module | Owner |
|---|---|
| PayPal adapter | P1 |
| FastAPI security and policy | P2 |
| MCP server and agent | P3 |
| React dashboard and AG Studio | P4 |

## 10. Contract Change Process

- Discuss breaking changes with affected owners.
- Update this document before implementing a breaking change.
- Use pull requests for changes to main.
- Keep request and response examples synchronized with implementation.
- Record unresolved assumptions as open questions.

## 11. Open Questions

- Final REST route paths and HTTP methods.
- Exact agent authentication mechanism.
- Exact merchant session mechanism.
- Database response schemas and audit-event fields.
- PayPal adapter function signature.
- Approval endpoint response schemas.

These details must be agreed by the relevant owners before integration.
