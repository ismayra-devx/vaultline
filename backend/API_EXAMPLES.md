# Vaultline API Examples

This document provides request and response JSON examples for every API endpoint in Vaultline.

---

## 1. System Health

### `GET /health`
Checks server liveness.

**Request:**
```http
GET /health HTTP/1.1
Host: localhost:8000
```

**Response (200 OK):**
```json
{
  "status": "ok"
}
```

---

## 2. Agent Endpoints

Authentication: `Authorization: Bearer <agent_api_key>` or `X-Agent-Key: <agent_api_key>`.

### `POST /api/agent/refund-requests`

#### Example A: Auto-Approved (`ALLOW` <= $10.00)
**Request:**
```http
POST /api/agent/refund-requests HTTP/1.1
Host: localhost:8000
Authorization: Bearer vl_agent_secret_key_abc123
Content-Type: application/json

{
  "capture_id": "CAP_A",
  "amount": "8.50",
  "currency": "USD",
  "action": "refund",
  "ai_risk_flag": false,
  "ai_risk_explanation": null
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_8f1c841a0293da829c71",
  "decision": "ALLOW",
  "reasons": [
    "Amount is <= $10.00 with all checks passing."
  ],
  "risk_summary": "Low risk auto-approved refund.",
  "approval_required": false,
  "expires_at": null,
  "execution_status": "COMPLETED",
  "paypal_refund_id": "REF_4FA31BC0D6211849",
  "adapter_call_count": 1
}
```

#### Example B: Escalated for Merchant Approval (`ESCALATE` > $10.00 and <= $25.00)
**Request:**
```http
POST /api/agent/refund-requests HTTP/1.1
Host: localhost:8000
Authorization: Bearer vl_agent_secret_key_abc123
Content-Type: application/json

{
  "capture_id": "CAP_B",
  "amount": "20.00",
  "currency": "USD",
  "action": "refund",
  "ai_risk_flag": false,
  "ai_risk_explanation": null
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_99b0c29410ea8f1d82ba",
  "decision": "ESCALATE",
  "reasons": [
    "Amount is between $10.01 and $25.00; requires merchant approval."
  ],
  "risk_summary": "Escalated for human judge approval due to amount threshold.",
  "approval_required": true,
  "expires_at": "2026-11-09T18:05:00.000000+00:00",
  "execution_status": null,
  "paypal_refund_id": null,
  "adapter_call_count": 0
}
```

#### Example C: Hard Blocked (`BLOCK` > $25.00)
**Request:**
```http
POST /api/agent/refund-requests HTTP/1.1
Host: localhost:8000
Authorization: Bearer vl_agent_secret_key_abc123
Content-Type: application/json

{
  "capture_id": "CAP_C",
  "amount": "250.00",
  "currency": "USD",
  "action": "refund",
  "ai_risk_flag": false
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_11a84fbe3d92750c1840",
  "decision": "BLOCK",
  "reasons": [
    "Amount $250.00 exceeds maximum allowed policy limit ($25.00)."
  ],
  "risk_summary": "Request blocked by policy: Amount $250.00 exceeds maximum allowed policy limit ($25.00).",
  "approval_required": false,
  "expires_at": null,
  "execution_status": null,
  "paypal_refund_id": null,
  "adapter_call_count": 0
}
```

### `GET /api/agent/requests/{id}`
Fetch status of an agent's own previous request.

**Request:**
```http
GET /api/agent/requests/req_8f1c841a0293da829c71 HTTP/1.1
Host: localhost:8000
Authorization: Bearer vl_agent_secret_key_abc123
```

**Response (200 OK):**
```json
{
  "request_id": "req_8f1c841a0293da829c71",
  "decision": "ALLOW",
  "reasons": [
    "Amount is <= $10.00 with all checks passing."
  ],
  "risk_summary": "Low risk auto-approved refund.",
  "approval_required": false,
  "expires_at": null,
  "execution_status": "COMPLETED",
  "paypal_refund_id": "REF_4FA31BC0D6211849",
  "adapter_call_count": 1
}
```

### `GET /api/agent/policy`
Fetch active policy parameters for the agent's merchant.

**Request:**
```http
GET /api/agent/policy HTTP/1.1
Host: localhost:8000
Authorization: Bearer vl_agent_secret_key_abc123
```

**Response (200 OK):**
```json
{
  "version": "v1",
  "max_auto_amount": "10.00",
  "max_escalate_amount": "25.00",
  "currency": "USD"
}
```

---

## 3. Merchant Endpoints

Authentication: `Authorization: Bearer <session_token>` or `X-Session-Token: <session_token>`.

### `POST /api/merchant/login`
Authenticate merchant judge user and receive session token.

**Request:**
```http
POST /api/merchant/login HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "username": "judge_merchant",
  "password": "vaultline_judge_password_123!"
}
```

**Response (200 OK):**
```json
{
  "session_token": "sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt",
  "merchant_id": "merch_default",
  "username": "judge_merchant",
  "role": "judge",
  "expires_at": "2026-11-09T21:48:00.000000+00:00"
}
```

### `GET /api/merchant/requests`
List all refund requests submitted for this merchant.

**Request:**
```http
GET /api/merchant/requests HTTP/1.1
Host: localhost:8000
Authorization: Bearer sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
```

**Response (200 OK):**
```json
[
  {
    "request_id": "req_99b0c29410ea8f1d82ba",
    "decision": "ESCALATE",
    "reasons": [
      "Amount is between $10.01 and $25.00; requires merchant approval."
    ],
    "risk_summary": "Escalated for human judge approval due to amount threshold.",
    "approval_required": true,
    "expires_at": "2026-11-09T18:05:00.000000+00:00",
    "execution_status": null,
    "paypal_refund_id": null,
    "adapter_call_count": 0
  }
]
```

### `POST /api/merchant/requests/{id}/approve`
Judge approves an escalated request, triggering money movement.

**Request:**
```http
POST /api/merchant/requests/req_99b0c29410ea8f1d82ba/approve HTTP/1.1
Host: localhost:8000
Authorization: Bearer sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
Content-Type: application/json

{
  "reason": "Customer verified return receipt with customer support"
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_99b0c29410ea8f1d82ba",
  "decision": "ALLOW",
  "reasons": [
    "Manually approved by merchant judge."
  ],
  "risk_summary": "Approved by human merchant reviewer.",
  "approval_required": false,
  "expires_at": "2026-11-09T18:05:00.000000+00:00",
  "execution_status": "COMPLETED",
  "paypal_refund_id": "REF_9E1430F8721BA532",
  "adapter_call_count": 1
}
```

### `POST /api/merchant/requests/{id}/deny`
Judge denies an escalated request.

**Request:**
```http
POST /api/merchant/requests/req_99b0c29410ea8f1d82ba/deny HTTP/1.1
Host: localhost:8000
Authorization: Bearer sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
Content-Type: application/json

{
  "reason": "Suspicious chargeback pattern detected"
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_99b0c29410ea8f1d82ba",
  "decision": "DENIED",
  "reasons": [
    "Suspicious chargeback pattern detected"
  ],
  "risk_summary": "Refund denied by merchant judge.",
  "approval_required": false,
  "expires_at": null,
  "execution_status": null,
  "paypal_refund_id": null,
  "adapter_call_count": 0
}
```

### `GET /api/merchant/audit`
Retrieve the tamper-evident audit log with SHA-256 cryptographic chain verification.

**Request:**
```http
GET /api/merchant/audit HTTP/1.1
Host: localhost:8000
Authorization: Bearer sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
```

**Response (200 OK):**
```json
{
  "is_valid": true,
  "message": "Audit chain is valid and verified successfully (tamper-evident integrity intact).",
  "total_events": 3,
  "events": [
    {
      "id": 1,
      "prev_hash": "GENESIS",
      "event_type": "DECISION",
      "request_id": "req_8f1c841a0293da829c71",
      "merchant_id": "merch_default",
      "actor_type": "agent",
      "actor_id": "agent_default",
      "payload": "{\"amount\":\"8.50\",\"approval_required\":false,\"capture_id\":\"CAP_A\",\"currency\":\"USD\",\"decision\":\"ALLOW\",\"reasons\":[\"Amount is <= $10.00 with all checks passing.\"]}",
      "timestamp": "2026-11-09T17:48:00.123456+00:00",
      "hash": "07c393bc651b75294cf72d4263dbf15df9eb0505bb3b2cc3ecb8b54930113f38"
    },
    {
      "id": 2,
      "prev_hash": "07c393bc651b75294cf72d4263dbf15df9eb0505bb3b2cc3ecb8b54930113f38",
      "event_type": "EXECUTION",
      "request_id": "req_8f1c841a0293da829c71",
      "merchant_id": "merch_default",
      "actor_type": "agent",
      "actor_id": "agent_default",
      "payload": "{\"amount\":\"8.50\",\"error\":null,\"paypal_refund_id\":\"REF_4FA31BC0D6211849\",\"paypal_request_id\":\"pp_req_req_8f1c841a0293da829c71\",\"status\":\"COMPLETED\"}",
      "timestamp": "2026-11-09T17:48:00.234567+00:00",
      "hash": "a4d7d6f5f9024f0c4391ea973b02868ff1a70ea39c5950e932906b3bcba0a87f"
    }
  ]
}
```

---

## 4. v2.0 Architecture Endpoints (`/api/v1`)

Aligned with **Vaultline Technical Architecture & Interface Decisions v2.0** for P1 (PayPal Adapter) and P3+P4 (MCP and Dashboard).

### `POST /api/v1/refunds`
Agent submits refund request with idempotency support via `client_request_id`.

**Request:**
```http
POST /api/v1/refunds HTTP/1.1
Host: localhost:8000
X-Agent-Key: vl_agent_secret_key_abc123
Content-Type: application/json

{
  "capture_id": "CAP_A",
  "amount": "5.00",
  "currency": "USD",
  "reason": "Customer returned product",
  "client_request_id": "client_uuid_123"
}
```

**Response (200 OK):**
```json
{
  "request_id": "req_123abc456",
  "decision": "ALLOW",
  "status": "COMPLETED",
  "execution_status": "COMPLETED",
  "amount": "5.00",
  "currency": "USD",
  "refund_id": "REF_9A12BC44",
  "approval_id": null,
  "reason": "Customer returned product",
  "created_at": "2026-11-09T18:00:00Z",
  "client_request_id": "client_uuid_123",
  "reasons": [
    "Amount is <= $10.00 with all checks passing."
  ],
  "risk_summary": "Low risk auto-approved refund.",
  "approval_required": false,
  "expires_at": null,
  "paypal_refund_id": "REF_9A12BC44",
  "adapter_call_count": 1
}
```

### `GET /api/v1/dashboard/summary`
Live aggregated statistics for the merchant dashboard.

**Request:**
```http
GET /api/v1/dashboard/summary HTTP/1.1
Host: localhost:8000
X-Session-Token: sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
```

**Response (200 OK):**
```json
{
  "total_requests": 25,
  "allowed": 15,
  "blocked": 6,
  "escalated": 4,
  "pending_approvals": 2,
  "completed_refunds": 16,
  "failed_refunds": 0,
  "unknown_refunds": 0
}
```

### `GET /api/v1/dashboard/refunds`
Paginated refund requests for dashboard display.

**Request:**
```http
GET /api/v1/dashboard/refunds?limit=50&offset=0 HTTP/1.1
Host: localhost:8000
X-Session-Token: sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
```

**Response (200 OK):**
```json
{
  "items": [
    {
      "request_id": "req_123abc456",
      "agent_id": "agent_default",
      "capture_id": "CAP_A",
      "amount": "20.00",
      "currency": "USD",
      "decision": "ESCALATE",
      "status": "AWAITING_APPROVAL",
      "execution_status": "NOT_STARTED",
      "risk_level": "MEDIUM",
      "created_at": "2026-11-09T18:00:00Z"
    }
  ],
  "total": 1
}
```

### `GET /api/v1/dashboard/risk`
Aggregated risk breakdown for dashboard AI analyst and charts.

**Request:**
```http
GET /api/v1/dashboard/risk HTTP/1.1
Host: localhost:8000
X-Session-Token: sess_V8_Yl-kU2w_b79D4sO_aP1K2jP3wE9Rt
```

**Response (200 OK):**
```json
{
  "high_risk_count": 6,
  "medium_risk_count": 4,
  "low_risk_count": 15,
  "total_flagged_ai_risk": 0,
  "policy_violations": {
    "Amount $250.00 exceeds hard maximum policy bound $25.00.": 6
  }
}
```


