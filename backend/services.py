import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, status
import sqlite3

from backend.config import Config
from backend.db import Database
from backend.adapter_interface import PaypalAdapter
from backend.policy import evaluate_refund_policy, Decision
from backend.approvals import (
    create_approval_record,
    consume_and_verify_approval,
    ApprovalVerificationError,
    CrossMerchantAccessError,
    TamperDetectedError,
    ExpiredApprovalError,
    NonceAlreadyConsumedError,
)
from backend.audit import record_audit_event, verify_audit_chain
from backend.schemas import (
    RefundResponse,
    MerchantLoginResponse,
    PolicyResponse,
    AuditResponse,
    AuditEventItem,
    DashboardSummaryResponse,
    DashboardRefundItem,
    DashboardRefundListResponse,
    DashboardRiskSummary,
)
from backend.auth import (
    hash_password,
    verify_password,
    create_merchant_session,
    require_judge_role,
)

class VaultlineService:
    def __init__(self, db: Database, adapter: PaypalAdapter, config: Config):
        self.db = db
        self.adapter = adapter
        self.config = config

    def _build_refund_response(
        self,
        request_id: str,
        decision: str,
        reasons: List[str],
        risk_summary: str,
        approval_required: bool,
        expires_at: Optional[str] = None,
        execution_status: Optional[str] = None,
        paypal_refund_id: Optional[str] = None,
        adapter_call_count: int = 0,
        amount: Optional[Decimal | str] = None,
        currency: Optional[str] = "USD",
        approval_id: Optional[str] = None,
        created_at: Optional[str] = None,
        reason: Optional[str] = None,
        client_request_id: Optional[str] = None,
    ) -> RefundResponse:
        # Determine high-level request status aligned with Section 3.5
        if decision == "BLOCK":
            status_val = "BLOCKED"
            exec_status_val = execution_status or "NOT_STARTED"
        elif decision == "ESCALATE":
            if execution_status == "COMPLETED":
                status_val = "COMPLETED"
                exec_status_val = "COMPLETED"
            elif execution_status == "FAILED":
                status_val = "FAILED"
                exec_status_val = "FAILED"
            elif execution_status == "UNKNOWN":
                status_val = "UNKNOWN"
                exec_status_val = "UNKNOWN"
            elif execution_status == "DENIED":
                status_val = "DENIED"
                exec_status_val = "NOT_STARTED"
            else:
                status_val = "AWAITING_APPROVAL"
                exec_status_val = execution_status or "NOT_STARTED"
        elif decision == "ALLOW":
            if execution_status == "COMPLETED":
                status_val = "COMPLETED"
                exec_status_val = "COMPLETED"
            elif execution_status == "PENDING":
                status_val = "PENDING"
                exec_status_val = "PENDING"
            elif execution_status == "UNKNOWN":
                status_val = "UNKNOWN"
                exec_status_val = "UNKNOWN"
            elif execution_status == "FAILED":
                status_val = "FAILED"
                exec_status_val = "FAILED"
            else:
                status_val = "COMPLETED" if paypal_refund_id else "EXECUTING"
                exec_status_val = execution_status or "STARTED"
        elif decision == "DENIED":
            status_val = "DENIED"
            exec_status_val = "NOT_STARTED"
        else:
            status_val = decision
            exec_status_val = execution_status or "NOT_STARTED"

        primary_reason = reason or (reasons[0] if reasons else risk_summary)

        return RefundResponse(
            request_id=request_id,
            decision=decision,
            reasons=reasons,
            risk_summary=risk_summary,
            approval_required=approval_required,
            expires_at=expires_at,
            execution_status=exec_status_val,
            paypal_refund_id=paypal_refund_id,
            adapter_call_count=adapter_call_count,
            status=status_val,
            amount=str(amount) if amount is not None else None,
            currency=currency or "USD",
            refund_id=paypal_refund_id,
            approval_id=approval_id,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            reason=primary_reason,
            client_request_id=client_request_id,
        )

    def create_refund_request(
        self,
        agent_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str = "USD",
        action: str = "refund",
        ai_risk_flag: bool = False,
        ai_risk_explanation: Optional[str] = None,
        ai_signal_error: Optional[str] = None,
        reason: Optional[str] = None,
        client_request_id: Optional[str] = None,
    ) -> RefundResponse:
        conn = self.db.get_connection()

        # Idempotency check by client_request_id (Section 3.9 & 10.1)
        if client_request_id:
            cur = conn.execute(
                """
                SELECT ar.*, ex.status as exec_status, ex.paypal_refund_id, ex.adapter_call_count,
                       ap.id as appr_id
                FROM action_requests ar
                LEFT JOIN executions ex ON ar.id = ex.request_id
                LEFT JOIN approvals ap ON ar.id = ap.request_id
                WHERE ar.agent_id = ? AND ar.client_request_id = ?
                """,
                (agent_id, client_request_id),
            )
            existing = cur.fetchone()
            if existing:
                if (
                    existing["capture_id"] == capture_id
                    and Decimal(existing["amount"]) == amount
                    and existing["currency"] == currency
                ):
                    reasons = json.loads(existing["reasons"])
                    return self._build_refund_response(
                        request_id=existing["id"],
                        decision=existing["decision"],
                        reasons=reasons,
                        risk_summary=existing["risk_summary"],
                        approval_required=bool(existing["approval_required"]),
                        expires_at=existing["expires_at"],
                        execution_status=existing["exec_status"],
                        paypal_refund_id=existing["paypal_refund_id"],
                        adapter_call_count=existing["adapter_call_count"] or 0,
                        amount=existing["amount"],
                        currency=existing["currency"],
                        approval_id=existing["appr_id"],
                        created_at=existing["created_at"],
                        reason=existing["reason"],
                        client_request_id=client_request_id,
                    )
                else:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Idempotency conflict: client_request_id reused with different request payload.",
                    )

        # 1. Fetch agent record
        agent_cur = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent_row = agent_cur.fetchone()
        agent_dict = dict(agent_row) if agent_row else None
        merchant_id = agent_dict["merchant_id"] if agent_dict else self.config.merchant_id

        # 2. Fetch capture record
        cap_cur = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,))
        cap_row = cap_cur.fetchone()
        cap_dict = dict(cap_row) if cap_row else None

        # 3. Fetch active policy
        pol_cur = conn.execute(
            "SELECT * FROM policies WHERE merchant_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (merchant_id,),
        )
        pol_row = pol_cur.fetchone()
        if pol_row:
            policy_version = pol_row["version"]
            max_auto_amount = Decimal(str(pol_row["max_auto_amount"]))
            max_escalate_amount = Decimal(str(pol_row["max_escalate_amount"]))
        else:
            policy_version = "v1"
            max_auto_amount = self.config.max_auto_amount
            max_escalate_amount = self.config.max_escalate_amount

        # 4. Evaluate policy ladder
        eval_result = evaluate_refund_policy(
            agent=agent_dict,
            capture=cap_dict,
            amount=amount,
            currency=currency,
            action=action,
            max_auto_amount=max_auto_amount,
            max_escalate_amount=max_escalate_amount,
            ai_risk_flag=ai_risk_flag,
            ai_risk_explanation=ai_risk_explanation,
            ai_signal_error=ai_signal_error,
        )

        request_id = f"req_{secrets.token_hex(10)}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Store action request
        conn.execute(
            """
            INSERT INTO action_requests (
                id, merchant_id, agent_id, action, capture_id, amount,
                currency, ai_risk_flag, ai_risk_explanation, decision,
                reasons, risk_summary, approval_required, policy_version,
                status, reason, client_request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                merchant_id,
                agent_id,
                action,
                capture_id,
                str(amount),
                currency,
                1 if ai_risk_flag else 0,
                ai_risk_explanation,
                eval_result.decision.value,
                json.dumps(eval_result.reasons),
                eval_result.risk_summary,
                1 if eval_result.approval_required else 0,
                policy_version,
                "DECIDED",
                reason,
                client_request_id,
                now_iso,
            ),
        )
        conn.commit()

        # Record DECISION audit event
        record_audit_event(
            conn=conn,
            event_type="DECISION",
            actor_type="agent",
            actor_id=agent_id,
            request_id=request_id,
            merchant_id=merchant_id,
            payload={
                "decision": eval_result.decision.value,
                "amount": str(amount),
                "currency": currency,
                "capture_id": capture_id,
                "reasons": eval_result.reasons,
                "approval_required": eval_result.approval_required,
            },
        )

        # ALLOW: Auto-executes through the adapter immediately
        if eval_result.decision == Decision.ALLOW:
            exec_record = self._execute_refund(
                conn=conn,
                request_id=request_id,
                merchant_id=merchant_id,
                capture_id=capture_id,
                amount=amount,
                currency=currency,
                actor_type="agent",
                actor_id=agent_id,
            )
            return self._build_refund_response(
                request_id=request_id,
                decision=eval_result.decision.value,
                reasons=eval_result.reasons,
                risk_summary=eval_result.risk_summary,
                approval_required=False,
                expires_at=None,
                execution_status=exec_record["status"],
                paypal_refund_id=exec_record["paypal_refund_id"],
                adapter_call_count=exec_record["adapter_call_count"],
                amount=amount,
                currency=currency,
                created_at=now_iso,
                reason=reason,
                client_request_id=client_request_id,
            )

        # ESCALATE: Creates bound approval
        elif eval_result.decision == Decision.ESCALATE:
            appr_id = create_approval_record(
                conn=conn,
                request_id=request_id,
                merchant_id=merchant_id,
                agent_id=agent_id,
                action=action,
                capture_id=capture_id,
                amount=amount,
                currency=currency,
                policy_version=policy_version,
                expiry_seconds=self.config.approval_expiry_seconds,
            )

            # Fetch created approval for expiry timestamp
            appr_cur = conn.execute("SELECT expires_at FROM approvals WHERE request_id = ?", (request_id,))
            appr_row = appr_cur.fetchone()
            expires_at = appr_row[0] if appr_row else None

            # Update expires_at in action_requests
            conn.execute("UPDATE action_requests SET expires_at = ? WHERE id = ?", (expires_at, request_id))
            conn.commit()

            record_audit_event(
                conn=conn,
                event_type="APPROVAL_PENDING",
                actor_type="agent",
                actor_id=agent_id,
                request_id=request_id,
                merchant_id=merchant_id,
                payload={"expires_at": expires_at, "amount": str(amount), "capture_id": capture_id},
            )

            return self._build_refund_response(
                request_id=request_id,
                decision=eval_result.decision.value,
                reasons=eval_result.reasons,
                risk_summary=eval_result.risk_summary,
                approval_required=True,
                expires_at=expires_at,
                execution_status=None,
                paypal_refund_id=None,
                adapter_call_count=0,
                amount=amount,
                currency=currency,
                approval_id=appr_id,
                created_at=now_iso,
                reason=reason,
                client_request_id=client_request_id,
            )

        # BLOCK: Record REJECTION audit event
        else:
            record_audit_event(
                conn=conn,
                event_type="REJECTION",
                actor_type="system",
                actor_id="policy_engine",
                request_id=request_id,
                merchant_id=merchant_id,
                payload={"reasons": eval_result.reasons, "amount": str(amount), "capture_id": capture_id},
            )
            return self._build_refund_response(
                request_id=request_id,
                decision=eval_result.decision.value,
                reasons=eval_result.reasons,
                risk_summary=eval_result.risk_summary,
                approval_required=False,
                expires_at=None,
                execution_status=None,
                paypal_refund_id=None,
                adapter_call_count=0,
                amount=amount,
                currency=currency,
                created_at=now_iso,
                reason=reason,
                client_request_id=client_request_id,
            )

    def get_agent_request(self, agent_id: str, request_id: str) -> RefundResponse:
        conn = self.db.get_connection()
        cursor = conn.execute(
            """
            SELECT ar.*, ex.status as exec_status, ex.paypal_refund_id, ex.adapter_call_count,
                   ap.id as appr_id
            FROM action_requests ar
            LEFT JOIN executions ex ON ar.id = ex.request_id
            LEFT JOIN approvals ap ON ar.id = ap.request_id
            WHERE ar.id = ?
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

        # Invariant: Scoped to agent's own requests only
        if row["agent_id"] != agent_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: cannot access requests belonging to another agent.",
            )

        reasons = json.loads(row["reasons"])
        return self._build_refund_response(
            request_id=row["id"],
            decision=row["decision"],
            reasons=reasons,
            risk_summary=row["risk_summary"],
            approval_required=bool(row["approval_required"]),
            expires_at=row["expires_at"],
            execution_status=row["exec_status"],
            paypal_refund_id=row["paypal_refund_id"],
            adapter_call_count=row["adapter_call_count"] or 0,
            amount=row["amount"],
            currency=row["currency"],
            approval_id=row["appr_id"],
            created_at=row["created_at"],
            reason=row["reason"] if "reason" in row.keys() else None,
            client_request_id=row["client_request_id"] if "client_request_id" in row.keys() else None,
        )

    def get_agent_policy(self, agent_id: str) -> PolicyResponse:
        conn = self.db.get_connection()
        agent_cur = conn.execute("SELECT merchant_id FROM agents WHERE id = ?", (agent_id,))
        agent_row = agent_cur.fetchone()
        if not agent_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

        pol_cur = conn.execute(
            "SELECT * FROM policies WHERE merchant_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (agent_row["merchant_id"],),
        )
        pol = pol_cur.fetchone()
        if not pol:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Policy not configured.")

        return PolicyResponse(
            version=pol["version"],
            max_auto_amount=pol["max_auto_amount"],
            max_escalate_amount=pol["max_escalate_amount"],
            currency=pol["currency"],
        )

    def merchant_login(self, username: str, password: str) -> MerchantLoginResponse:
        conn = self.db.get_connection()
        cursor = conn.execute("SELECT * FROM merchant_users WHERE username = ?", (username,))
        user = cursor.fetchone()

        if not user or not verify_password(password, user["password_hash"]):
            record_audit_event(
                conn=conn,
                event_type="LOGIN_FAILED",
                actor_type="merchant_user",
                actor_id=username,
                payload={"username": username, "reason": "Invalid credentials"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid username or password.",
            )

        session = create_merchant_session(
            merchant_id=user["merchant_id"],
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
        )

        record_audit_event(
            conn=conn,
            event_type="LOGIN",
            actor_type="merchant_user",
            actor_id=user["id"],
            merchant_id=user["merchant_id"],
            payload={"username": user["username"], "role": user["role"]},
        )

        return MerchantLoginResponse(
            session_token=session.token,
            merchant_id=session.merchant_id,
            username=session.username,
            role=session.role,
            expires_at=session.expires_at.isoformat(),
        )

    def get_merchant_requests(self, merchant_id: str) -> List[RefundResponse]:
        conn = self.db.get_connection()
        cursor = conn.execute(
            """
            SELECT ar.*, ex.status as exec_status, ex.paypal_refund_id, ex.adapter_call_count,
                   ap.id as appr_id
            FROM action_requests ar
            LEFT JOIN executions ex ON ar.id = ex.request_id
            LEFT JOIN approvals ap ON ar.id = ap.request_id
            WHERE ar.merchant_id = ?
            ORDER BY ar.created_at DESC
            """,
            (merchant_id,),
        )
        rows = cursor.fetchall()

        responses = []
        for r in rows:
            reasons = json.loads(r["reasons"])
            responses.append(
                self._build_refund_response(
                    request_id=r["id"],
                    decision=r["decision"],
                    reasons=reasons,
                    risk_summary=r["risk_summary"],
                    approval_required=bool(r["approval_required"]),
                    expires_at=r["expires_at"],
                    execution_status=r["exec_status"],
                    paypal_refund_id=r["paypal_refund_id"],
                    adapter_call_count=r["adapter_call_count"] or 0,
                    amount=r["amount"],
                    currency=r["currency"],
                    approval_id=r["appr_id"],
                    created_at=r["created_at"],
                    reason=r["reason"] if "reason" in r.keys() else None,
                    client_request_id=r["client_request_id"] if "client_request_id" in r.keys() else None,
                )
            )
        return responses

    def approve_request(
        self,
        merchant_id: str,
        user_id: str,
        user_role: str,
        request_id: str,
        reason: Optional[str] = None,
    ) -> RefundResponse:
        conn = self.db.get_connection()

        # Step 1: Verify and consume approval nonce atomically
        try:
            approval = consume_and_verify_approval(
                conn=conn,
                request_id=request_id,
                merchant_id=merchant_id,
                user_id=user_id,
                user_role=user_role,
            )
        except (TamperDetectedError, ExpiredApprovalError, NonceAlreadyConsumedError, CrossMerchantAccessError, ApprovalVerificationError) as e:
            record_audit_event(
                conn=conn,
                event_type="REJECTION",
                actor_type="merchant_user",
                actor_id=user_id,
                request_id=request_id,
                merchant_id=merchant_id,
                payload={"error": str(e), "reason": reason, "action": "approve"},
            )
            # Differentiate HTTP status based on reason
            if isinstance(e, CrossMerchantAccessError):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
            elif isinstance(e, NonceAlreadyConsumedError):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
            elif isinstance(e, (TamperDetectedError, ExpiredApprovalError)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        # Record APPROVAL audit event
        record_audit_event(
            conn=conn,
            event_type="APPROVAL",
            actor_type="merchant_user",
            actor_id=user_id,
            request_id=request_id,
            merchant_id=merchant_id,
            payload={"reason": reason or "Approved by judge", "amount": approval["amount"]},
        )

        # Update action request decision and status
        conn.execute(
            """
            UPDATE action_requests
            SET decision = 'ALLOW', status = 'EXECUTED'
            WHERE id = ?
            """,
            (request_id,),
        )
        conn.commit()

        # Execute refund via adapter
        exec_record = self._execute_refund(
            conn=conn,
            request_id=request_id,
            merchant_id=merchant_id,
            capture_id=approval["capture_id"],
            amount=Decimal(approval["amount"]),
            currency=approval["currency"],
            actor_type="merchant_user",
            actor_id=user_id,
        )

        return self._build_refund_response(
            request_id=request_id,
            decision="ALLOW",
            reasons=["Manually approved by merchant judge."],
            risk_summary="Approved by human merchant reviewer.",
            approval_required=False,
            expires_at=approval["expires_at"],
            execution_status=exec_record["status"],
            paypal_refund_id=exec_record["paypal_refund_id"],
            adapter_call_count=exec_record["adapter_call_count"],
            amount=approval["amount"],
            currency=approval["currency"],
            approval_id=approval["id"],
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )

    def deny_request(
        self,
        merchant_id: str,
        user_id: str,
        user_role: str,
        request_id: str,
        reason: Optional[str] = None,
    ) -> RefundResponse:
        if user_role not in ("judge", "admin"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Must be judge or admin to deny.")

        conn = self.db.get_connection()
        cursor = conn.execute("SELECT * FROM action_requests WHERE id = ?", (request_id,))
        req = cursor.fetchone()
        if not req:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
        if req["merchant_id"] != merchant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

        if req["status"] == "EXECUTED" or req["decision"] == "ALLOW":
            record_audit_event(
                conn=conn,
                event_type="REJECTION",
                actor_type="merchant_user",
                actor_id=user_id,
                request_id=request_id,
                merchant_id=merchant_id,
                payload={"error": "Cannot deny an already approved/executed request.", "action": "deny"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot deny an already approved/executed request.",
            )

        if req["decision"] != "ESCALATE" or req["status"] != "DECIDED":
            record_audit_event(
                conn=conn,
                event_type="REJECTION",
                actor_type="merchant_user",
                actor_id=user_id,
                request_id=request_id,
                merchant_id=merchant_id,
                payload={"error": f"Request with status '{req['status']}' cannot be denied.", "action": "deny"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only pending ESCALATED requests can be denied. Current status is '{req['status']}'.",
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        # Invalidate approval by marking consumed
        conn.execute(
            """
            UPDATE approvals
            SET consumed = 1, consumed_at = ?, consumed_by_user_id = ?
            WHERE request_id = ? AND consumed = 0
            """,
            (now_iso, user_id, request_id),
        )

        # Update action request status
        conn.execute(
            """
            UPDATE action_requests
            SET decision = 'DENIED', status = 'DENIED'
            WHERE id = ?
            """,
            (request_id,),
        )
        conn.commit()

        # Record DENIAL audit event
        record_audit_event(
            conn=conn,
            event_type="DENIAL",
            actor_type="merchant_user",
            actor_id=user_id,
            request_id=request_id,
            merchant_id=merchant_id,
            payload={"reason": reason or "Denied by judge"},
        )

        return self._build_refund_response(
            request_id=request_id,
            decision="DENIED",
            reasons=[reason or "Denied by merchant reviewer."],
            risk_summary="Refund denied by merchant judge.",
            approval_required=False,
            expires_at=None,
            execution_status=None,
            paypal_refund_id=None,
            adapter_call_count=0,
            amount=req["amount"],
            currency=req["currency"],
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            client_request_id=req["client_request_id"] if "client_request_id" in req.keys() else None,
        )

    def get_merchant_audit(self, merchant_id: str) -> AuditResponse:
        conn = self.db.get_connection()
        is_valid, msg, total = verify_audit_chain(conn)

        cursor = conn.execute(
            "SELECT * FROM audit_events WHERE merchant_id = ? OR merchant_id IS NULL ORDER BY id ASC",
            (merchant_id,),
        )
        rows = cursor.fetchall()

        events = [
            AuditEventItem(
                id=r["id"],
                prev_hash=r["prev_hash"],
                event_type=r["event_type"],
                request_id=r["request_id"],
                merchant_id=r["merchant_id"],
                actor_type=r["actor_type"],
                actor_id=r["actor_id"],
                payload=r["payload"],
                timestamp=r["timestamp"],
                hash=r["hash"],
            )
            for r in rows
        ]

        return AuditResponse(
            is_valid=is_valid,
            message=msg,
            total_events=total,
            events=events,
        )

    def get_pending_approvals(self, merchant_id: str) -> List[RefundResponse]:
        """List all pending escalated requests awaiting judge approval for this merchant."""
        conn = self.db.get_connection()
        cursor = conn.execute(
            """
            SELECT ar.*, ex.status as exec_status, ex.paypal_refund_id, ex.adapter_call_count,
                   ap.id as appr_id, ap.expires_at as appr_expires_at
            FROM action_requests ar
            JOIN approvals ap ON ar.id = ap.request_id
            LEFT JOIN executions ex ON ar.id = ex.request_id
            WHERE ar.merchant_id = ? AND ap.consumed = 0 AND ar.status != 'DENIED'
            ORDER BY ar.created_at DESC
            """,
            (merchant_id,),
        )
        rows = cursor.fetchall()
        result = []
        for r in rows:
            reasons = json.loads(r["reasons"])
            result.append(
                self._build_refund_response(
                    request_id=r["id"],
                    decision=r["decision"],
                    reasons=reasons,
                    risk_summary=r["risk_summary"],
                    approval_required=True,
                    expires_at=r["appr_expires_at"] or r["expires_at"],
                    execution_status=r["exec_status"],
                    paypal_refund_id=r["paypal_refund_id"],
                    adapter_call_count=r["adapter_call_count"] or 0,
                    amount=r["amount"],
                    currency=r["currency"],
                    approval_id=r["appr_id"],
                    created_at=r["created_at"],
                    reason=r["reason"] if "reason" in r.keys() else None,
                    client_request_id=r["client_request_id"] if "client_request_id" in r.keys() else None,
                )
            )
        return result

    def get_dashboard_summary(self, merchant_id: str) -> DashboardSummaryResponse:
        """Computes live aggregated dashboard summary stats from persisted records."""
        conn = self.db.get_connection()
        total_requests = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ?", (merchant_id,)).fetchone()[0]
        allowed = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'ALLOW'", (merchant_id,)).fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'BLOCK'", (merchant_id,)).fetchone()[0]
        escalated = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'ESCALATE'", (merchant_id,)).fetchone()[0]

        pending_approvals = conn.execute(
            """
            SELECT COUNT(*) FROM approvals a
            JOIN action_requests r ON a.request_id = r.id
            WHERE r.merchant_id = ? AND a.consumed = 0 AND r.status != 'DENIED'
            """,
            (merchant_id,),
        ).fetchone()[0]

        completed_refunds = conn.execute(
            """
            SELECT COUNT(*) FROM executions e
            JOIN action_requests r ON e.request_id = r.id
            WHERE r.merchant_id = ? AND e.status = 'COMPLETED'
            """,
            (merchant_id,),
        ).fetchone()[0]

        failed_refunds = conn.execute(
            """
            SELECT COUNT(*) FROM executions e
            JOIN action_requests r ON e.request_id = r.id
            WHERE r.merchant_id = ? AND e.status = 'FAILED'
            """,
            (merchant_id,),
        ).fetchone()[0]

        unknown_refunds = conn.execute(
            """
            SELECT COUNT(*) FROM executions e
            JOIN action_requests r ON e.request_id = r.id
            WHERE r.merchant_id = ? AND e.status = 'UNKNOWN'
            """,
            (merchant_id,),
        ).fetchone()[0]

        return DashboardSummaryResponse(
            total_requests=total_requests,
            allowed=allowed,
            blocked=blocked,
            escalated=escalated,
            pending_approvals=pending_approvals,
            completed_refunds=completed_refunds,
            failed_refunds=failed_refunds,
            unknown_refunds=unknown_refunds,
        )

    def get_dashboard_refunds(self, merchant_id: str, limit: int = 50, offset: int = 0) -> DashboardRefundListResponse:
        """Fetches paginated list of refund requests for merchant dashboard."""
        conn = self.db.get_connection()
        total = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ?", (merchant_id,)).fetchone()[0]
        cursor = conn.execute(
            """
            SELECT ar.*, ex.status as exec_status, ex.paypal_refund_id
            FROM action_requests ar
            LEFT JOIN executions ex ON ar.id = ex.request_id
            WHERE ar.merchant_id = ?
            ORDER BY ar.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (merchant_id, limit, offset),
        )
        rows = cursor.fetchall()
        items = []
        for row in rows:
            dec = row["decision"]
            risk_level = "HIGH" if dec == "BLOCK" else ("MEDIUM" if dec == "ESCALATE" else "LOW")
            stat = row["status"]
            if stat == "DECIDED":
                stat = "AWAITING_APPROVAL" if dec == "ESCALATE" else ("BLOCKED" if dec == "BLOCK" else "APPROVED")
            items.append(
                DashboardRefundItem(
                    request_id=row["id"],
                    agent_id=row["agent_id"],
                    capture_id=row["capture_id"],
                    amount=str(row["amount"]),
                    currency=row["currency"],
                    decision=row["decision"],
                    status=stat,
                    execution_status=row["exec_status"] or "NOT_STARTED",
                    risk_level=risk_level,
                    created_at=row["created_at"],
                )
            )
        return DashboardRefundListResponse(items=items, total=total)

    def get_dashboard_risk(self, merchant_id: str) -> DashboardRiskSummary:
        """Fetches aggregated risk statistics for the merchant dashboard."""
        conn = self.db.get_connection()
        high_risk_count = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'BLOCK'", (merchant_id,)).fetchone()[0]
        medium_risk_count = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'ESCALATE'", (merchant_id,)).fetchone()[0]
        low_risk_count = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND decision = 'ALLOW'", (merchant_id,)).fetchone()[0]
        flagged_ai = conn.execute("SELECT COUNT(*) FROM action_requests WHERE merchant_id = ? AND ai_risk_flag = 1", (merchant_id,)).fetchone()[0]

        cursor = conn.execute("SELECT reasons FROM action_requests WHERE merchant_id = ?", (merchant_id,))
        violations: Dict[str, int] = {}
        for row in cursor.fetchall():
            reasons = json.loads(row["reasons"])
            for r in reasons:
                violations[r] = violations.get(r, 0) + 1

        return DashboardRiskSummary(
            high_risk_count=high_risk_count,
            medium_risk_count=medium_risk_count,
            low_risk_count=low_risk_count,
            total_flagged_ai_risk=flagged_ai,
            policy_violations=violations,
        )

    def reconcile_request(self, request_id: str) -> RefundResponse:
        """
        Reconciles an in-flight or timed-out execution by replaying with the
        EXACT SAME paypal_request_id, never a new one.
        """
        conn = self.db.get_connection()
        cursor = conn.execute("SELECT * FROM executions WHERE request_id = ?", (request_id,))
        exec_row = cursor.fetchone()
        if not exec_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found.")

        paypal_request_id = exec_row["paypal_request_id"]
        capture_id = exec_row["capture_id"]
        amount = Decimal(exec_row["amount"])
        currency = exec_row["currency"]

        try:
            result = self.adapter.refund_capture(
                paypal_request_id=paypal_request_id,
                capture_id=capture_id,
                amount=amount,
                currency=currency,
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            new_status = result.get("status")

            if new_status == "COMPLETED":
                # Update capture
                cap_cur = conn.execute("SELECT refunded_amount, original_amount FROM captures WHERE id = ?", (capture_id,))
                cap = cap_cur.fetchone()
                if cap:
                    new_ref = Decimal(cap["refunded_amount"]) + amount
                    new_cap_status = "REFUNDED" if new_ref >= Decimal(cap["original_amount"]) else "PARTIALLY_REFUNDED"
                    conn.execute(
                        "UPDATE captures SET refunded_amount = ?, status = ? WHERE id = ?",
                        (str(new_ref), new_cap_status, capture_id),
                    )

                conn.execute(
                    """
                    UPDATE executions
                    SET status = 'COMPLETED', paypal_refund_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (result.get("id"), now_iso, exec_row["id"]),
                )
                conn.commit()

                record_audit_event(
                    conn=conn,
                    event_type="EXECUTION",
                    actor_type="system",
                    actor_id="reconciler",
                    request_id=request_id,
                    payload={"status": "COMPLETED", "paypal_refund_id": result.get("id")},
                )
            else:
                conn.execute(
                    "UPDATE executions SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now_iso, exec_row["id"]),
                )
                conn.commit()

        except Exception as e:
            record_audit_event(
                conn=conn,
                event_type="EXECUTION_ERROR",
                actor_type="system",
                actor_id="reconciler",
                request_id=request_id,
                payload={"error": str(e)},
            )

        # Return refreshed request
        req_cur = conn.execute(
            """
            SELECT ar.*, ap.id as appr_id
            FROM action_requests ar
            LEFT JOIN approvals ap ON ar.id = ap.request_id
            WHERE ar.id = ?
            """,
            (request_id,),
        )
        req = req_cur.fetchone()
        reasons = json.loads(req["reasons"])
        refreshed_exec = conn.execute("SELECT * FROM executions WHERE id = ?", (exec_row["id"],)).fetchone()

        return self._build_refund_response(
            request_id=req["id"],
            decision=req["decision"],
            reasons=reasons,
            risk_summary=req["risk_summary"],
            approval_required=bool(req["approval_required"]),
            expires_at=req["expires_at"],
            execution_status=refreshed_exec["status"],
            paypal_refund_id=refreshed_exec["paypal_refund_id"],
            adapter_call_count=refreshed_exec["adapter_call_count"],
            amount=req["amount"],
            currency=req["currency"],
            approval_id=req["appr_id"],
            created_at=req["created_at"],
            reason=req["reason"] if "reason" in req.keys() else None,
            client_request_id=req["client_request_id"] if "client_request_id" in req.keys() else None,
        )

    def _execute_refund(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        merchant_id: str,
        capture_id: str,
        amount: Decimal,
        currency: str,
        actor_type: str,
        actor_id: str,
    ) -> Dict[str, Any]:
        """
        Executes money movement through PaypalAdapter:
        1. Writes execution intent and stable paypal_request_id BEFORE calling adapter.
        2. Success ONLY when status is COMPLETED.
        3. Timeout yields UNKNOWN.
        """
        exec_id = f"exec_{secrets.token_hex(10)}"
        paypal_request_id = f"pp_req_{request_id}"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Step 1: Write execution intent BEFORE adapter call
        conn.execute(
            """
            INSERT INTO executions (
                id, request_id, paypal_request_id, capture_id, amount,
                currency, status, paypal_refund_id, adapter_call_count,
                error_message, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', NULL, 0, NULL, ?, ?)
            """,
            (
                exec_id,
                request_id,
                paypal_request_id,
                capture_id,
                str(amount),
                currency,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()

        # Check remaining balance on capture before calling adapter
        cap_cur = conn.execute("SELECT refunded_amount, original_amount, status FROM captures WHERE id = ?", (capture_id,))
        cap = cap_cur.fetchone()
        if cap:
            remaining = Decimal(cap["original_amount"]) - Decimal(cap["refunded_amount"])
            if amount > remaining:
                error_msg = f"Requested amount ${amount} exceeds remaining balance ${remaining}"
                conn.execute(
                    """
                    UPDATE executions
                    SET status = 'FAILED', error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (error_msg, now_iso, exec_id),
                )
                conn.commit()
                record_audit_event(
                    conn=conn,
                    event_type="REJECTION",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    request_id=request_id,
                    merchant_id=merchant_id,
                    payload={"error": error_msg, "amount": str(amount), "remaining": str(remaining)},
                )
                return {
                    "id": exec_id,
                    "status": "FAILED",
                    "paypal_refund_id": None,
                    "adapter_call_count": 0,
                }

        # Step 2: Call PayPal adapter
        paypal_refund_id = None
        adapter_call_count = 1
        error_msg = None

        try:
            result = self.adapter.refund_capture(
                paypal_request_id=paypal_request_id,
                capture_id=capture_id,
                amount=amount,
                currency=currency,
            )
            raw_status = result.get("status")
            paypal_refund_id = result.get("id")

            # Invariant: Success ONLY when refund status is COMPLETED
            if raw_status == "COMPLETED":
                exec_status = "COMPLETED"
                # Update capture refunded amount
                cap_cur = conn.execute("SELECT refunded_amount, original_amount FROM captures WHERE id = ?", (capture_id,))
                cap = cap_cur.fetchone()
                if cap:
                    new_ref = Decimal(cap["refunded_amount"]) + amount
                    new_cap_status = "REFUNDED" if new_ref >= Decimal(cap["original_amount"]) else "PARTIALLY_REFUNDED"
                    conn.execute(
                        "UPDATE captures SET refunded_amount = ?, status = ? WHERE id = ?",
                        (str(new_ref), new_cap_status, capture_id),
                    )
            elif raw_status == "PENDING":
                exec_status = "PENDING"
            else:
                exec_status = "FAILED"
                error_msg = f"Non-completed status returned: {raw_status}"

        except TimeoutError as te:
            # Invariant: A timeout becomes UNKNOWN
            exec_status = "UNKNOWN"
            error_msg = str(te)
        except Exception as e:
            exec_status = "FAILED"
            error_msg = str(e)

        # Update execution row
        conn.execute(
            """
            UPDATE executions
            SET status = ?, paypal_refund_id = ?, adapter_call_count = ?,
                error_message = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                exec_status,
                paypal_refund_id,
                adapter_call_count,
                error_msg,
                datetime.now(timezone.utc).isoformat(),
                exec_id,
            ),
        )
        conn.commit()

        # Record EXECUTION audit event
        record_audit_event(
            conn=conn,
            event_type="EXECUTION",
            actor_type=actor_type,
            actor_id=actor_id,
            request_id=request_id,
            merchant_id=merchant_id,
            payload={
                "status": exec_status,
                "paypal_refund_id": paypal_refund_id,
                "amount": str(amount),
                "paypal_request_id": paypal_request_id,
                "error": error_msg,
            },
        )

        return {
            "id": exec_id,
            "status": exec_status,
            "paypal_refund_id": paypal_refund_id,
            "adapter_call_count": adapter_call_count,
        }
