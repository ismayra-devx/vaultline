from typing import List, Optional
from fastapi import APIRouter, Depends, Request
from backend.schemas import (
    MerchantLoginRequest,
    MerchantLoginResponse,
    ApproveDenyRequest,
    RefundResponse,
    AuditResponse,
)
from backend.auth import require_merchant_auth, MerchantSession

router = APIRouter(prefix="/api/merchant", tags=["Merchant"])

@router.post("/login", response_model=MerchantLoginResponse)
def merchant_login(
    request: Request,
    payload: MerchantLoginRequest,
) -> MerchantLoginResponse:
    """Merchant logs in to receive an authenticated session token."""
    service = request.app.state.service
    return service.merchant_login(username=payload.username, password=payload.password)

@router.get("/requests", response_model=List[RefundResponse])
def get_merchant_requests(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> List[RefundResponse]:
    """List all action requests for the merchant."""
    service = request.app.state.service
    return service.get_merchant_requests(merchant_id=session.merchant_id)

@router.post("/requests/{request_id}/approve", response_model=RefundResponse)
def approve_refund_request(
    request_id: str,
    request: Request,
    payload: Optional[ApproveDenyRequest] = None,
    session: MerchantSession = Depends(require_merchant_auth),
) -> RefundResponse:
    """Judge reviews and approves an escalated refund request."""
    service = request.app.state.service
    reason = payload.reason if payload else None
    return service.approve_request(
        merchant_id=session.merchant_id,
        user_id=session.user_id,
        user_role=session.role,
        request_id=request_id,
        reason=reason,
    )

@router.post("/requests/{request_id}/deny", response_model=RefundResponse)
def deny_refund_request(
    request_id: str,
    request: Request,
    payload: Optional[ApproveDenyRequest] = None,
    session: MerchantSession = Depends(require_merchant_auth),
) -> RefundResponse:
    """Judge reviews and denies an escalated refund request."""
    service = request.app.state.service
    reason = payload.reason if payload else None
    return service.deny_request(
        merchant_id=session.merchant_id,
        user_id=session.user_id,
        user_role=session.role,
        request_id=request_id,
        reason=reason,
    )

@router.get("/audit", response_model=AuditResponse)
def get_merchant_audit_trail(
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> AuditResponse:
    """Fetch the tamper-evident audit trail and verification status."""
    service = request.app.state.service
    return service.get_merchant_audit(merchant_id=session.merchant_id)

