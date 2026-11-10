# Vaultline — Technical Architecture & Interface Decisions

**Project:** Vaultline — AI Financial Security Infrastructure  
**Tagline:** Every agent has a limit. Every transaction needs authority.  
**Version:** 2.0 — MVP Synchronous Contract Frozen Baseline  
**Date:** 10 November 2026  
**Team:** Ismayra (P1), Ankit (P2), Mohd (P3 + P4)  
**Status:** Approved Technical Contract  
**Architecture:** Python Modular Monolith · FastAPI · MCP · PayPal Payments API v2 (Sandbox) · SQLite WAL · React · AG Studio

---

## 1. Executive Summary & Security Invariants

Vaultline is a financial authorization gateway that allows autonomous AI agents to propose financial operations (specifically refunds) without granting them direct authority over money or access to payment credentials.

### Core Security Model
> **The agent proposes. Vaultline authorizes. The merchant approves when required. The execution service executes. PayPal confirms. The audit system records.**

No component is permitted to bypass this sequence.

### Core Architectural Decisions:
1. **Decimal-Only Currency**: Float types are forbidden in all models and database columns.
2. **Deterministic Security Gateway**: Policy checks live strictly in `policy.py` and `services.py`.
3. **Bound Merchant Approvals**: Multi-field cryptographic binding with atomic single-use nonce consumption.
4. **Idempotency & Pre-Persistence**: Execution intent (`executions` row with `PENDING` status and `paypal_request_id`) is committed to the database **before** calling the external PayPal adapter.
5. **Fail-Closed Execution**: If `VAULTLINE_USE_REAL_PAYPAL=true`, the system fails startup immediately if the real adapter cannot be imported or configured; it never silently falls back to a test double.
6. **Resource Lifecycle**: Connections are cleanly closed on application shutdown via FastAPI's `lifespan` handler.

---

## 2. PayPal Adapter Interface (Frozen Synchronous Contract)

### 2.1 Interface Definition
The canonical interface is the synchronous abstract base class `PaypalAdapter` defined in [`backend/adapter_interface.py`](file:///e:/pyfol/backend/adapter_interface.py):

```python
from abc import ABC, abstractmethod
from decimal import Decimal
from backend.adapter_interface import CaptureResult, RefundResult

class PaypalAdapter(ABC):
    """
    Canonical abstract base class for all PayPal communication in Vaultline.
    Synchronous execution contract for services.py, production adapter, and test doubles.
    """

    @abstractmethod
    def get_capture(self, capture_id: str) -> CaptureResult:
        """Fetches capture details from PayPal."""
        pass

    @abstractmethod
    def refund_capture(
        self,
        paypal_request_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
        **kwargs,
    ) -> RefundResult:
        """
        Executes a capture refund using an idempotent paypal_request_id.
        Returns normalized RefundResult.
        """
        pass

    @abstractmethod
    def get_refund(self, refund_id: str) -> RefundResult:
        """Fetches status of an existing refund."""
        pass

    def close(self) -> None:
        """Closes any underlying network connections or HTTP clients. Default no-op."""
        pass
```

### 2.2 Contract Alignment Note (MVP Freeze)
> [!IMPORTANT]
> Earlier architectural drafts proposed an asynchronous `PayPalAdapter(Protocol)`.
> For the MVP, the **synchronous `PaypalAdapter(ABC)` contract is frozen as canonical**.
>
> **Rationale:**
> - Vaultline's FastAPI endpoints execute CPU/DB-bound authorization logic and run synchronous service calls within worker threadpools.
> - Using synchronous `httpx.Client` inside the adapter avoids dual async/sync loop bridging issues, maintains strict atomicity with SQLite WAL transactions, and prevents competing contracts.
> - `PayPalAdapter` is retained as a backward-compatible alias to `PaypalAdapter`.

### 2.3 Normalized Data Models
Raw PayPal JSON is never leaked through the security backend:

```python
@dataclass(frozen=True)
class CaptureResult:
    capture_id: str
    status: str
    amount: Decimal
    currency: str

@dataclass(frozen=True)
class RefundResult:
    refund_id: str
    capture_id: Optional[str]
    status: str
    amount: Decimal
    currency: str
    paypal_debug_id: Optional[str] = None
```

### 2.4 Error Handling & Timeout Mapping
1. **Definite Failures**: Network failures before sending, or HTTP 4xx errors (e.g. 400, 403, 404), raise adapter errors that map to `FAILED`. Money did not move.
2. **Ambiguous Outcomes**: Read timeouts, network drops after sending, or unexpected HTTP 5xx responses must raise an exception inheriting from Python's built-in `TimeoutError` (e.g. `PayPalOutcomeUnknown(TimeoutError)`).
3. **Gateway Isolation**: `VaultlineService` catches `builtins.TimeoutError` and records the execution as `UNKNOWN` (preserving `paypal_request_id`). It never marks an ambiguous outcome as `FAILED` or `COMPLETED`.
4. **Re-Read Verification**: A 2xx response from PayPal POST is not trusted on its own; the adapter re-verifies the refund resource state before reporting `COMPLETED`.

---

## 3. Component Ownership

| Component | Owner | Responsibilities | Forbidden Actions |
| :--- | :--- | :--- | :--- |
| **P1** | Ismayra | PayPal Sandbox adapter (`backend/paypal/`), OAuth token lifecycle, `PayPal-Request-Id` forwarding, typed error mapping, Sandbox host validation. | Must NOT evaluate merchant policy, approve transactions, or persist DB records. |
| **P2** | Ankit | FastAPI security gateway, policy engine, merchant approvals, DB & audit hash chain, idempotency & intent pre-persistence, v1/v2 REST endpoints. | Must NOT make direct external PayPal HTTP calls outside the adapter. |
| **P3 + P4**| Mohd | MCP server tools (`request_refund`, `get_request_status`, `get_agent_policy`), credential forwarding, React dashboard, AG Studio. | Must NOT bypass backend endpoints, execute raw refunds, or access SQLite directly. |

---

## 4. Lifecycle & Factory Configuration

### 4.1 Production Adapter Instantiation
In [`backend/main.py`](file:///e:/pyfol/backend/main.py):
- When `VAULTLINE_USE_REAL_PAYPAL=true`, the application calls `PayPalSandboxAdapter.from_env()`.
- If credentials or module imports fail, startup aborts immediately (**fail closed**).
- When `VAULTLINE_USE_REAL_PAYPAL=false`, the server defaults to `FakePaypalAdapter()` for development.
- In automated test suites, `create_app(adapter=test_double)` permits deterministic in-memory injection.

### 4.2 Resource Shutdown
On application shutdown, FastAPI's `lifespan` context manager invokes `adapter.close()`, ensuring all underlying HTTP client connection pools and file handles are released cleanly.

