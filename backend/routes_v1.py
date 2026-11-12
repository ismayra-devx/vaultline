from typing import List, Optional
from fastapi import APIRouter, Depends, Request, Query
from backend.schemas import (
    RefundRequestCreate,
    RefundResponse,
    PolicyResponse,
    MerchantLoginRequest,
    MerchantLoginResponse,
    ApproveDenyRequest,
    AuditResponse,
    DashboardSummaryResponse,
    DashboardRefundListResponse,
    DashboardRiskSummary,
)
from backend.auth import (
    require_agent_auth,
    AgentIdentity,
    require_merchant_auth,
    MerchantSession,
)

router = APIRouter(prefix="/api/v1", tags=["v1"])

# ==========================================
# Agent Endpoints (v2.0 Interface B / C)
# ==========================================

@router.post("/refunds", response_model=RefundResponse, tags=["v1-Agent"])
def submit_refund(
    request: Request,
    payload: RefundRequestCreate,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> RefundResponse:
    """Submit a refund request for deterministic authorization."""
    service = request.app.state.service
    return service.create_refund_request(
        agent_id=agent.agent_id,
        capture_id=payload.capture_id,
        amount=payload.amount,
        currency=payload.currency,
        action=payload.action,
        ai_risk_flag=payload.ai_risk_flag,
        ai_risk_explanation=payload.ai_risk_explanation,
        reason=payload.reason,
        client_request_id=payload.client_request_id,
    )

@router.get("/refunds/by-client-id/{client_request_id}", response_model=RefundResponse, tags=["v1-Agent"])
def get_refund_status_by_client_id(
    client_request_id: str,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> RefundResponse:
    """Read-only lookup of own request status by client_request_id."""
    service = request.app.state.service
    return service.get_agent_request_by_client_id(
        agent_id=agent.agent_id,
        client_request_id=client_request_id,
    )

@router.get("/refunds/{request_id}", response_model=RefundResponse, tags=["v1-Agent"])
def get_refund_status(
    request_id: str,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> RefundResponse:
    """Retrieve own request status and authorization outcome."""
    service = request.app.state.service
    return service.get_agent_request(agent_id=agent.agent_id, request_id=request_id)

@router.get("/agents/me/policy", response_model=PolicyResponse, tags=["v1-Agent"])
def get_my_policy(
    request: Request,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> PolicyResponse:
    """Retrieve policy limits applicable to the authenticated agent."""
    service = request.app.state.service
    return service.get_agent_policy(agent_id=agent.agent_id)

# ==========================================
# Merchant Approval Endpoints (v2.0 Interface B / D)
# ==========================================

@router.get("/approvals", response_model=List[RefundResponse], tags=["v1-Merchant"])
def list_pending_approvals(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> List[RefundResponse]:
    """List pending escalated requests awaiting judge approval."""
    service = request.app.state.service
    return service.get_pending_approvals(merchant_id=session.merchant_id)

@router.post("/approvals/{approval_id}/approve", response_model=RefundResponse, tags=["v1-Merchant"])
def approve_request(
    approval_id: str,
    request: Request,
    payload: Optional[ApproveDenyRequest] = None,
    session: MerchantSession = Depends(require_merchant_auth),
) -> RefundResponse:
    """Judge reviews and approves an escalated request."""
    service = request.app.state.service
    conn = request.app.state.db.get_connection()
    appr_row = conn.execute("SELECT request_id FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    req_id = appr_row["request_id"] if appr_row else approval_id
    reason = payload.reason if payload else None
    return service.approve_request(
        merchant_id=session.merchant_id,
        user_id=session.user_id,
        user_role=session.role,
        request_id=req_id,
        reason=reason,
    )

@router.post("/approvals/{approval_id}/deny", response_model=RefundResponse, tags=["v1-Merchant"])
def deny_request(
    approval_id: str,
    request: Request,
    payload: Optional[ApproveDenyRequest] = None,
    session: MerchantSession = Depends(require_merchant_auth),
) -> RefundResponse:
    """Judge reviews and denies an escalated request."""
    service = request.app.state.service
    conn = request.app.state.db.get_connection()
    appr_row = conn.execute("SELECT request_id FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    req_id = appr_row["request_id"] if appr_row else approval_id
    reason = payload.reason if payload else None
    return service.deny_request(
        merchant_id=session.merchant_id,
        user_id=session.user_id,
        user_role=session.role,
        request_id=req_id,
        reason=reason,
    )

# ==========================================
# Dashboard & Audit Endpoints (v2.0 Interface D)
# ==========================================

@router.get("/dashboard/summary", response_model=DashboardSummaryResponse, tags=["v1-Dashboard"])
def get_dashboard_summary(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> DashboardSummaryResponse:
    """Fetch live aggregated dashboard summary stats."""
    service = request.app.state.service
    return service.get_dashboard_summary(merchant_id=session.merchant_id)

@router.get("/dashboard/refunds", response_model=DashboardRefundListResponse, tags=["v1-Dashboard"])
def get_dashboard_refunds(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: MerchantSession = Depends(require_merchant_auth),
) -> DashboardRefundListResponse:
    """Fetch paginated list of refund requests for merchant dashboard."""
    service = request.app.state.service
    return service.get_dashboard_refunds(merchant_id=session.merchant_id, limit=limit, offset=offset)

@router.get("/dashboard/risk", response_model=DashboardRiskSummary, tags=["v1-Dashboard"])
def get_dashboard_risk(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> DashboardRiskSummary:
    """Fetch aggregated risk signals and violations for dashboard."""
    service = request.app.state.service
    return service.get_dashboard_risk(merchant_id=session.merchant_id)

@router.get("/audit", response_model=AuditResponse, tags=["v1-Dashboard"])
def get_audit_trail(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> AuditResponse:
    """Fetch the tamper-evident audit history and cryptographic verification."""
    service = request.app.state.service
    return service.get_merchant_audit(merchant_id=session.merchant_id)

@router.post("/auth/login", response_model=MerchantLoginResponse, tags=["v1-Auth"])
def merchant_login(
    request: Request,
    payload: MerchantLoginRequest,
) -> MerchantLoginResponse:
    """Merchant logs in to receive an authenticated session token."""
    service = request.app.state.service
    return service.merchant_login(username=payload.username, password=payload.password)

