import { useMemo, useState } from 'react';
import { useData } from '../lib/data';
import { ago, getCustomerName, money } from '../lib/format';
import { Empty, Loading } from '../components/ui';
import { TransactionInformation } from '../components/TransactionInformation';

export function StudioDashboard() {
  const { requests, audit } = useData();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const now = useMemo(() => new Date(), []);

  if (requests.loading) return <Loading label="Loading dashboard…" />;
  const allRequests = requests.data ?? [];
  const auditEvents = audit.data?.events ?? [];

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
  const MONTHLY_LIMIT = 1000;
  const amountRemaining = Math.max(0, MONTHLY_LIMIT - totalAmountMonth);

  // Row 3: Recent requests (sorted by created_at DESC)
  const recentRequests = [...allRequests]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 6);

  // Row 3: Pending approvals · N live
  const pendingApprovals = allRequests.filter(
    (r) => r.status === 'AWAITING_APPROVAL' || (r.decision === 'ESCALATE' && r.status !== 'APPROVED' && r.status !== 'DENIED')
  );

  const selectedRequest = allRequests.find((r) => r.request_id === selectedId);

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
          <span className="dash-strip-label">AMOUNT REMAINING (MONTHLY LIMIT {money(MONTHLY_LIMIT.toFixed(2))})</span>
          <span className="dash-strip-value">{money(amountRemaining.toFixed(2))}</span>
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
            <Empty title="No requests yet" hint="Agent refund requests will appear here." />
          ) : (
            <div className="table-wrap">
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>CUSTOMER</th>
                    <th>AGENT</th>
                    <th className="num">AMOUNT</th>
                    <th>STATUS</th>
                  </tr>
                </thead>
                <tbody>
                  {recentRequests.map((r) => (
                    <tr
                      key={r.request_id}
                      className={r.request_id === selectedId ? 'selected-row' : ''}
                    >
                      <td>
                        <button
                          type="button"
                          className="customer-name-link"
                          onClick={() => setSelectedId((prev) => (prev === r.request_id ? null : r.request_id))}
                          title={`View transaction details for ${r.request_id}`}
                        >
                          {getCustomerName(r)}
                        </button>
                      </td>
                      <td className="mono small">{r.agent_id ?? '—'}</td>
                      <td className="num">{money(r.amount, r.currency)}</td>
                      <td>{r.decision}</td>
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
                <li
                  key={r.request_id}
                  className={`pending-approval-item ${r.request_id === selectedId ? 'selected-item' : ''}`}
                >
                  <div className="pending-item-main">
                    <button
                      type="button"
                      className="customer-name-link"
                      onClick={() => setSelectedId((prev) => (prev === r.request_id ? null : r.request_id))}
                      title={`View transaction details for ${r.request_id}`}
                    >
                      {getCustomerName(r)}
                    </button>
                    <span className="mono small muted">{r.agent_id ?? 'agent'}</span>
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

      {/* Transaction Information rendered under the request section */}
      {selectedRequest && (
        <section className="dash-details-section" aria-label="Transaction Details">
          <TransactionInformation
            request={selectedRequest}
            events={auditEvents}
            onClose={() => setSelectedId(null)}
          />
        </section>
      )}
    </div>
  );
}
