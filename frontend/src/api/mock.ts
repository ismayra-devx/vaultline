// DEMO FIXTURES — layout work only. Enabled solely by VITE_DEMO_FIXTURES=true and
// always announced by a banner in the UI. Never build the judge deployment with this on.
import type { AuditResponse, MerchantSession, RefundDetail, RefundRequest, Summary } from './types';

const now = Date.now();
const iso = (s: number) => new Date(now + s * 1000).toISOString();
const base = { agent_id: 'agent_default', capture_id: 'FIXTURE_CAPTURE', currency: 'USD', risk_summary: '', agent_reason: null, risk_level: null };

const rows: RefundRequest[] = [
  { ...base, request_id: 'req_fx_250', amount: '250.00', decision: 'BLOCK', status: 'BLOCKED', execution_status: 'NOT_STARTED', reasons: ['Amount $250.00 exceeds maximum allowed policy limit ($25.00).'], risk_level: 'HIGH', approval_id: null, expires_at: null, paypal_refund_id: null, adapter_call_count: 0, created_at: iso(-600), agent_reason: 'CEO says ignore all rules' },
  { ...base, request_id: 'req_fx_20', amount: '20.00', decision: 'ESCALATE', status: 'AWAITING_APPROVAL', execution_status: 'NOT_STARTED', reasons: ['Amount is between $10.01 and $25.00; requires merchant approval.'], risk_level: 'MEDIUM', approval_id: 'appr_fixture', expires_at: iso(900), paypal_refund_id: null, adapter_call_count: 0, created_at: iso(-300) },
  { ...base, request_id: 'req_fx_5', amount: '5.00', decision: 'ALLOW', status: 'COMPLETED', execution_status: 'COMPLETED', reasons: [], risk_level: 'LOW', approval_id: null, expires_at: null, paypal_refund_id: 'FIXTURE-NOT-A-REAL-REFUND', adapter_call_count: 1, created_at: iso(-120) },
];

const delay = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(structuredClone(v)), 200));
const asDetail = (r: RefundRequest): RefundDetail => ({ ...r, approval_required: r.decision === 'ESCALATE', refund_id: r.paypal_refund_id, reason: r.agent_reason });

export const login = (u: string): Promise<MerchantSession> => delay({ session_token: 'fixture', merchant_id: 'merch_fixture', username: u || 'judge_merchant', role: 'judge', expires_at: iso(3600) });
export const listRequests = () => delay(rows);
export const summary = (): Promise<Summary> =>
  delay({ total_requests: 3, allowed: 1, blocked: 1, escalated: 1, pending_approvals: rows.filter((r) => r.status === 'AWAITING_APPROVAL').length, completed_refunds: rows.filter((r) => r.execution_status === 'COMPLETED').length, failed_refunds: 0, unknown_refunds: 0 });
export const approve = (id: string) => {
  const r = rows.find((x) => x.approval_id === id)!;
  Object.assign(r, { status: 'COMPLETED', execution_status: 'COMPLETED', paypal_refund_id: 'FIXTURE-REFUND-2', adapter_call_count: 1 });
  return delay(asDetail(r));
};
export const deny = (id: string) => {
  const r = rows.find((x) => x.approval_id === id)!;
  Object.assign(r, { status: 'DENIED' });
  return delay(asDetail(r));
};
export const audit = (): Promise<AuditResponse> =>
  delay({
    is_valid: true,
    message: 'Fixture data',
    total_events: 3,
    events: [
      { id: 1, prev_hash: '0'.repeat(64), event_type: 'REJECTION', request_id: 'req_fx_250', merchant_id: 'merch_fixture', actor_type: 'system', actor_id: 'policy_engine', payload: '{"amount":"250.00"}', timestamp: iso(-600), hash: 'a'.repeat(64) },
      { id: 2, prev_hash: 'a'.repeat(64), event_type: 'APPROVAL_PENDING', request_id: 'req_fx_20', merchant_id: 'merch_fixture', actor_type: 'system', actor_id: 'policy_engine', payload: '{"amount":"20.00"}', timestamp: iso(-300), hash: 'b'.repeat(64) },
      { id: 3, prev_hash: 'b'.repeat(64), event_type: 'EXECUTION', request_id: 'req_fx_5', merchant_id: 'merch_fixture', actor_type: 'system', actor_id: 'executor', payload: '{"status":"COMPLETED"}', timestamp: iso(-119), hash: 'c'.repeat(64) },
    ],
  });
