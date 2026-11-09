from decimal import Decimal
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, ConfigDict, field_validator, field_serializer

class StrictBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class RefundRequestCreate(StrictBaseModel):
    capture_id: str
    amount: Decimal
    currency: str = "USD"
    action: str = "refund"
    ai_risk_flag: bool = False
    ai_risk_explanation: Optional[str] = None
    reason: Optional[str] = None
    client_request_id: Optional[str] = None

    @field_validator("amount", mode="before")
    @classmethod
    def validate_amount_no_float(cls, v: Any) -> Decimal:
        if isinstance(v, bool):
            raise ValueError("Boolean values are strictly forbidden for money.")
        if isinstance(v, float):
            raise ValueError("Float values are strictly forbidden for money. Use Decimal strings.")
        if isinstance(v, (int, str)):
            s = str(v).strip()
            if not s:
                raise ValueError("Amount cannot be empty.")
            # Check for scientific notation, NaN, Infinity
            if any(token in s.lower() for token in ("e", "nan", "inf")):
                raise ValueError("Scientific notation, NaN, and Infinity are strictly forbidden.")
            try:
                d = Decimal(s)
            except Exception:
                raise ValueError("Invalid decimal string for amount.")
            if d.is_nan() or d.is_infinite():
                raise ValueError("Amount must be a finite decimal number.")
            # Disallow more than 2 decimal places for USD
            if d.as_tuple().exponent < -2:
                raise ValueError("Amount cannot have more than 2 decimal places.")
            return d
        if isinstance(v, Decimal):
            if v.is_nan() or v.is_infinite():
                raise ValueError("Amount must be a finite decimal number.")
            if v.as_tuple().exponent < -2:
                raise ValueError("Amount cannot have more than 2 decimal places.")
            return v
        raise ValueError("Amount must be a decimal string or Decimal.")

    @field_serializer("amount")
    def serialize_amount(self, v: Decimal) -> str:
        return str(v)

class RefundResponse(BaseModel):
    request_id: str
    decision: str
    reasons: List[str]
    risk_summary: str
    approval_required: bool
    expires_at: Optional[str] = None
    execution_status: Optional[str] = None
    paypal_refund_id: Optional[str] = None
    adapter_call_count: int = 0
    # Additional fields aligned with v2.0 API contract
    status: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = "USD"
    refund_id: Optional[str] = None
    approval_id: Optional[str] = None
    created_at: Optional[str] = None
    reason: Optional[str] = None
    client_request_id: Optional[str] = None

class DashboardSummaryResponse(BaseModel):
    total_requests: int
    allowed: int
    blocked: int
    escalated: int
    pending_approvals: int
    completed_refunds: int
    failed_refunds: int
    unknown_refunds: int

class DashboardRefundItem(BaseModel):
    request_id: str
    agent_id: str
    capture_id: str
    amount: str
    currency: str
    decision: str
    status: str
    execution_status: str
    risk_level: str
    created_at: str

class DashboardRefundListResponse(BaseModel):
    items: List[DashboardRefundItem]
    total: int

class DashboardRiskSummary(BaseModel):
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    total_flagged_ai_risk: int
    policy_violations: Dict[str, int]

class MerchantLoginRequest(StrictBaseModel):
    username: str
    password: str

class MerchantLoginResponse(BaseModel):
    session_token: str
    merchant_id: str
    username: str
    role: str
    expires_at: str

class ApproveDenyRequest(StrictBaseModel):
    reason: Optional[str] = None

class PolicyResponse(BaseModel):
    version: str
    max_auto_amount: str
    max_escalate_amount: str
    currency: str

class AuditEventItem(BaseModel):
    id: int
    prev_hash: str
    event_type: str
    request_id: Optional[str] = None
    merchant_id: Optional[str] = None
    actor_type: str
    actor_id: Optional[str] = None
    payload: str
    timestamp: str
    hash: str

class AuditResponse(BaseModel):
    is_valid: bool
    message: str
    total_events: int
    events: List[AuditEventItem]

