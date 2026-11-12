import { useData } from '../lib/data';
import { ago, getCustomerName, money } from '../lib/format';
import { Badge, DecisionBadge, Empty, ErrorState, Loading, StaleNote } from '../components/ui';

/** Parse a decimal money string into integer cents safely; invalid values yield 0 (never NaN). */
function parseCents(amount: string | null | undefined): number {
  if (!amount) return 0;
  const trimmed = amount.trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(trimmed)) return 0;
  const [whole, frac = ''] = trimmed.split('.');
  const cents = Number(whole) * 100 + Number(frac.padEnd(2, '0'));
  return Number.isFinite(cents) ? cents : 0;
}

function formatCents(cents: number): string {
  return (cents / 100).toFixed(2);
}

function parseTimestampMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

export function StudioDashboard({ onOpen }: { onOpen?: (id: string) => void }) {
  const { requests, audit } = useData();

  if (requests.loading) return <Loading label="Loading dashboard…" />;
  if (requests.error && !requests.data) {
    return <ErrorState error={requests.error} onRetry={requests.refresh} />;
  }

  const now = new Date();
  const allRequests = requests.data ?? [];

  // Policy decision counts (authoritative backend decisions)
  const allowRequests = allRequests.filter((r) => r.decision === 'ALLOW');
  const escalateRequests = allRequests.filter((r) => r.decision === 'ESCALATE');
  const blockRequests = allRequests.filter((r) => r.decision === 'BLOCK');

  // Execution outcomes (strictly separated from policy decisions)
  const completedRefunds = allRequests.filter(
    (r) => r.execution_status === 'COMPLETED' && Boolean(r.paypal_refund_id)
  );
  const pendingApprovals = allRequests.filter(
    (r) =>
      r.status === 'AWAITING_APPROVAL' ||
      (r.decision === 'ESCALATE' &&
        r.status !== 'APPROVED' &&
        r.status !== 'DENIED' &&
        r.status !== 'EXPIRED' &&
        r.status !== 'COMPLETED')
  );
  const failedRefunds = allRequests.filter(
    (r) => r.execution_status === 'FAILED' || r.execution_status === 'CANCELLED' || r.status === 'FAILED'
  );
  const unknownRefunds = allRequests.filter(
    (r) =>
      r.execution_status === 'UNKNOWN' ||
      r.execution_status === 'RECONCILING' ||
      r.status === 'UNKNOWN'
  );

  // Financial totals in integer cents (never NaN)
  const requestedCents = allRequests.reduce((sum, r) => sum + parseCents(r.amount), 0);
  const completedCents = completedRefunds.reduce((sum, r) => sum + parseCents(r.amount), 0);
  const blockedCents = blockRequests.reduce((sum, r) => sum + parseCents(r.amount), 0);
  const pendingCents = pendingApprovals.reduce((sum, r) => sum + parseCents(r.amount), 0);
  const failedCents = failedRefunds.reduce((sum, r) => sum + parseCents(r.amount), 0);
  const unknownCents = unknownRefunds.reduce((sum, r) => sum + parseCents(r.amount), 0);

  // Row 2: This month metrics (uses current Date, ignores missing/invalid created_at)
  const thisMonthRequests = allRequests.filter((r) => {
    const ms = parseTimestampMs(r.created_at);
    if (ms === null) return false;
    const d = new Date(ms);
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear();
  });
  const totalTransactionsMonth = thisMonthRequests.length;
  const totalCentsMonth = thisMonthRequests.reduce((sum, r) => sum + parseCents(r.amount), 0);
  // Backend dependency: Vaultline policy engine enforces per-transaction thresholds ($10.00 auto / $25.00 escalation).
  // No monthly limit is defined in the policy schema or exposed by the backend API.
  const backendMonthlyLimit = null as number | null;
  const amountRemaining =
    backendMonthlyLimit !== null ? Math.max(0, backendMonthlyLimit - totalCentsMonth / 100) : null;

  // Row 3: Recent requests (safe timestamp sort, never calls localeCompare on undefined)
  const recentRequests = [...allRequests]
    .sort((a, b) => (parseTimestampMs(b.created_at) ?? 0) - (parseTimestampMs(a.created_at) ?? 0))
    .slice(0, 6);

  const latestAuditTimestamp = audit.data?.events?.[0]?.timestamp ?? null;

  return (
    <div className="card-dashboard">
      {requests.stale && <StaleNote updatedAt={requests.updatedAt} />}

      {/* Row 1: Three cards */}
      <div className="dash-row-1">
        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">REQUESTED</span>
          </div>
          <div className="dash-card-count">{allRequests.length}</div>
          <div className="dash-card-sub">Total value {money(formatCents(requestedCents))}</div>
          <a href="#/requests?filter=ALL" className="dash-card-link">
            View all requests &rarr;
          </a>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">COMPLETED REFUNDS</span>
          </div>
          <div className="dash-card-count">{completedRefunds.length}</div>
          <div className="dash-card-sub">
            {money(formatCents(completedCents))} refunded &middot; {allowRequests.length} allowed by policy
          </div>
          <a href="#/requests?filter=ALLOW" className="dash-card-link">
            View allowed &rarr;
          </a>
        </div>

        <div className="dash-card">
          <div className="dash-card-header">
            <span className="dash-card-title">BLOCKED</span>
          </div>
          <div className="dash-card-count">{blockRequests.length}</div>
          <div className="dash-card-sub">{money(formatCents(blockedCents))} prevented</div>
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
          <span className="dash-strip-value">{money(formatCents(totalCentsMonth))}</span>
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

      {/* Governance row: Decision Breakdown + Audit Health */}
      <div className="dash-row-3">
        <section className="dash-panel" aria-label="Decision Breakdown">
          <div className="dash-panel-head">
            <h3>Decision &amp; execution breakdown</h3>
            <a href="#/requests" className="dash-panel-link">
              All requests &rarr;
            </a>
          </div>
          <div className="table-wrap">
            <table className="dash-table">
              <thead>
                <tr>
                  <th>POLICY / EXECUTION</th>
                  <th className="num">COUNT</th>
                  <th className="num">AMOUNT</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td><DecisionBadge d="ALLOW" /> Allowed by policy</td>
                  <td className="num">{allowRequests.length}</td>
                  <td className="num">
                    {money(formatCents(allowRequests.reduce((s, r) => s + parseCents(r.amount), 0)))}
                  </td>
                </tr>
                <tr>
                  <td><DecisionBadge d="ESCALATE" /> Escalated (policy)</td>
                  <td className="num">{escalateRequests.length}</td>
                  <td className="num">
                    {money(formatCents(escalateRequests.reduce((s, r) => s + parseCents(r.amount), 0)))}
                  </td>
                </tr>
                <tr>
                  <td><DecisionBadge d="BLOCK" /> Blocked by policy</td>
                  <td className="num">{blockRequests.length}</td>
                  <td className="num">{money(formatCents(blockedCents))}</td>
                </tr>
                <tr>
                  <td><Badge tone="ok">COMPLETED</Badge> Refunds executed</td>
                  <td className="num">{completedRefunds.length}</td>
                  <td className="num">{money(formatCents(completedCents))}</td>
                </tr>
                <tr>
                  <td><Badge tone="warn">AWAITING</Badge> Awaiting approval</td>
                  <td className="num">{pendingApprovals.length}</td>
                  <td className="num">{money(formatCents(pendingCents))}</td>
                </tr>
                <tr>
                  <td><Badge tone="bad">FAILED</Badge> Failed / cancelled execution</td>
                  <td className="num">{failedRefunds.length}</td>
                  <td className="num">{money(formatCents(failedCents))}</td>
                </tr>
                <tr>
                  <td><Badge tone="warn">UNKNOWN</Badge> Unknown / reconciling</td>
                  <td className="num">{unknownRefunds.length}</td>
                  <td className="num">{money(formatCents(unknownCents))}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section className="dash-panel" aria-label="Audit Health">
          <div className="dash-panel-head">
            <h3>Audit health</h3>
            <a href="#/activity" className="dash-panel-link">
              Audit trail &rarr;
            </a>
          </div>
          {audit.loading ? (
            <Loading label="Verifying audit chain…" />
          ) : audit.error && !audit.data ? (
            <div className="state state-error" role="alert">
              <Badge tone="warn">Audit unavailable</Badge>
              <p className="muted small">
                {audit.error.message} {audit.error.status ? `(HTTP ${audit.error.status})` : ''}
              </p>
              <button className="btn" onClick={audit.refresh}>
                Retry
              </button>
            </div>
          ) : audit.data ? (
            <div className="dash-details-section">
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                {audit.data.is_valid ? (
                  <Badge tone="ok">Chain verified</Badge>
                ) : (
                  <Badge tone="bad">Verification failed</Badge>
                )}
                {audit.stale && <Badge tone="warn">Stale</Badge>}
              </div>
              <p className="small" style={{ marginBottom: '10px' }}>{audit.data.message}</p>
              <dl className="payload">
                <dt>Total events</dt>
                <dd>{audit.data.total_events}</dd>
                <dt>Latest event</dt>
                <dd>{latestAuditTimestamp ? new Date(latestAuditTimestamp).toLocaleString() : '—'}</dd>
              </dl>
            </div>
          ) : (
            <Empty title="Audit unavailable" hint="No audit verification data returned by backend." />
          )}
        </section>
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
                            if (onOpen) {
                              e.preventDefault();
                              onOpen(r.request_id);
                            }
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
                        if (onOpen) {
                          e.preventDefault();
                          onOpen(r.request_id);
                        }
                      }}
                      title={`View audit trail & transaction details for ${r.request_id}`}
                    >
                      {getCustomerName(r)}
                    </a>
                    <span className="small muted">
                      {parseTimestampMs(r.created_at) !== null ? ago(r.created_at) : '—'}
                    </span>
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
