import type { AuditEvent, RefundRequest } from '../api/types';
import { money, time, getCustomerName } from '../lib/format';
import { DecisionBadge, ExecBadge } from './ui';

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

export function TransactionInformation({
  request,
  events = [],
  onClose,
}: {
  request: RefundRequest;
  events?: AuditEvent[];
  onClose?: () => void;
}) {
  const reqEvents = events.filter((e) => e.request_id === request.request_id);

  return (
    <div className="transaction-info-panel" tabIndex={-1}>
      <div className="transaction-info-head">
        <div>
          <h3>Transaction Information</h3>
          <span className="muted small">
            {getCustomerName(request)} · <span className="req-serif">{request.request_id}</span>
          </span>
        </div>
        {onClose && (
          <button type="button" className="btn btn-ghost small" onClick={onClose} aria-label="Close transaction info">
            Hide details &times;
          </button>
        )}
      </div>

      <div className="req-summary">
        <div>
          <span className="muted small">Customer</span>
          <br />
          <strong>{getCustomerName(request)}</strong>
        </div>
        <div>
          <span className="muted small">Amount</span>
          <br />
          <span className="amount">{money(request.amount, request.currency)}</span>
        </div>
        <div>
          <span className="muted small">Policy decision</span>
          <br />
          <DecisionBadge d={request.decision} />
        </div>
        <div>
          <span className="muted small">PayPal execution</span>
          <br />
          <ExecBadge s={request.execution_status} />
        </div>
        {request.paypal_refund_id && (
          <div>
            <span className="muted small">Refund ID</span>
            <br />
            <span className="mono">{request.paypal_refund_id}</span>
          </div>
        )}
        <div>
          <span className="muted small">Capture ID</span>
          <br />
          <span className="mono">{request.capture_id ?? '—'}</span>
        </div>
        <div>
          <span className="muted small">Agent ID</span>
          <br />
          <span className="mono">{request.agent_id ?? '—'}</span>
        </div>
        <div>
          <span className="muted small">Adapter calls</span>
          <br />
          <strong>{request.adapter_call_count}</strong>
        </div>
      </div>

      {request.agent_reason && (
        <p className="small muted transaction-meta-note">
          <strong>Agent reason:</strong> &ldquo;{request.agent_reason}&rdquo;
        </p>
      )}

      {request.reasons && request.reasons.length > 0 && (
        <p className="small muted transaction-meta-note">
          <strong>Decision rationale:</strong> {request.reasons.join(' ')}
        </p>
      )}

      {reqEvents.length > 0 && (
        <div className="transaction-audit-trail">
          <div className="muted small transaction-audit-title">Audit trail ({reqEvents.length} events)</div>
          <ol className="timeline">
            {reqEvents.map((e) => (
              <li key={e.id} className={`ev ev-${e.event_type}`}>
                <div className="ev-head">
                  <strong>{e.event_type.replaceAll('_', ' ')}</strong>
                  <span className="muted small">
                    #{e.id} · {time(e.timestamp)} · {e.actor_type}
                    {e.actor_id ? `:${e.actor_id}` : ''}
                  </span>
                </div>
                <div className="small mono">{eventDetail(e.payload)}</div>
                <div className="mono tiny muted">
                  prev {e.prev_hash} &rarr; {e.hash}
                </div>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
