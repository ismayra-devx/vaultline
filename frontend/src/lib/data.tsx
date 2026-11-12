import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { api } from '../api/client';
import { ApiError, type AuditResponse, type RefundRequest } from '../api/types';

/** 2-second polling of authoritative backend state (Master Plan: no WebSockets). */
export interface Polled<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean; // first load only
  stale: boolean; // last poll failed but we still show the previous good data
  updatedAt: number | null;
  refresh: () => Promise<void>;
}

function usePoll<T>(fn: () => Promise<T>, ms: number, onAuthError: () => void): Polled<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const inflight = useRef(false);
  const refresh = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    try {
      const d = await fn();
      setData(d);
      setError(null);
      setUpdatedAt(Date.now());
    } catch (e) {
      const err = e instanceof ApiError ? e : new ApiError(0, 'UNKNOWN', String(e));
      setError(err);
      if (err.status === 401) onAuthError();
    } finally {
      inflight.current = false;
    }
  }, [fn, onAuthError]);
  useEffect(() => {
    refresh();
    const t = setInterval(() => document.visibilityState === 'visible' && refresh(), ms);
    return () => clearInterval(t);
  }, [refresh, ms]);
  return { data, error, loading: data === undefined && error === null, stale: !!error && data !== undefined, updatedAt, refresh };
}

interface DataCtx {
  requests: Polled<RefundRequest[]>;
  audit: Polled<AuditResponse>;
}

// External store: AG Studio may mount custom widgets outside our React context tree,
// so widgets read the same polled state through useSyncExternalStore.
const initial = <T,>(): Polled<T> => ({ data: undefined, error: null, loading: true, stale: false, updatedAt: null, refresh: async () => {} });
let snapshot: DataCtx = { requests: initial(), audit: initial() };
const listeners = new Set<() => void>();
const store = {
  set(v: DataCtx) {
    snapshot = v;
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => listeners.delete(l);
  },
  get: () => snapshot,
};

export function DataProvider({ children, onAuthError }: { children: ReactNode; onAuthError: () => void }) {
  const requests = usePoll(api.listRequests, 2000, onAuthError);
  const audit = usePoll(api.audit, 4000, onAuthError);
  const value = useMemo(() => ({ requests, audit }), [requests, audit]);
  useLayoutEffect(() => store.set(value), [value]);
  return <>{children}</>;
}
export const useData = (): DataCtx => useSyncExternalStore(store.subscribe, store.get);
