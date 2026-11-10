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

/** Vaultline "V" monogram: two angled strokes with a lighter inner facet. */
export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden>
      <svg viewBox="0 0 48 48" fill="none">
        <path d="M5 7h10l9 15 9-15h10L24 42 5 7Z" stroke="currentColor" strokeWidth="5" strokeLinejoin="round" />
        <path d="M17 7h8L15 25" stroke="#D8BA98" strokeWidth="3" strokeLinecap="round" />
      </svg>
    </span>
  );
}

/* Small inline icons (decorative; the adjacent text carries the meaning). */
export const SparkleIcon = () => (
  <svg className="ico ico-sparkle" viewBox="0 0 24 24" aria-hidden><path d="M12 2c.8 6 4 9.2 10 10-6 .8-9.2 4-10 10-.8-6-4-9.2-10-10 6-.8 9.2-4 10-10z" fill="currentColor" /></svg>
);
export const InfoIcon = () => (
  <svg className="ico ico-info" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="11" fill="#7A1420" /><circle cx="12" cy="7.4" r="1.4" fill="#fff" /><path d="M12 11v6.5" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" /></svg>
);
export const ClockIcon = () => (
  <svg className="ico" viewBox="0 0 24 24" aria-hidden fill="none" stroke="#7A1420" strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></svg>
);
export const ShieldIcon = () => (
  <svg className="ico ico-title" viewBox="0 0 24 24" aria-hidden fill="none" stroke="#7A1420" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 3l7.5 3v5.5c0 4.6-3.2 7.9-7.5 9.5-4.3-1.6-7.5-4.9-7.5-9.5V6z" /><path d="M8.8 12.2l2.3 2.3 4.2-4.6" /></svg>
);
export const CheckCircleIcon = () => (
  <svg className="ico" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="11" fill="#7A1420" /><path d="M7.5 12.3l3 3 6-6.3" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
);
export const QuestionCircleIcon = () => (
  <svg className="ico" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="11" fill="#E4CDB4" /><path d="M9.4 9.3a2.7 2.7 0 1 1 3.8 2.5c-.8.4-1.2 1-1.2 1.8v.6" fill="none" stroke="#7A1420" strokeWidth="2" strokeLinecap="round" /><circle cx="12" cy="17.2" r="1.2" fill="#7A1420" /></svg>
);
export const AlertCircleIcon = () => (
  <svg className="ico" viewBox="0 0 24 24" aria-hidden><circle cx="12" cy="12" r="11" fill="#E4CDB4" /><path d="M12 6.8v6.4" stroke="#7A1420" strokeWidth="2.2" strokeLinecap="round" /><circle cx="12" cy="16.9" r="1.3" fill="#7A1420" /></svg>
);

/** Fixed background: alabaster page with a large midnight field, a tan field and a maroon swoosh. */
export function SilkBackground() {
  return <div className="page-bg" aria-hidden />;
}
