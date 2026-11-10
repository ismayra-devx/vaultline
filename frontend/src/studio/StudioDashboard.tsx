import { useMemo, useState } from 'react';
import { AgStudio, AgStudioProvider, createWidgets } from 'ag-studio-react';
import { useData } from '../lib/data';
import { ApprovalQueueWidget, AuditHealthWidget, DecisionBreakdownWidget } from './widgets';

// AG Studio licence keys are designed to ship in client bundles (not a secret).
// Without a key Studio runs with a watermark — fine for local dev, set it for the judge build.
const LICENSE = import.meta.env.VITE_AG_STUDIO_LICENSE_KEY as string | undefined;

const SHIELD = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/></svg>`;

// Widgets read live backend state directly, so they need no data mapping or edit-panel form.
const def = (id: string, label: string, comp: () => React.JSX.Element, w: number, h: number) => ({
  id, label, icon: SHIELD, comp,
  form: () => [],
  defaultSize: { width: w, height: h },
  minSize: { width: 4, height: 3 },
  toolbar: ['duplicate', 'delete'],
});
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const customWidgets: any[] = [
  def('vaultlineDecisions', 'Decision breakdown', DecisionBreakdownWidget, 6, 7),
  def('vaultlineApprovals', 'Approval queue', ApprovalQueueWidget, 6, 7),
  def('vaultlineAudit', 'Audit health', AuditHealthWidget, 12, 3),
];

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const widgets = (cfg: any) =>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  createWidgets<any>({
    additionalTypes: customWidgets,
    menu: [{ label: 'Vaultline', widgetIds: customWidgets.map((w) => w.id) }, ...cfg.menu],
  });

const initialState = {
  selectedPageId: 'overview',
  pages: [
    {
      id: 'overview',
      name: 'Security overview',
      widgets: {
        decisions: { type: 'vaultlineDecisions' },
        approvals: { type: 'vaultlineApprovals' },
        audit: { type: 'vaultlineAudit' },
      },
      layout: {
        columns: 12, rowHeight: 48, widgetPadding: 0,
        backgroundColor: '#0b0f14', widgetBackgroundColor: '#121821',
        widgetBorderEnabled: true, widgetBorderColor: '#243041', widgetBorderWidth: 1, widgetBorderRadius: 10,
      },
      widgetLayout: {
        decisions: { xTrack: 0, yTrack: 0, xSpan: 6, ySpan: 7 },
        approvals: { xTrack: 6, yTrack: 0, xSpan: 6, ySpan: 7 },
        audit: { xTrack: 0, yTrack: 7, xSpan: 12, ySpan: 3 },
      },
    },
  ],
};

export function StudioDashboard() {
  const { requests } = useData();
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  // Flat rows so analysts can build extra built-in charts/grids. amount_usd is display/analytics only.
  const rowsKey = JSON.stringify((requests.data ?? []).map((r) => [r.request_id, r.decision, r.execution_status, r.status]));
  const data = useMemo(
    () => ({
      sources: [
        {
          id: 'refund_requests',
          name: 'Refund requests',
          data: (requests.data ?? []).map((r) => ({
            request_id: r.request_id,
            agent_id: r.agent_id,
            decision: r.decision,
            amount_usd: Number(r.amount),
            reasons: r.reasons.join(', '),
            execution_status: r.execution_status,
            paypal_refund_id: r.paypal_refund_id ?? '',
            created_at: new Date(r.created_at),
          })),
        },
      ],
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rowsKey],
  );

  return (
    <AgStudioProvider licenseKey={LICENSE}>
      <div className="studio-bar">
        <span className="muted small">AG Studio · custom Vaultline widgets on live backend data</span>
        <button className="btn" aria-pressed={mode === 'edit'} onClick={() => setMode((m) => (m === 'view' ? 'edit' : 'view'))}>
          {mode === 'view' ? 'Edit dashboard' : 'Done editing'}
        </button>
      </div>
      <div className="studio-host">
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <AgStudio {...({ data, widgets, initialState, mode } as any)} />
      </div>
    </AgStudioProvider>
  );
}
