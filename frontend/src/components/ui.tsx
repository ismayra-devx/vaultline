import type { ReactNode } from 'react';
import type { ApiError, Decision, ExecutionStatus } from '../api/types';
import { DECISION_TONE, execLabel } from '../lib/format';

export const Badge = ({ tone, children }: { tone: string; children: ReactNode }) => <span className={`badge tone-${tone}`}>{children}</span>;
export const DecisionBadge = ({ d }: { d: Decision }) => <Badge tone={DECISION_TONE[d]}>{d}</Badge>;
export const ExecBadge = ({ s }: { s: ExecutionStatus }) => <Badge tone={execLabel(s).tone}>{execLabel(s).label}</Badge>;

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden /> {label}
    </div>
  );
}
export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {hint && <p className="muted">{hint}</p>}
    </div>
  );
}
export function ErrorState({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Couldn’t load from the backend</strong>
      <p className="muted">
        {error.message} {error.status ? `(HTTP ${error.status})` : ''}
      </p>
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
export function StaleNote({ updatedAt }: { updatedAt: number | null }) {
  return (
    <div className="stale" role="status">
      Connection problem — showing last good data{updatedAt ? ` from ${new Date(updatedAt).toLocaleTimeString()}` : ''}. Retrying every 2s.
    </div>
  );
}
