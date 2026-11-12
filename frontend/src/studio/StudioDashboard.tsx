import { useMemo } from 'react';
import { useData } from '../lib/data';
import { ago, getCustomerName, money } from '../lib/format';
import { Empty, Loading, DecisionBadge } from '../components/ui';

export function StudioDashboard({ onOpen }: { onOpen?: (id: string) => void }) {
  const { requests } = useData();

  const now = useMemo(() => new Date(), []);

  if (requests.loading) return <Loading label="Loading dashboard…" />;
  const allRequests = requests.data ?? [];

  // Row 1: Three cards
  const requestedCount = allRequests.length;
  const requestedTotal = allRequests.reduce((sum, r) => sum + parseFloat(r.amount || '0'), 0);

  const approvedRequests = allRequests.filter(
    (r) => r.decision === 'ALLOW' || r.status === 'COMPLETED' || r.status === 'APPROVED'
  );
  const approvedCount = approvedRequests.length;
  const approvedTotal = approvedRequests.reduce((sum, r) => sum + parseFloat(r.amount || '0'), 0);

  const blockedRequests = allRequests.filter((r) => r.decision === 'BLOCK');
  const blockedCount = blockedRequests.length;
  const blockedTotal = blockedRequests.reduce((sum, r) => sum + parseFloat(r.amount || '0'), 0);

  // Row 2: This month metrics
  const thisMonthRequests = allRequests.filter((r) => {
    if (!r.created_at) return true;
    const d = new Date(r.created_at);
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
  });
  const totalTransactionsMonth = thisMonthRequests.length;
  const totalAmountMonth = thisMonthRequests.reduce((sum, r) => sum + parseFloat(r.amount || '0'), 0);
  // Backend dependency: Vaultline policy engine currently enforces per-transaction thresholds ($10.00 auto / $25.00 escalation).
  // No monthly limit is defined in the policy schema or exposed by the backend API.
  const backendMonthlyLimit = null as number | null;
  const amountRemaining = backendMonthlyLimit !== null ? Math.max(0, backendMonthlyLimit - totalAmountMonth) : null;

  // Row 3: Recent requests (sorted by created_at DESC)
  const recentRequests = [...allRequests]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 6);

  // Row 3: Pending approvals · N live
  const pendingApprovals = allRequests.filter(
    (r) => r.status === 'AWAITING_APPROVAL' || (r.decision === 'ESCALATE' && r.status !== 'APPROVED' && r.status !== 'DENIED')
  );

  return (
    <div className="card-dashboard">
      {/* Row 1: Three cards */}
      <div className="dash-row-1">
        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">REQUESTED</span>
          </div>
          <div className="dash-card-count">{requestedCount}</div>
          <div className="dash-card-sub">Total value {money(requestedTotal.toFixed(2))}</div>
          <a href="#/requests?filter=ALL" className="dash-card-link">
            View all requests &rarr;
          </a>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">APPROVED</span>
          </div>
          <div className="dash-card-count">{approvedCount}</div>
          <div className="dash-card-sub">{money(approvedTotal.toFixed(2))} refunded</div>
          <a href="#/requests?filter=ALLOW" className="dash-card-link">
            View approved &rarr;
          </a>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">BLOCKED</span>
          </div>
          <div className="dash-card-count">{blockedCount}</div>
          <div className="dash-card-sub">{money(blockedTotal.toFixed(2))} prevented</div>
          <a href="#/requests?filter=BLOCK" className="dash-card-link">
            View blocked &rarr;
          </a>
        </div>
      </div>

      {/* Row 2: One strip */}
      <div className="dash-strip">
        <div className="dash-strip-item">
          <span className="dash-strip-label">TOTAL TRANSACTIONS THIS MONTH</span>
          <span className="dash-strip-value">{totalTransactionsMonth}</span>
        </div>
        <div className="dash-strip-divider" aria-hidden />
        <div className="dash-strip-item">
          <span className="dash-strip-label">TOTAL AMOUNT THIS MONTH</span>
          <span className="dash-strip-value">{money(totalAmountMonth.toFixed(2))}</span>
        </div>
        <div className="dash-strip-divider" aria-hidden />
        <div className="dash-strip-item">
          <span className="dash-strip-label">
            {backendMonthlyLimit !== null
              ? `AMOUNT REMAINING (MONTHLY LIMIT ${money(backendMonthlyLimit.toFixed(2))})`
              : 'MONTHLY POLICY LIMIT'}
          </span>
          <span
            className="dash-strip-value"
            style={backendMonthlyLimit === null ? { fontSize: '15px', color: 'var(--muted)' } : undefined}
            title={
              backendMonthlyLimit === null
                ? 'Backend dependency: Policy engine enforces per-transaction thresholds ($10.00 auto / $25.00 escalation); no monthly cap is exposed by the API.'
                : undefined
            }
          >
            {backendMonthlyLimit !== null ? money(amountRemaining!.toFixed(2)) : 'Not configured (Backend dependency)'}
          </span>
        </div>
      </div>

      {/* Row 3: Two panels */}
      <div className="dash-row-3">
        {/* Panel 1: Recent requests */}
        <section className="dash-panel">
          <div className="dash-panel-head">
            <h3>Recent requests</h3>
            <a href="#/requests" className="dash-panel-link">
              View all &rarr;
            </a>
          </div>
          {recentRequests.length === 0 ? (
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
                  {recentRequests.map((r) => (
                    <tr key={r.request_id}>
                      <td>
                        <a
                          href={`#/activity?req=${encodeURIComponent(r.request_id)}`}
                          className="customer-name-link"
                          onClick={(e) => {
                            e.preventDefault();
                            onOpen?.(r.request_id);
                          }}
                          title={`View audit trail & transaction details for ${r.request_id}`}
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
        </section>

        {/* Panel 2: Pending approvals · N live */}
        <section className="dash-panel">
          <div className="dash-panel-head">
            <h3>Pending approvals &middot; {pendingApprovals.length} live</h3>
            <a href="#/approvals" className="dash-panel-link">
              Approvals &rarr;
            </a>
          </div>
          {pendingApprovals.length === 0 ? (
            <div className="state">
              <strong>Nothing waiting for you</strong>
              <p className="muted small">Escalated refunds will queue here for review.</p>
            </div>
          ) : (
            <ul className="pending-approvals-list">
              {pendingApprovals.map((r) => (
                <li key={r.request_id} className="pending-approval-item">
                  <div className="pending-item-main">
                    <a
                      href={`#/activity?req=${encodeURIComponent(r.request_id)}`}
                      className="customer-name-link"
                      onClick={(e) => {
                        e.preventDefault();
                        onOpen?.(r.request_id);
                      }}
                      title={`View audit trail & transaction details for ${r.request_id}`}
                    >
                      {getCustomerName(r)}
                    </a>
                    <span className="small muted">{ago(r.created_at)}</span>
                  </div>
                  <div className="pending-item-amount num">
                    <strong>{money(r.amount, r.currency)}</strong>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
