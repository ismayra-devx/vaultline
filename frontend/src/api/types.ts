// Shapes returned by the live Vaultline backend (security-backend / deploy-render).
// Money is always a decimal STRING. Never parse it into a float for logic.

export type Decision = 'ALLOW' | 'BLOCK' | 'ESCALATE';

/** Request lifecycle (status) — separate from the policy decision and PayPal execution. */
export type RequestStatus =
  | 'RECEIVED' | 'VALIDATING' | 'BLOCKED' | 'AWAITING_APPROVAL' | 'APPROVED' | 'DENIED'
  | 'EXECUTING' | 'PENDING' | 'COMPLETED' | 'FAILED' | 'UNKNOWN' | 'EXPIRED' | string;

/** PayPal execution state. Success only when COMPLETED with a refund id. */
export type ExecutionStatus =
  | 'NOT_STARTED' | 'STARTED' | 'PENDING' | 'COMPLETED' | 'FAILED' | 'CANCELLED' | 'UNKNOWN' | 'RECONCILING' | string;

/** GET /api/merchant/requests item (full decision detail). */
export interface RefundDetail {
  request_id: string;
  decision: Decision;
  reasons: string[];
  risk_summary: string;
  approval_required: boolean;
  expires_at: string | null;
  execution_status: ExecutionStatus | null;
  paypal_refund_id: string | null;
  adapter_call_count: number;
  status: RequestStatus | null;
  amount: string | null;
  currency: string | null;
  refund_id: string | null;
  approval_id: string | null;
  created_at: string | null;
  reason: string | null;
}

/** GET /api/v1/dashboard/refunds item (who / which capture). */
export interface DashboardItem {
  request_id: string;
  agent_id: string;
  capture_id: string;
  amount: string;
  currency: string;
  decision: Decision;
  status: RequestStatus;
  execution_status: ExecutionStatus;
  risk_level: string;
  created_at: string;
}

/** What the UI works with: both views joined on request_id. */
export interface RefundRequest {
  request_id: string;
  agent_id: string | null;
  capture_id: string | null;
  amount: string;
  currency: string;
  decision: Decision;
  status: RequestStatus;
  execution_status: ExecutionStatus;
  reasons: string[];
  risk_summary: string;
  risk_level: string | null;
  approval_id: string | null;
  expires_at: string | null;
  paypal_refund_id: string | null;
  adapter_call_count: number;
  agent_reason: string | null;
  created_at: string;
}

export interface AuditEvent {
  id: number;
  prev_hash: string;
  event_type: string;
  request_id: string | null;
  merchant_id: string | null;
  actor_type: string;
  actor_id: string | null;
  payload: string; // JSON text written by the backend
  timestamp: string;
  hash: string;
}

export interface AuditResponse {
  is_valid: boolean;
  message: string;
  total_events: number;
  events: AuditEvent[];
}

export interface Summary {
  total_requests: number;
  allowed: number;
  blocked: number;
  escalated: number;
  pending_approvals: number;
  completed_refunds: number;
  failed_refunds: number;
  unknown_refunds: number;
}

export interface MerchantSession {
  session_token: string;
  merchant_id: string;
  username: string;
  role: string;
  expires_at: string;
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}
