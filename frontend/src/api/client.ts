import { ApiError, type AuditResponse, type DashboardItem, type MerchantSession, type RefundDetail, type RefundRequest, type Summary } from './types';
import * as mock from './mock';

/**
 * Merchant API client for the hosted Vaultline backend.
 * - Merchant routes only. The browser never holds an agent key or any PayPal secret.
 * - The merchant session token from /api/v1/auth/login is kept in sessionStorage (this tab only)
 *   and sent as `Authorization: Bearer`. The backend enforces every permission.
 * - VITE_DEMO_FIXTURES=true swaps in clearly-labelled fixtures for layout work only.
 */
export const USING_FIXTURES = import.meta.env.VITE_DEMO_FIXTURES === 'true';
const BUILD_API = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').trim().replace(/\/$/, '');
const API_KEY = 'vaultline.apiBase';
const TOKEN_KEY = 'vaultline.session';

const safe = <T,>(fn: () => T, fallback: T): T => {
  try {
    return fn();
  } catch {
    return fallback;
  }
};

export function apiBase(): string {
  return safe(() => localStorage.getItem(API_KEY), null) || BUILD_API;
}
export function setApiBase(url: string) {
  const v = url.trim().replace(/\/$/, '');
  safe(() => (v && v !== BUILD_API ? localStorage.setItem(API_KEY, v) : localStorage.removeItem(API_KEY)), undefined);
}
export function validApiBase(url: string): string | null {
  try {
    const u = new URL(url);
    if (u.protocol !== 'https:' && !['localhost', '127.0.0.1'].includes(u.hostname)) return 'The API must use https.';
    if (u.pathname !== '/' || u.search || u.hash) return 'Use the origin only, e.g. https://vaultline-api.onrender.com';
    return null;
  } catch {
    return 'Not a valid URL.';
  }
}

let memoryToken: string | null = null;
const getToken = () => memoryToken ?? safe(() => sessionStorage.getItem(TOKEN_KEY), null);
function setToken(t: string | null) {
  memoryToken = t;
  safe(() => (t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY)), undefined);
}
export const hasSession = () => USING_FIXTURES || !!getToken();

async function req<T>(path: string, init: RequestInit = {}, auth = true): Promise<T> {
  const base = apiBase();
  if (!base) throw new ApiError(0, 'NO_API', 'No Vaultline API URL configured.');
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (init.body) headers['Content-Type'] = 'application/json';
  const tok = getToken();
  if (auth && tok) headers.Authorization = `Bearer ${tok}`;
  let res: Response;
  try {
    res = await fetch(`${base}${path}`, { ...init, headers, credentials: 'omit' });
  } catch {
    throw new ApiError(0, 'NETWORK', 'Cannot reach the Vaultline API (it may be waking up — free servers take up to a minute).');
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && auth) setToken(null);
    const err = body?.error;
    const code = err?.code ?? `HTTP_${res.status}`;
    const detail = body?.detail;
    const msg =
      err?.message ??
      (typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: { msg?: string }) => d.msg).join('; ') : res.statusText);
    throw new ApiError(res.status, code, msg || 'Request failed');
  }
  return body as T;
}

function join(details: RefundDetail[], items: DashboardItem[]): RefundRequest[] {
  const byId = new Map(items.map((i) => [i.request_id, i]));
  return details
    .map((d) => {
      const i = byId.get(d.request_id);
      return {
        request_id: d.request_id,
        agent_id: i?.agent_id ?? null,
        capture_id: i?.capture_id ?? null,
        amount: d.amount ?? i?.amount ?? '0.00',
        currency: d.currency ?? i?.currency ?? 'USD',
        decision: d.decision,
        status: d.status ?? i?.status ?? 'RECEIVED',
        execution_status: d.execution_status ?? i?.execution_status ?? 'NOT_STARTED',
        reasons: d.reasons ?? [],
        risk_summary: d.risk_summary ?? '',
        risk_level: i?.risk_level ?? null,
        approval_id: d.approval_id,
        expires_at: d.expires_at,
        paypal_refund_id: d.paypal_refund_id ?? d.refund_id,
        adapter_call_count: d.adapter_call_count ?? 0,
        agent_reason: d.reason,
        created_at: d.created_at ?? i?.created_at ?? new Date(0).toISOString(),
      };
    })
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export const api = {
  async login(username: string, password: string): Promise<MerchantSession> {
    if (USING_FIXTURES) return mock.login(username);
    const s = await req<MerchantSession>('/api/v1/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }, false);
    setToken(s.session_token);
    return s;
  },
  logout() {
    setToken(null);
  },
  async listRequests(): Promise<RefundRequest[]> {
    if (USING_FIXTURES) return mock.listRequests();
    const [details, list] = await Promise.all([
      req<RefundDetail[]>('/api/merchant/requests'),
      req<{ items: DashboardItem[] }>('/api/v1/dashboard/refunds?limit=200'),
    ]);
    return join(details, list.items);
  },
  summary: () => (USING_FIXTURES ? mock.summary() : req<Summary>('/api/v1/dashboard/summary')),
  // The backend re-validates expiry, nonce, payload binding and policy before any PayPal call.
  approve: (approvalId: string) =>
    USING_FIXTURES ? mock.approve(approvalId) : req<RefundDetail>(`/api/v1/approvals/${encodeURIComponent(approvalId)}/approve`, { method: 'POST', body: '{}' }),
  deny: (approvalId: string) =>
    USING_FIXTURES ? mock.deny(approvalId) : req<RefundDetail>(`/api/v1/approvals/${encodeURIComponent(approvalId)}/deny`, { method: 'POST', body: '{}' }),
  audit: () => (USING_FIXTURES ? mock.audit() : req<AuditResponse>('/api/v1/audit')),
};
