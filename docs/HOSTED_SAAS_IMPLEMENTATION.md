# Hosted SaaS implementation — working architecture

Status: implementation baseline, not a release approval.

## Product decisions
- Product: Zyvello, India-first hosted SaaS.
- Budget target: ₹0/month for the initial build and private beta.
- The existing public site at https://www.zyvello.pro/ remains on its current deployment until a replacement is verified.
- Preserve the existing local/self-hosted edition.
- Preserve the existing Groq, Claude and Codex provider implementations and provider-specific behaviour. Do not rewrite, remove, rename, or silently change those adapters as part of hosted conversion.
- Hosted customer credentials and provider authorization must be isolated per workspace. Never put a customer's provider key in the repository, frontend bundle, shared process environment, or another customer's configuration.
- Email delivery integrations are separate from AI providers; preserve their existing support and controls.

## What exists today
- The repository is a Python 3.11+ FastAPI application with a React 19 / Vite / TypeScript frontend.
- The frontend package currently builds a static marketing site. Its /login and /signup UI calls /api/auth/session, /api/auth/login, and /api/auth/signup, but static hosting alone does not provide those API routes.
- The current account implementation is a local, single-owner model (owner.id = 1) backed by SQLite.
- Main application state is stored in a local SQLite database at data/harvey.db; configuration and credentials are read from local files such as .env and harvey.yaml.
- The dashboard's process controls use local subprocess/PID/log paths. The heartbeat (harvey.main) is designed around one local installation.
- The current AI adapters include Groq, Claude and Codex. Preserve these capabilities; they are not targets for a provider rewrite.

## Target architecture

### Public web application
- Keep React/Vite as the frontend stack.
- Serve the marketing site and authenticated application from a verified HTTPS origin.
- Replace the current demo-only root route with real application routing only after backend endpoints are ready.
- The frontend never receives database credentials, signing secrets, or decrypted provider keys.

### Hosted API and identity
- Keep FastAPI.
- Add a hosted API boundary separate from the local-only dashboard security middleware.
- Use PostgreSQL for hosted identity, workspaces, memberships, encrypted integration credentials, billing records, usage, and durable job metadata.
- Implement email verification, password reset, secure server-managed sessions, logout/revocation, CSRF/origin protections, rate limiting, and account recovery before public registration is enabled.
- A user belongs to one or more workspaces through memberships. Enforce workspace membership and role checks on every workspace-owned API request.
- Never trust a workspace_id supplied by the browser without verifying membership server-side.

### Workspace data and existing engine
- All hosted companies, prospects, campaigns, conversations, feedback, actions, settings, and exports must be tenant/workspace scoped.
- Preserve the current local SQLite storage path and migrations for local users.
- Never point multiple hosted customers at one shared data/harvey.db.
- Hosted worker execution must have an explicit workspace context and isolated configuration, secrets and state. The current process-wide .env, YAML and project-root assumptions must not be reused as a shared customer store.
- Before unattended outreach, add durable job leases/checkpoints, retry policy, idempotency for outbound actions, and recovery after worker restarts.
- Keep approval mode and existing send limits/opt-out protections as defaults during beta.

### AI provider credentials
- Preserve the current Groq, Claude and Codex adapters unchanged in this conversion.
- Build a hosted credential/authorization boundary around them only after provider-specific auth requirements are mapped. Groq API keys are secrets; Claude/Codex subscription authorization may use different secure flows and must not be treated as ordinary API-key fields.
- Store credentials encrypted at rest using a managed secret/key-encryption design; decrypt only in the isolated workspace execution context. Never return plaintext secrets after save.
- Validate credentials with provider-native preflight calls and show masked status only.
- Do not introduce a new provider or change model defaults/fallback behaviour in this infrastructure phase.

## Delivery sequence and release gates
1. Repository and deployment baseline: record actual static host, DNS, deployment artifact and API routing; keep production domain and main untouched; add staging build/test/deploy checks.
2. Hosted data and identity: add PostgreSQL schema and versioned migrations for users, workspaces, memberships, sessions, verification/reset tokens and integration credentials. Acceptance: two workspaces cannot read or mutate each other's records, even by guessing IDs.
3. Frontend/API connection: wire signup/login/session/logout to hosted API; add verification and password reset. Acceptance: browser tests cover signup, verification, login, refresh, logout, expired session and cross-workspace denial.
4. Workspace application data: move hosted dashboard reads/writes behind workspace-scoped API services while keeping local SQLite routes working. Acceptance: tenant-isolation tests cover every resource and export route.
5. AI integrations and worker runtime: reuse existing provider adapters without changing provider behaviour; add encrypted per-workspace credentials/authorizations and durable queue/worker execution. Acceptance: two workspaces run independent test jobs without cross-tenant credentials, prompts, logs, limits or results.
6. Reliability and operations: test reply ingestion recovery, outbound idempotency, retries, rate limits, opt-outs, backups and restore. Acceptance: worker restart and partial-failure tests do not lose replies or duplicate sends.
7. Website polish and public launch: only after real application flows pass, polish marketing/product UI. Billing is a later gated phase; no public checkout or paid-plan claims until subscription lifecycle is verified.

## Zero-cost hosting constraints
- Free VPS does not automatically mean reliable production SLA. Free compute may have resource ceilings, inactivity policies, capacity limits, or verification requirements.
- Keep the current domain unchanged until staging passes health, database, auth, worker and restore checks.
- Do not put production data or customer credentials on an unverified free host.
- If the free tier cannot run API and worker reliably, use it for a private beta only and document the limitation rather than claiming production readiness.

## Explicit non-goals for this phase
- No visual redesign before functional integration.
- No provider adapter rewrite or provider removal.
- No production DNS switch, live deployment, or public checkout.
- No migration or deletion of local customer data without a tested export/backup/rollback path.
- No real outreach sends during automated tests.

## Confirmed current hosting: Hostinger Business Web Hosting

Hostinger's current official support documentation confirms:
- Business Web Hosting supports managed Node.js web applications, including React/Vite frontends and supported Node.js backends.
- Python, FastAPI, Django and Flask require VPS Hosting; Hostinger Web/Cloud hosting does not provide the root access needed for Python runtimes and dependencies.
- Managed Node.js Web/Cloud apps do not support inbound WebSockets or always-running background workers. Requests have a 120-second timeout. Node.js cron jobs are available, but each scheduled run stops after 30 minutes.
- Therefore, the current Hostinger Business plan is suitable to retain the existing public frontend, but it is not a supported home for this repository's FastAPI API or its persistent Python automation worker.

Deployment decision:
1. Keep `zyvello.pro` and its current Hostinger deployment unchanged.
2. Do not attempt to convert the Python engine into a Node.js rewrite just to fit the shared hosting plan.
3. Evaluate a separate free/low-cost runtime for the FastAPI API and worker, plus managed PostgreSQL. Treat any free tier as staging/private-beta infrastructure until sleep, capacity, persistence, backups, security and usage limits are verified.
4. Keep DNS and production traffic unchanged until staging tests pass and the user explicitly approves a deployment.
5. Hostinger may optionally serve a separate Node.js frontend/API gateway later, but it must not become a workaround that exposes local SQLite, process-wide secrets or worker state to multiple customers.

Official Hostinger references reviewed 9 November 2026:
- https://www.hostinger.com/support/which-programming-languages-and-frameworks-are-supported-at-hostinger/
- https://www.hostinger.com/support/hostinger-node-js-limits-for-web-and-cloud-plans/
- https://www.hostinger.com/support/how-to-run-cron-jobs-for-node-js-websites-at-hostinger/
