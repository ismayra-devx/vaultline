import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, hasSession, USING_FIXTURES } from './api/client';
import { BrandMark, Loading, SilkBackground } from './components/ui';
import { DataProvider, useData } from './lib/data';
import { ActivityPage, AnalystPanel, ApprovalsPage, LoginPage, RequestsPage } from './pages/pages';

// Studio is heavy; load it only for the dashboard route.
const StudioDashboard = lazy(() => import('./studio/StudioDashboard').then((m) => ({ default: m.StudioDashboard })));

type Route = 'dashboard' | 'requests' | 'approvals' | 'activity';
const ROUTES: { id: Route; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'requests', label: 'Requests' },
  { id: 'approvals', label: 'Approvals' },
  { id: 'activity', label: 'Activity & audit' },
];
const readHash = (): Route => (ROUTES.find((r) => `#/${r.id}` === location.hash.split('?')[0])?.id ?? 'dashboard');

export default function App() {
  const [signedIn, setSignedIn] = useState(hasSession);
  const onAuthError = useCallback(() => setSignedIn(false), []);
  // Route guards are UX only — every merchant route is enforced by the backend.
  return (
    <>
      <SilkBackground />
      {!signedIn ? (
        <LoginPage onDone={() => setSignedIn(true)} />
      ) : (
        <DataProvider onAuthError={onAuthError}>
          <Shell onLogout={() => { api.logout(); setSignedIn(false); }} />
        </DataProvider>
      )}
    </>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [route, setRoute] = useState<Route>(readHash);
  const [focus, setFocus] = useState<string | null>(null);
  const [analyst, setAnalyst] = useState(false);
  const { requests } = useData();
  useEffect(() => {
    const h = () => setRoute(readHash());
    addEventListener('hashchange', h);
    return () => removeEventListener('hashchange', h);
  }, []);
  const pending = (requests.data ?? []).filter((r) => r.status === 'AWAITING_APPROVAL').length;
  // The live indicator rings briefly whenever the backend reports something new.
  const signature = JSON.stringify((requests.data ?? []).map((r) => [r.request_id, r.status, r.execution_status]));
  const [fresh, setFresh] = useState(false);
  const last = useRef<string | null>(null);
  useEffect(() => {
    if (!requests.data) return;
    if (last.current !== null && last.current !== signature) {
      setFresh(true);
      const t = setTimeout(() => setFresh(false), 900);
      last.current = signature;
      return () => clearTimeout(t);
    }
    last.current = signature;
  }, [signature, requests.data]);

  return (
    <div className="app">
      {USING_FIXTURES && <div className="fixture-banner" role="alert">DEMO FIXTURES — not live backend data. Unset VITE_DEMO_FIXTURES for the real build.</div>}
      <header className={`topbar ${fresh ? 'fresh' : ''}`}>
        <div className="brand"><BrandMark /><span className="brand-name">Vaultline</span><span className="tag">Every agent has a limit.</span></div>
        <nav aria-label="Main">
          {ROUTES.map((r) => (
            <a key={r.id} href={`#/${r.id}`} className={route === r.id ? 'on' : ''} aria-current={route === r.id ? 'page' : undefined}>
              {r.label}{r.id === 'approvals' && pending > 0 && <span className="count">{pending}</span>}
            </a>
          ))}
        </nav>
        <div className="who">
          
          <button className="link" onClick={() => setAnalyst((v) => !v)} aria-expanded={analyst}>Analyst</button>
          <span className="who-name">Merchant</span>
          <button className="link" onClick={onLogout}>Sign out</button>
        </div>
      </header>
      <div className={`body ${analyst ? 'with-analyst' : ''}`}>
        <main className="content">
          <h1 className="sr-only">{ROUTES.find((r) => r.id === route)!.label}</h1>
          {route === 'dashboard' && (
            <Suspense fallback={<Loading label="Loading AG Studio…" />}>
              <StudioDashboard />
            </Suspense>
          )}
          {route === 'requests' && <RequestsPage onOpen={(id) => { setFocus(id); location.hash = '#/activity'; }} />}
          {route === 'approvals' && <ApprovalsPage />}
          {route === 'activity' && <ActivityPage key={focus ?? ''} focus={focus} />}
        </main>
        {analyst && <AnalystPanel />}
      </div>
    </div>
  );
}

