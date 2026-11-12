import type { ReactNode } from 'react';
import { useData } from '../lib/data';
import { ApprovalCard } from '../components/ApprovalCard';
import { Empty, ErrorState, Loading, StaleNote, DecisionBadge } from '../components/ui';
import { getCustomerName, money } from '../lib/format';
import type { Decision } from '../api/types';

const DECISIONS: Decision[] = ['ALLOW', 'ESCALATE', 'BLOCK'];

const Card = ({ className = '', children }: { className?: string; children: ReactNode }) => (
  <div className={`w ${className}`}>{children}</div>
);

export function DecisionBreakdownWidget() {
  const { requests } = useData();
  if (requests.loading) return <Card><Loading /></Card>;
  if (requests.error && !requests.data) return <Card><ErrorState error={requests.error} onRetry={requests.refresh} /></Card>;
  const rows = requests.data ?? [];
  if (rows.length === 0) return <Card><Empty title="No requests yet" hint="Decisions appear here as soon as refunds are processed." /></Card>;

  const counts = Object.fromEntries(DECISIONS.map((d) => [d, rows.filter((r) => r.decision === d).length])) as Record<Decision, number>;
  const blocked = rows.filter((r) => r.decision === 'BLOCK');
  const total = rows.length;

  return (
    <Card>
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="w-title">Policy decisions <span className="w-sub">· {total} requests</span></div>
      <div className="bar" role="img" aria-label={DECISIONS.map((d) => `${d} ${counts[d]}`).join(', ')}>
        {DECISIONS.map((d) => counts[d] > 0 && <span key={d} className={`seg seg-${d}`} style={{ flexGrow: counts[d] }} title={`${d}: ${counts[d]}`} />)}
      </div>
      <div className="legend">
        {DECISIONS.map((d) => (
          <div key={d} className="legend-item">
            <span className={`dot seg-${d}`} />
            <span>{d}</span>
            <strong>{counts[d]}</strong>
            <span className="w-sub small">{total > 0 ? Math.round((counts[d] / total) * 100) : 0}%</span>
          </div>
        ))}
      </div>
      <div className="kpis">
        <div className="kpi kpi-bad">
          <div className="kpi-v">{blocked.length}</div>
          <div className="kpi-l">Blocked attempts</div>
        </div>
      </div>
    </Card>
  );
}

export function ApprovalQueueWidget() {
  const { requests } = useData();
  if (requests.loading) return <Card><Loading /></Card>;
  if (requests.error && !requests.data) return <Card><ErrorState error={requests.error} onRetry={requests.refresh} /></Card>;
  const pending = (requests.data ?? []).filter((r) => r.decision === 'ESCALATE' && r.status === 'AWAITING_APPROVAL');
  return (
    <Card className="w-queue">
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}
      <div className="w-title">
        Pending approvals <span className="w-sub">· {pending.length} live</span>
      </div>
      {pending.length === 0 ? (
        <div className="state">
          <strong>Nothing waiting for you</strong>
          <p className="muted small">Escalated refunds will queue here for review.</p>
        </div>
      ) : (
        <div className="queue">{pending.map((r) => <ApprovalCard key={r.request_id} r={r} compact />)}</div>
      )}
    </Card>
  );
}

export function RecentRequestsWidget({ onOpen }: { onOpen?: (id: string) => void }) {
  const { requests } = useData();
  if (requests.loading) return <Card><Loading /></Card>;
  const allRequests = requests.data ?? [];
  const recent = [...allRequests].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 8);

  return (
    <Card>
      <div className="w-title">Recent requests <span className="w-sub">· live telemetry</span></div>
      {recent.length === 0 ? (
        <Empty title="No requests yet" hint="Refund requests will appear here." />
      ) : (
        <div className="table-wrap">
          <table className="dash-table">
            <thead>
              <tr>
                <th>CUSTOMER</th>
                <th className="num">AMOUNT</th>
                <th>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((r) => (
                <tr key={r.request_id}>
                  <td>
                    <a
                      href={`#/activity?req=${encodeURIComponent(r.request_id)}`}
                      className="customer-name-link"
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen?.(r.request_id);
                      }}
                      title={`View audit trail for ${r.request_id}`}
                    >
                      {getCustomerName(r)}
                    </a>
                  </td>
                  <td className="num">{money(r.amount, r.currency)}</td>
                  <td><DecisionBadge d={r.decision} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
