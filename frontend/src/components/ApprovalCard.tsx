import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ApiError, RefundRequest } from '../api/types';
import { useData } from '../lib/data';
import { isExpired, money, STATUS_TONE, time } from '../lib/format';

const statusLabel = (s: string) => s.charAt(0) + s.slice(1).toLowerCase().replaceAll('_', ' ');
import { Badge, ExecBadge } from './ui';

/**
 * Shows the exact payload being authorised. Approve/Deny call the backend's authenticated
 * approval routes; the backend re-validates expiry, single use, payload binding and policy and
 * is the only authority. We never claim a refund happened until the backend reports PayPal
 * COMPLETED with a refund id, and we re-read backend state after every action.
 */
export function ApprovalCard({ r, compact = false }: { r: RefundRequest; compact?: boolean }) {
  const { requests } = useData();
  const [confirming, setConfirming] = useState<null | 'approve' | 'deny'>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ tone: string; text: string } | null>(null);
  const [, tick] = useState(0);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 1000); // live expiry countdown
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (confirming) dialogRef.current?.showModal();
    else dialogRef.current?.close();
  }, [confirming]);

  const expired = isExpired(r.expires_at);
  const pending = r.status === 'AWAITING_APPROVAL';
  const actionable = pending && !!r.approval_id && !expired;
  const secsLeft = r.expires_at ? Math.max(0, Math.round((new Date(r.expires_at).getTime() - Date.now()) / 1000)) : null;

  async function run(kind: 'approve' | 'deny') {
    if (busy || !r.approval_id) return; // duplicate-click guard (the backend nonce is the real guard)
    setBusy(true);
    setResult(null);
    try {
      const updated = kind === 'approve' ? await api.approve(r.approval_id) : await api.deny(r.approval_id);
      await requests.refresh();
      const refundId = updated.paypal_refund_id ?? updated.refund_id;
      if (kind === 'deny') setResult({ tone: 'muted', text: 'Denied. No refund was executed.' });
      else if (updated.execution_status === 'COMPLETED' && refundId) setResult({ tone: 'ok', text: `Approved. PayPal refund ${refundId} verified COMPLETED.` });
      else setResult({ tone: 'warn', text: `Approval accepted. PayPal status: ${updated.execution_status ?? 'unknown'}. Not yet a confirmed refund.` });
    } catch (e) {
      const err = e as ApiError;
      setResult({
        tone: 'bad',
        text:
          err.status === 401 || err.status === 403
            ? 'Not authorised. Sign in again as the merchant.'
            : `${err.message || 'Action failed.'} No refund was executed by this action.`,
      });
      await requests.refresh();
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  return (
    <article className={`approval ${compact ? 'compact' : ''}`} aria-label={`Approval for ${r.request_id}`}>
      <header className="approval-head">
        <div>
          <div className="amount">{money(r.amount, r.currency)}</div>
        </div>
        <div className="approval-meta">
          <Badge tone={actionable ? 'warn' : STATUS_TONE(String(r.status))}>{pending && expired ? 'Expired' : statusLabel(String(r.status))}</Badge>
          <ExecBadge s={r.execution_status} />
        </div>
      </header>
      <dl className="payload">
        <dt>Capture</dt><dd className="mono">{r.capture_id ?? '—'}</dd>
        <dt>Currency</dt><dd>{r.currency}</dd>
        {!compact && r.agent_reason && (<><dt>Reason</dt><dd>“{r.agent_reason}”</dd></>)}
        {r.paypal_refund_id && (<><dt>PayPal refund</dt><dd className="mono">{r.paypal_refund_id}</dd></>)}
        <dt>Expires</dt>
        <dd>
          {r.expires_at && pending ? (expired ? <span className="tone-text-bad">expired {time(r.expires_at)}</span> : `${Math.floor(secsLeft! / 60)}m ${secsLeft! % 60}s`) : '—'}
        </dd>
      </dl>
      {!compact && r.reasons.length > 0 && <p className="small">{r.reasons.join(' ')}</p>}
      {pending && (
        <div className="actions">
          <button className="btn btn-primary" disabled={!actionable || busy} onClick={() => setConfirming('approve')}>Approve</button>
          <button className="btn" disabled={!actionable || busy} onClick={() => setConfirming('deny')}>Deny</button>
        </div>
      )}
      {result && <p className={`result tone-text-${result.tone}`} role="status">{result.text}</p>}

      <dialog ref={dialogRef} className="dialog" onClose={() => setConfirming(null)}>
        <h3>{confirming === 'approve' ? 'Authorise this exact refund?' : 'Deny this refund?'}</h3>
        <p>
          <strong>{money(r.amount, r.currency)}</strong> from capture <span className="mono">{r.capture_id ?? '—'}</span>.
        </p>
        {confirming === 'approve' && (
          <p className="muted small">Approval is bound to this exact payload and can be used once. Vaultline re-checks it and the current policy right before calling PayPal.</p>
        )}
        <div className="actions">
          <button className="btn" onClick={() => setConfirming(null)} disabled={busy}>Cancel</button>
          <button className={`btn ${confirming === 'approve' ? 'btn-primary' : 'btn-danger'}`} onClick={() => run(confirming!)} disabled={busy} autoFocus>
            {busy ? 'Working…' : confirming === 'approve' ? `Approve ${money(r.amount)}` : 'Deny'}
          </button>
        </div>
      </dialog>
    </article>
  );
}
