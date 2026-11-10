import { useMemo, useState } from 'react';
import { api, apiBase, setApiBase, validApiBase } from '../api/client';
import type { AnalystAnswer, ApiError, Decision, RefundRequest } from '../api/types';
import { ApprovalCard } from '../components/ApprovalCard';
import { DecisionBadge, Empty, ErrorState, ExecBadge, Loading, StaleNote } from '../components/ui';
import { useData } from '../lib/data';
import { ago, decimalGt, money, time } from '../lib/format';

/* ---------------- Requests (transaction explorer) ---------------- */
export function RequestsPage({ onOpen }: { onOpen: (id: string) => void }) {
  const { requests } = useData();
  const [filter, setFilter] = useState<Decision | 'ALL'>('ALL');
  if (requests.loading) return <Loading label="Loading requests…" />;
  if (requests.error && !requests.data) return <ErrorState error={requests.error} onRetry={requests.refresh} />;
  const rows = (requests.data ?? []).filter((r) => filter === 'ALL' || r.decision === filter);
  return (
    <section>
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="toolbar" role="group" aria-label="Filter by decision">
        {(['ALL', 'ALLOW', 'ESCALATE', 'BLOCK'] as const).map((d) => (
          <button key={d} className={`chip ${filter === d ? 'on' : ''}`} aria-pressed={filter === d} onClick={() => setFilter(d)}>{d}</button>
        ))}
      </div>
      {rows.length === 0 ? (
        <Empty title="No requests match" hint="Agent requests appear here within 2 seconds of being decided." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>When</th><th>Request</th><th>Agent</th><th className="num">Amount</th><th>Decision</th><th>Reason</th><th>PayPal execution</th></tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.request_id} tabIndex={0} onClick={() => onOpen(r.request_id)} onKeyDown={(e) => e.key === 'Enter' && onOpen(r.request_id)}>
                  <td title={time(r.created_at)}>{ago(r.created_at)}</td>
                  <td className="mono small">{r.request_id}</td>
                  <td className="mono small">{r.agent_id}</td>
                  <td className="num">{money(r.amount, r.currency)}</td>
                  <td><DecisionBadge d={r.decision} /></td>
                  <td className="small">{r.reasons.join(' ') || '—'}</td>
                  <td><ExecBadge s={r.execution_status} />{r.paypal_refund_id && <div className="mono small">{r.paypal_refund_id}</div>}</td>
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
      <h2>Escalated refunds · {esc.filter((r) => r.status === 'AWAITING_APPROVAL').length} waiting for you</h2>
      {sorted.length === 0 ? <Empty title="No escalated refunds" hint="Requests between $10 and $25 queue here for merchant approval." /> : <div className="grid-cards">{sorted.map((r) => <ApprovalCard key={r.request_id} r={r} />)}</div>}
    </section>
  );
}

/* ---------------- Activity: per-request timeline + audit chain ---------------- */
function eventDetail(payload: string): string {
  try {
    const p = JSON.parse(payload) as Record<string, unknown>;
    return Object.entries(p)
      .filter(([, v]) => v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(' ') : typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
      .join(' · ');
  } catch {
    return payload;
  }
}

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
      <div className={`chain ${a.is_valid ? 'ok' : 'bad'}`} role="status">
        <strong>Audit hash chain: {a.is_valid ? 'verified intact' : 'BROKEN'}</strong>
        <span className="muted small"> · {a.total_events} events · SHA-256 chain, tamper-evident (detects edits; not immutable) · checked {time(audit.updatedAt ? new Date(audit.updatedAt).toISOString() : null)}</span>
      </div>
      {ids.length === 0 ? (
        <Empty title="No audit events yet" />
      ) : (
        <div className="split">
          <nav className="side-list" aria-label="Requests">
            {ids.slice().reverse().map((id) => (
              <button key={id} className={`side-item ${id === current ? 'on' : ''}`} aria-current={id === current} onClick={() => setSel(id)}>
                <span className="mono small">{id}</span>
              </button>
            ))}
          </nav>
          <div>
            {req && (
              <div className="req-summary">
                <span className="amount">{money(req.amount)}</span>
                <div><span className="muted small">Policy decision</span><br /><DecisionBadge d={req.decision} /></div>
                <div><span className="muted small">PayPal execution (separate)</span><br /><ExecBadge s={req.execution_status} /></div>
                {req.paypal_refund_id && <div><span className="muted small">Refund ID</span><br /><span className="mono">{req.paypal_refund_id}</span></div>}
                <div><span className="muted small">Adapter money-calls</span><br /><strong>{req.adapter_call_count}</strong></div>
              </div>
            )}
            <ol className="timeline">
              {events.map((e) => (
                <li key={e.id} className={`ev ev-${e.event_type}`}>
                  <div className="ev-head"><strong>{e.event_type.replaceAll('_', ' ')}</strong><span className="muted small">#{e.id} · {time(e.timestamp)} · {e.actor_type}{e.actor_id ? `:${e.actor_id}` : ''}</span></div>
                  <div className="small mono">{eventDetail(e.payload)}</div>
                  <div className="mono tiny muted">prev {e.prev_hash} → {e.hash}</div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </section>
  );
}

/* ---------------- Read-only analyst ---------------- */
const EXAMPLE = 'Show blocked refund attempts above $25 and summarize why.';

/** Deterministic fallback — clearly labelled, never presented as model output. */
function ruleBased(q: string, rows: RefundRequest[]): AnalystAnswer {
  const lower = q.toLowerCase();
  const dec = (['block', 'escalat', 'allow'] as const).find((d) => lower.includes(d));
  const thr = lower.match(/(?:above|over|>)\s*\$?(\d+(?:\.\d+)?)/)?.[1];
  const hits = rows.filter((r) => (!dec || r.decision.toLowerCase().startsWith(dec)) && (!thr || decimalGt(r.amount, thr)));
  const byReason = new Map<string, number>();
  hits.forEach((r) => r.reasons.forEach((c) => byReason.set(c, (byReason.get(c) ?? 0) + 1)));
  const reasons = [...byReason].sort((a, b) => b[1] - a[1]).map(([c, n]) => `${n}× ${c}`).join(' ');
  return {
    source: 'rule-based',
    record_ids: hits.map((r) => r.request_id),
    summary: hits.length === 0 ? 'No matching records.' : `${hits.length} matching request(s)${dec ? ` with decision ${dec.toUpperCase()}` : ''}${thr ? ` above $${thr}` : ''}. ${reasons}`,
  };
}

export function AnalystPanel() {
  const { requests } = useData();
  const [q, setQ] = useState(EXAMPLE);
  const [ans, setAns] = useState<AnalystAnswer | null>(null);
    const byId = useMemo(() => new Map((requests.data ?? []).map((r) => [r.request_id, r])), [requests.data]);

  function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!q.trim() || !requests.data) return;
    // No AI model is wired to the hosted backend yet, so this is a deterministic filter, labelled as such.
    setAns(ruleBased(q, requests.data));
  }

  return (
    <aside className="analyst" aria-label="Read-only analyst">
      <div className="w-title">Analyst <span className="badge tone-muted">read-only</span></div>
      <p className="muted small">Filters and summarises the live records with deterministic rules (no AI model yet). It cannot approve, deny or execute refunds.</p>
      <form onSubmit={ask}>
        <label htmlFor="aq" className="sr-only">Question</label>
        <textarea id="aq" value={q} onChange={(e) => setQ(e.target.value)} rows={3} />
        <div className="actions">
          <button type="button" className="btn btn-ghost small" onClick={() => setQ(EXAMPLE)}>Example</button>
          <button className="btn btn-primary">Ask</button>
        </div>
      </form>
      {ans && (
        <div className="answer" role="status">
          <div className="muted tiny">{ans.source === 'model' ? 'AI summary · grounded in the records below' : 'Rule-based summary · no AI'}</div>
          <p>{ans.summary}</p>
          {ans.record_ids.length > 0 && (
            <ul className="cites">
              {ans.record_ids.map((id) => {
                const r = byId.get(id);
                return <li key={id}><span className="mono small">{id}</span>{r ? <> · {money(r.amount)} · <DecisionBadge d={r.decision} /></> : <span className="muted small"> · not in current view</span>}</li>;
              })}
            </ul>
          )}
        </div>
      )}
    </aside>
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
    if (bad) { setShowApi(true); setErr(`API URL: ${bad}`); return; }
    setApiBase(apiUrl);
    setBusy(true); setErr(null);
    try { await api.login(u, p); setP(''); onDone(); }
    catch (x) { const e2 = x as ApiError; setErr(e2.status === 401 ? 'Wrong username or password.' : e2.message); }
    finally { setBusy(false); }
  }
  return (
    <main className="login">
      <form onSubmit={submit} className="login-card">
        <div className="brand big">VAULTLINE</div>
        <p className="muted small">Merchant control plane · PayPal Sandbox</p>
        <label>Username<input autoComplete="username" value={u} onChange={(e) => setU(e.target.value)} required /></label>
        <label>Password<input type="password" autoComplete="current-password" value={p} onChange={(e) => setP(e.target.value)} required /></label>
        {showApi ? (
          <label>Vaultline API URL<input inputMode="url" value={apiUrl} onChange={(e) => setApiUrl(e.target.value)} placeholder="https://vaultline-api.onrender.com" required /></label>
        ) : (
          <button type="button" className="btn btn-ghost small" onClick={() => setShowApi(true)}>Connected to {apiUrl.replace('https://', '')} · change</button>
        )}
        {err && <p className="tone-text-bad small" role="alert">{err}</p>}
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Signing in… (free servers can take a minute to wake)' : 'Sign in'}</button>
        <p className="muted tiny">Merchant login only. Agent keys cannot sign in here, and this page never sees PayPal credentials.</p>
      </form>
    </main>
  );
}
