import { useData } from '../lib/data';
import { ApprovalCard } from '../components/ApprovalCard';
import { Empty, ErrorState, Loading, StaleNote } from '../components/ui';
import { isExpired, money } from '../lib/format';
import type { Decision } from '../api/types';

/* Vaultline custom AG Studio widgets.
 * They read the same 2s-polled backend state as the rest of the app (external store),
 * so they always reflect persisted backend data — never hardcoded values. */

const DECISIONS: Decision[] = ['ALLOW', 'ESCALATE', 'BLOCK'];

export function DecisionBreakdownWidget() {
  const { requests } = useData();
  if (requests.loading) return <Loading />;
  if (requests.error && !requests.data) return <ErrorState error={requests.error} onRetry={requests.refresh} />;
  const rows = requests.data ?? [];
  if (rows.length === 0) return <Empty title="No agent requests yet" hint="Decisions appear here as soon as an agent calls request_refund." />;

  const counts = Object.fromEntries(DECISIONS.map((d) => [d, rows.filter((r) => r.decision === d).length])) as Record<Decision, number>;
  const blocked = rows.filter((r) => r.decision === 'BLOCK');
  // Backend-instrumented adapter counter summed over BLOCKed requests. Must be 0.
  const blockedMoneyCalls = blocked.reduce((n, r) => n + (r.adapter_call_count ?? 0), 0);
  const total = rows.length;

  return (
    <div className="w w-breakdown">
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="w-title">Policy decisions <span className="muted small">· {total} requests</span></div>
      <div className="bar" role="img" aria-label={DECISIONS.map((d) => `${d} ${counts[d]}`).join(', ')}>
        {DECISIONS.map((d) => counts[d] > 0 && <span key={d} className={`seg seg-${d}`} style={{ flexGrow: counts[d] }} title={`${d}: ${counts[d]}`} />)}
      </div>
      <div className="legend">
        {DECISIONS.map((d) => (
          <div key={d} className="legend-item">
            <span className={`dot seg-${d}`} />
            <span>{d}</span>
            <strong>{counts[d]}</strong>
            <span className="muted small">{Math.round((counts[d] / total) * 100)}%</span>
          </div>
        ))}
      </div>
      <div className="kpis">
        <div className="kpi kpi-bad">
          <div className="kpi-v">{blocked.length}</div>
          <div className="kpi-l">Blocked attempts</div>
        </div>
        <div className={`kpi ${blockedMoneyCalls === 0 ? 'kpi-ok' : 'kpi-bad'}`}>
          <div className="kpi-v">{blockedMoneyCalls}</div>
          <div className="kpi-l">PayPal money-calls on blocked requests</div>
        </div>
      </div>
      <p className="muted small">Counter is reported by the backend PayPal adapter, not computed in the browser.</p>
    </div>
  );
}

export function ApprovalQueueWidget() {
  const { requests } = useData();
  if (requests.loading) return <Loading />;
  if (requests.error && !requests.data) return <ErrorState error={requests.error} onRetry={requests.refresh} />;
  const pending = (requests.data ?? []).filter((r) => r.decision === 'ESCALATE' && r.status === 'AWAITING_APPROVAL');
  const live = pending.filter((r) => !isExpired(r.expires_at));
  return (
    <div className="w w-queue">
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="w-title">
        Pending approvals <span className="muted small">· {live.length} live{pending.length > live.length ? `, ${pending.length - live.length} expired` : ''}</span>
      </div>
      {pending.length === 0 ? (
        <Empty title="Nothing waiting for you" hint="Escalated refunds ($10–$25) will queue here with their exact payload." />
      ) : (
        <div className="queue">{pending.map((r) => <ApprovalCard key={r.request_id} r={r} compact />)}</div>
      )}
    </div>
  );
}

export function AuditHealthWidget() {
  const { audit, requests } = useData();
  if (audit.loading) return <Loading />;
  if (audit.error && !audit.data) return <ErrorState error={audit.error} onRetry={audit.refresh} />;
  const a = audit.data!;
  const completed = (requests.data ?? []).filter((r) => r.execution_status === 'COMPLETED');
  const unknown = (requests.data ?? []).filter((r) => r.execution_status === 'UNKNOWN').length;
  return (
    <div className="w">
      <div className="w-title">Audit & execution health</div>
      <div className="kpis">
        <div className={`kpi ${a.is_valid ? 'kpi-ok' : 'kpi-bad'}`}>
          <div className="kpi-v">{a.is_valid ? 'Intact' : 'Broken'}</div>
          <div className="kpi-l">Hash chain (tamper-evident)</div>
        </div>
        <div className="kpi kpi-ok">
          <div className="kpi-v">{completed.length}</div>
          <div className="kpi-l">Verified PayPal refunds</div>
        </div>
        <div className={`kpi ${unknown ? 'kpi-warn' : ''}`}>
          <div className="kpi-v">{unknown}</div>
          <div className="kpi-l">Unknown outcomes</div>
        </div>
      </div>
      <p className="muted small">
        {a.total_events} events · chain checked {audit.updatedAt ? new Date(audit.updatedAt).toLocaleTimeString() : '—'}
        {completed[0] && ` · last refund ${money(completed[0].amount)} ${completed[0].paypal_refund_id}`}
      </p>
    </div>
  );
}
