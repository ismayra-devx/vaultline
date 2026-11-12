# Vaultline Dashboard (merchant control plane)

React + TypeScript + Vite with **AG Studio**. It shows what Vaultline decided and lets an authenticated merchant approve or deny escalated refunds. It is **not** the authorization boundary: every action goes to the backend, which re-validates everything before PayPal is ever called.

Hosted on Render as the `vaultline-dashboard` static site (see `../render.yaml` and `../docs/DEPLOY.md`).

## Screens

| Route | What it shows |
|---|---|
| **Dashboard** (AG Studio) | Custom widgets: **Policy decisions** (ALLOW / ESCALATE / BLOCK, blocked attempts, and *PayPal money-calls on blocked requests* from the backend adapter counter), **Pending approvals** (exact payload, live expiry, Approve / Deny), **Audit & execution health** (hash-chain status, verified PayPal refunds, UNKNOWN outcomes). **Edit dashboard** opens Studio's editor with a `refund_requests` data source. |
| **Requests** | Every request with the policy decision and the PayPal execution status shown separately, the backend's reason, and the PayPal refund ID when one exists. |
| **Approvals** | Escalated refunds with capture, agent, amount, the agent's stated reason, and expiry. Confirming names the exact amount and capture. A duplicate-click guard is in place, and the result is always re-read from the backend. |
| **Activity & audit** | A per-request timeline built from the hash-chained audit log, with the chain verification labelled *tamper-evident, not immutable*. |
| **Analyst** (side panel) | Read-only, deterministic filter and summary over the live records, **labelled "no AI"**. It lists the record IDs it used and cannot approve, deny or execute anything. |

Every view has loading, empty, error and stale-data states, and data polls every 2–4 s (paused in hidden tabs).

## Backend endpoints used

Login is `POST /api/v1/auth/login` with `{username, password}`, which returns `session_token`. The token is kept in `sessionStorage` (this tab only) and sent as `Authorization: Bearer`.

| Purpose | Endpoint |
|---|---|
| Decision details (reasons, refund ID, adapter count, approval ID) | `GET /api/merchant/requests` |
| Agent and capture per request | `GET /api/v1/dashboard/refunds?limit=200` (joined on `request_id`) |
| Approve / deny | `POST /api/v1/approvals/{approval_id}/approve` and `.../deny` |
| Audit chain | `GET /api/v1/audit` |

The browser never holds an agent key or any PayPal credential.

## Configuration (build-time env)

| Variable | Purpose |
|---|---|
| `VITE_API_BASE_URL` | Public https URL of `vaultline-api`. If empty, the login page asks for it (handy for deploy-your-own instances). |
| `VITE_AG_STUDIO_LICENSE_KEY` | AG Studio licence key. It ships to the browser by design. Without it Studio shows a trial watermark. |
| `VITE_DEMO_FIXTURES` | `true` = clearly bannered fixtures for layout work only. Never set it for a real build. |

The API must allow the dashboard's origin: set `VAULTLINE_ALLOWED_ORIGINS=https://<dashboard>.onrender.com` on `vaultline-api`.

## Develop

```bash
npm ci
VITE_DEMO_FIXTURES=true npm run dev     # layout work without a backend
npm run build                            # type-check + production build
```

## Third-party

- **AG Studio** (`ag-studio-react` 3.1.0) is a commercial product, used here under a trial licence for the hackathon. It is not open source. The custom widgets use plain React/CSS only.
- React and Vite are MIT licensed.
