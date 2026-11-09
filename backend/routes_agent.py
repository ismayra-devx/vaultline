from fastapi import APIRouter, Depends, Request
from backend.schemas import RefundRequestCreate, RefundResponse, PolicyResponse
from backend.auth import require_agent_auth, AgentIdentity

router = APIRouter(prefix="/api/agent", tags=["Agent"])

@router.post("/refund-requests", response_model=RefundResponse)
def create_refund_request(
    request: Request,
    payload: RefundRequestCreate,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> RefundResponse:
    """Agent submits a refund request for policy ladder evaluation."""
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

@router.get("/requests/{request_id}", response_model=RefundResponse)
def get_refund_request(
    request_id: str,
    request: Request,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> RefundResponse:
    """Fetch status of an agent's own request."""
    service = request.app.state.service
    return service.get_agent_request(agent_id=agent.agent_id, request_id=request_id)

@router.get("/policy", response_model=PolicyResponse)
def get_agent_policy(
    request: Request,
    agent: AgentIdentity = Depends(require_agent_auth),
) -> PolicyResponse:
    """Fetch current policy constraints for the agent's merchant."""
    service = request.app.state.service
    return service.get_agent_policy(agent_id=agent.agent_id)

