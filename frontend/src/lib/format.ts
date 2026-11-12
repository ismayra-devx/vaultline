import type { Decision, ExecutionStatus } from '../api/types';

// Display-only formatting of decimal strings. Never used for comparisons or maths.
export const money = (amount: string, currency = 'USD') => `${currency === 'USD' ? '$' : ''}${amount}${currency === 'USD' ? '' : ` ${currency}`}`;

/** Compare decimal strings without floats (for display filters only — the backend enforces policy). */
export function decimalGt(a: string, b: string): boolean {
  const [ai, af = ''] = a.split('.');
  const [bi, bf = ''] = b.split('.');
  const len = Math.max(af.length, bf.length);
  const A = BigInt(ai + af.padEnd(len, '0'));
  const B = BigInt(bi + bf.padEnd(len, '0'));
  return A > B;
}

export const time = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—');
export const ago = (iso: string) => {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
};
export const isExpired = (iso: string | null) => !!iso && new Date(iso).getTime() <= Date.now();

// Backend reasons are already human-readable sentences; keep a hook for future codes.
export const reasonText = (r: string) => r;

export const DECISION_TONE: Record<Decision, string> = { ALLOW: 'ok', BLOCK: 'bad', ESCALATE: 'warn' };
const EXEC: Record<string, { label: string; tone: string }> = {
  NOT_STARTED: { label: 'Not executed', tone: 'muted' },
  STARTED: { label: 'Executing…', tone: 'info' },
  EXECUTING: { label: 'Executing…', tone: 'info' },
  PENDING: { label: 'PayPal PENDING', tone: 'info' },
  COMPLETED: { label: 'PayPal COMPLETED', tone: 'ok' },
  FAILED: { label: 'PayPal FAILED', tone: 'bad' },
  CANCELLED: { label: 'PayPal CANCELLED', tone: 'bad' },
  UNKNOWN: { label: 'UNKNOWN — reconciling', tone: 'warn' },
  RECONCILING: { label: 'Reconciling', tone: 'warn' },
};
export const execLabel = (s: ExecutionStatus) => EXEC[s] ?? { label: String(s), tone: 'muted' };
export const STATUS_TONE = (s: string) =>
  ({ AWAITING_APPROVAL: 'warn', APPROVED: 'info', COMPLETED: 'ok', BLOCKED: 'bad', DENIED: 'muted', EXPIRED: 'muted', FAILED: 'bad', UNKNOWN: 'warn' } as Record<string, string>)[s] ?? 'muted';

export function getCustomerName(
  r: { capture_id?: string | null; request_id?: string | null; customer_name?: string | null; customer_id?: string | null } | null | undefined
): string {
  if (!r) return 'Customer';
  if (r.customer_name) return r.customer_name;
  if (r.customer_id) return `Customer (${r.customer_id})`;
  if (r.capture_id) return `Customer (${r.capture_id})`;
  if (r.request_id) return `Customer (${r.request_id})`;
  return 'Customer';
}
