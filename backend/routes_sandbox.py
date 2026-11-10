"""Merchant-only endpoint to register a real PayPal Sandbox capture as refundable.

Agents can never call this (merchant session + judge/admin role required). The capture is
verified with PayPal before it is recorded, and the action is written to the audit chain.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from backend.audit import record_audit_event
from backend.auth import MerchantSession, require_judge_role, require_merchant_auth
from backend.fake_adapter import FakePaypalAdapter
from backend.paypal import PayPalError, register_capture

router = APIRouter(prefix="/api/v1/captures", tags=["v1-Merchant"])


class RegisterCaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capture_id: str = Field(pattern=r"^[A-Za-z0-9-]{1,64}$")


class RegisterCaptureResponse(BaseModel):
    capture_id: str
    status: str
    amount: str
    registered: bool


@router.post("/register", response_model=RegisterCaptureResponse)
def register_sandbox_capture(
    body: RegisterCaptureRequest,
    request: Request,
    session: MerchantSession = Depends(require_merchant_auth),
) -> RegisterCaptureResponse:
    require_judge_role(session)
    adapter = request.app.state.adapter
    if isinstance(adapter, FakePaypalAdapter):
        raise HTTPException(status.HTTP_409_CONFLICT, "Capture registration requires VAULTLINE_PAYPAL_MODE=sandbox.")
    conn = request.app.state.db.get_connection()
    try:
        result = register_capture(conn, adapter, session.merchant_id, body.capture_id)
    except PayPalError as exc:
        code = status.HTTP_404_NOT_FOUND if getattr(exc, "status", None) == 404 else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, f"PayPal could not verify this capture: {exc}") from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    except TimeoutError:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, "PayPal did not respond. Try again.") from None
    if result["registered"]:
        record_audit_event(
            conn=conn,
            event_type="CAPTURE_REGISTERED",
            actor_type="merchant_user",
            actor_id=session.user_id,
            merchant_id=session.merchant_id,
            payload={"capture_id": result["capture_id"], "amount": result["amount"], "currency": "USD"},
        )
        conn.commit()
    return RegisterCaptureResponse(**result)
