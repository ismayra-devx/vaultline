import { useEffect, useState } from 'react';
import { api, apiBase, setApiBase, validApiBase } from '../api/client';
import type { ApiError, Decision, RefundRequest } from '../api/types';
import { ApprovalCard } from '../components/ApprovalCard';
import { BrandMark, DecisionBadge, Empty, ErrorState, ExecBadge, Loading, StaleNote } from '../components/ui';
import { TransactionInformation } from '../components/TransactionInformation';
import { useData } from '../lib/data';
import { ago, getCustomerName, money, time } from '../lib/format';

/* ---------------- Requests (transaction explorer) ---------------- */
export function RequestsPage({ onOpen }: { onOpen: (id: string) => void }) {
  const { requests } = useData();
  const getFilterFromHash = (): Decision | 'ALL' => {
    const h = location.hash;
    if (h.includes('filter=ALLOW')) return 'ALLOW';
    if (h.includes('filter=ESCALATE')) return 'ESCALATE';
    if (h.includes('filter=BLOCK')) return 'BLOCK';
    return 'ALL';
  };

  const [filter, setFilter] = useState<Decision | 'ALL'>(getFilterFromHash);

  useEffect(() => {
    const h = () => setFilter(getFilterFromHash());
    window.addEventListener('hashchange', h);
    return () => window.removeEventListener('hashchange', h);
  }, []);

  if (requests.loading) return <Loading label="Loading requests…" />;
  if (requests.error && !requests.data) return <ErrorState error={requests.error} onRetry={requests.refresh} />;

  const allRows = requests.data ?? [];
  const rows = allRows.filter((r) => filter === 'ALL' || r.decision === filter);

  return (
    <section>
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="toolbar" role="group" aria-label="Filter by decision">
        {(['ALL', 'ALLOW', 'ESCALATE', 'BLOCK'] as const).map((d) => (
          <button
            key={d}
            className={`chip ${filter === d ? 'on' : ''}`}
            aria-pressed={filter === d}
            onClick={() => {
              setFilter(d);
              location.hash = `#/requests?filter=${d}`;
            }}
          >
            {d === 'ALL' ? 'All' : d}
          </button>
        ))}
      </div>
      {rows.length === 0 ? (
        <Empty title="No requests match" hint="Requests appear here within 2 seconds of being decided." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Customer</th>
                <th>When</th>
                <th className="num">Amount</th>
                <th>Decision</th>
                <th>Reason</th>
                <th>PayPal execution</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.request_id}>
                  <td>
                    <a
                      href={`#/activity?req=${encodeURIComponent(r.request_id)}`}
                      className="customer-name-link"
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen(r.request_id);
                      }}
                      title={`View audit trail & transaction details for ${r.request_id}`}
                    >
                      {getCustomerName(r)}
                    </a>
                  </td>
                  <td title={time(r.created_at)}>{ago(r.created_at)}</td>
                  <td className="num">{money(r.amount, r.currency)}</td>
                  <td><DecisionBadge d={r.decision} /></td>
                  <td className="small">{r.reasons.join(' ') || '—'}</td>
                  <td>
                    <ExecBadge s={r.execution_status} />
                    {r.paypal_refund_id && <div className="mono small">{r.paypal_refund_id}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/* ---------------- Approvals ---------------- */
export function ApprovalsPage() {
  const { requests } = useData();
  if (requests.loading) return <Loading label="Loading approval queue…" />;
  if (requests.error && !requests.data) return <ErrorState error={requests.error} onRetry={requests.refresh} />;

  // Anything that went through approval (decision flips to ALLOW once approved, so key on approval_id).
  const esc = (requests.data ?? []).filter((r) => r.approval_id || r.status === 'AWAITING_APPROVAL');
  // One stable list (pending first) so a card keeps its result message after the backend state changes.
  const rank = (r: RefundRequest) => (r.status === 'AWAITING_APPROVAL' ? 0 : 1);
  const sorted = [...esc].sort((a, b) => rank(a) - rank(b) || b.created_at.localeCompare(a.created_at));

  return (
    <section>
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      {sorted.length === 0 ? (
        <Empty title="No escalated refunds" hint="Requests between $10 and $25 queue here for merchant approval." />
      ) : (
        <div className="grid-cards">
          {sorted.map((r) => (
            <ApprovalCard key={r.request_id} r={r} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ---------------- Activity: per-request timeline + audit chain ---------------- */
export function ActivityPage({ focus }: { focus: string | null }) {
  const { audit, requests } = useData();
  const [sel, setSel] = useState<string | null>(focus);

  if (audit.loading) return <Loading label="Loading audit trail…" />;
  if (audit.error && !audit.data) return <ErrorState error={audit.error} onRetry={audit.refresh} />;

  const a = audit.data!;
  const ids = [...new Set(a.events.map((e) => e.request_id).filter(Boolean))] as string[];
  const current = sel ?? ids[ids.length - 1] ?? null;
  const events = a.events.filter((e) => e.request_id === current);
  const req = requests.data?.find((r) => r.request_id === current);

  return (
    <section>
      {ids.length === 0 ? (
        <Empty title="No audit events yet" />
      ) : (
        <div className="split">
          <nav className="side-list" aria-label="Requests">
            {ids.slice().reverse().map((id) => {
              const r = requests.data?.find((x) => x.request_id === id);
              const custName = r ? getCustomerName(r) : 'Customer';
              return (
                <button
                  key={id}
                  className={`side-item ${id === current ? 'on' : ''}`}
                  aria-current={id === current}
                  onClick={() => setSel(id)}
                >
                  <div className="side-item-cust">{custName}</div>
                </button>
              );
            })}
          </nav>
          <div className="activity-main">
            {req && (
              <TransactionInformation
                request={req}
                events={events}
              />
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/* ---------------- Login ---------------- */
export function LoginPage({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState('judge_merchant');
  const [p, setP] = useState('');
  const [apiUrl, setApiUrl] = useState(apiBase());
  const [showApi, setShowApi] = useState(!apiBase());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const bad = validApiBase(apiUrl);
    if (bad) {
      setShowApi(true);
      setErr(`API URL: ${bad}`);
      return;
    }
    setApiBase(apiUrl);
    setBusy(true);
    setErr(null);
    try {
      await api.login(u, p);
      setP('');
      onDone();
    } catch (x) {
      const e2 = x as ApiError;
      setErr(e2.status === 401 ? 'Wrong username or password.' : e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form onSubmit={submit} className="login-card">
        <div className="brand">
          <BrandMark />
          <span className="brand-name">Vaultline</span>
        </div>
        <h1>Merchant sign in</h1>
        <p className="muted small">Approve or block escalated refund requests. Connected to PayPal Sandbox.</p>
        <label>
          Username
          <input autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required />
        </label>
        <label>
          Password
          <input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required />
        </label>
        {showApi ? (
          <label>
            Vaultline API URL
            <input inputMode="url" value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} placeholder="https://vaultline-api.onrender.com" required />
          </label>
        ) : (
          <button type="button" className="btn btn-ghost small" onClick={() => setShowApi(true)}>
            Connected to {apiUrl.replace('https://', '')} &middot; change
          </button>
        )}
        {err && (
          <p className="tone-text-bad small" role="alert">
            {err}
          </p>
        )}
        <button className="btn btn-primary" disabled={busy}>
          {busy ? 'Signing in… (free servers can take a minute to wake)' : 'Sign in'}
        </button>
        <p className="muted tiny">Merchant login only. API keys cannot sign in here, and this page never sees PayPal credentials.</p>
      </form>
    </main>
  );
}
