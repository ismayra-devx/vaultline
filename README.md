
# Vaultline

### AI Financial Security Infrastructure

**Every agent has a limit. Every transaction needs authority.**

Vaultline is a PayPal-first, MCP-native financial authorization gateway that enables AI agents to request financial actions within merchant-defined limits.

AI agents can propose financial actions, but they cannot independently execute money-moving operations beyond their granted authority.

## Core Concept

Vaultline acts as a security gateway between AI agents and PayPal.

Every transaction passes through:
1. Agent authentication
2. Deterministic policy evaluation
3. Risk assessment
4. Merchant approval when required
5. Transaction validation
6. PayPal Sandbox execution
7. Audit logging and status verification

## MVP Scope

- Refund-only workflow
- PayPal Sandbox integration
- USD transactions
- Agent-specific financial policies
- Merchant approval workflow
- MCP-based agent interaction
- Audit trail and transaction monitoring
- AG Grid / AG Studio merchant dashboard

## Policy Model

| Refund amount | Decision |
|---|---|
| Up to $10 | ALLOW |
| Above $10 up to $25 | ESCALATE |
| Above $25 | BLOCK |

Deterministic backend rules are authoritative. AI risk assessment may tighten a decision but must never override a BLOCK into an ALLOW.

## Architecture

Agent → Vaultline MCP → FastAPI Security Gateway → Policy Engine → Approval Workflow → PayPal Adapter → PayPal Sandbox

The merchant dashboard provides visibility and approval controls. It is not the security boundary.

## Technology Stack

- Python
- FastAPI
- Model Context Protocol (MCP)
- PayPal REST API
- SQLite (MVP persistence)
- React + Vite
- AG Grid / AG Studio

## Team Responsibilities

| Role | Responsibility |
|---|---|
| P1 | PayPal integration, hosting and release |
| P2 | Security backend, authentication and policies |
| P3 | MCP server, agent workflow, AI risk and audit |
| P4 | Frontend, merchant dashboard and AG Studio |

## Security Principles

- Agents never receive PayPal credentials.
- Merchant and agent authentication are separate.
- Approval is bound to the exact transaction payload.
- Approval cannot be replayed.
- AI cannot loosen deterministic policy decisions.
- Unknown PayPal outcomes must be reconciled safely.
- Audit logs are tamper-evident, not claimed to be immutable.

## Development

Project setup instructions and environment requirements will be maintained as implementation is completed.

Copy `.env.example` to `.env` and configure your own Sandbox credentials locally.

Never commit `.env`, access tokens or secrets.

## Project Status

Hackathon MVP — under active development.

All integrations and features must be verified against the PayPal Sandbox before being represented as working.
