# Zyvello launch readiness — 6 November 2026

## Decision

The local product passes the checks below. **Do not open paid public checkout yet.**
The application is still a single-installation, single-owner local edition, rather
than an isolated multi-customer hosted service. No deployment or live outreach was
performed in this review.

## Implemented in this review

- Integrated the supplied sidebar interaction pattern with the existing Vite / React
  / TypeScript project. Component: `frontend/src/components/ui/sidebar.tsx`;
  mounting adapter: `frontend/src/sidebar-entry.tsx`. Existing `@/components/ui`
  and `@/styles` paths, Tailwind, Framer Motion, Lucide and `cn` were already present.
  Native anchors replace Next.js-only navigation; no second framework was added.
- Desktop open/close toggle with a persistent icon rail, accessible names and
  tooltips. Original tabs, counts, handlers, account access and session controls
  remain mounted. Small screens use a drawer with backdrop, Escape, keyboard focus
  containment, restored opener focus and automatic closing after navigation.
  Reduced-motion preferences are respected.
- SMTP/IMAP inbox exceptions and negative select/search responses now propagate
  to the existing handler sending hold, rather than looking like an empty inbox.
  Added a bounded IMAP connection timeout. Regression tests cover failure
  propagation, negative server responses, cleanup and successful empty reads.
- Added `.dockerignore` to exclude local credentials, tokens, mailbox/account
  databases, generated product knowledge, and local dependencies from the Docker
  build context. Existing Git exclusions remain in place. Do not publish an image
  containing a customer-specific `harvey.yaml`; the current Dockerfile copies that
  configuration into the image and Compose mounts it at runtime.

## Verification

- TypeScript and Vite production build: pass.
- npm dependency audit, including development dependencies: zero known vulnerabilities.
- Installed Python dependency metadata: 35 distributions, 36 active requirements,
  no missing or incompatible requirements. The review environment has no pip
  module, so this check used installed package metadata rather than `pip check`.
- JavaScript syntax, parsing all 54 Python modules, CLI help from project root: pass.
- Full Windows regression run: **1,497 passed, 184 skipped, 10 warnings**.
  This run began before the SMTP hardening patch and before the four new test
  cases were collected. The final patch was then checked with the SMTP/header/
  sending-hold suites: **28 passed**, including those four new cases.
- Browser: all 13 original tabs plus Account open successfully, no console errors.
  Checked desktop collapse/expand, persistence after reload, mobile drawer,
  keyboard cycling, Escape, automatic dismissal, 320px and 390px content fit.
- No real AI calls, mailbox authorization, payments, sends or replies were tested.
- Docker build/runtime, hosted Linux operation and deployment were not verified.

## Remaining release gates

### 1. Durable reply processing — required before unattended outreach

`harvey/agents/handler.py` still has paths where an unexpected exception reaches
a `finally` block that marks a reply processed. Durable processing checkpoints
and outbound idempotency are required before changing this behavior: simply
retrying could duplicate conversations, queued replies or remote sends. Verify
crashes at every persistence/send boundary, with opt-outs included.

SMTP still uses UNSEEN/RFC822 fetching, which can mark a message seen before
successful processing. Add durable ingestion, stable provider identifiers and
acknowledgement after successful handling. Verify fetch and partial-batch failure,
restart recovery, backlog limits, and malformed messages. The new error
propagation fixes the silent-read-failure path only.

### 2. Hosted foundation — required before multiple paying customers

The current account database enforces one owner (`id = 1`), and prospect data,
credentials, configuration, sending state and jobs belong to one installation.
Implement workspace-scoped storage and authorization, encrypted per-customer
credentials, durable jobs with leases/retries, quota enforcement, migrations,
backups and a tested restore procedure. Verify two workspaces cannot access each
other's data, credentials, queued work, exports or mailbox callbacks.

The server deliberately binds only to loopback and disables proxy headers. Build
and test a hosted HTTPS/authentication deployment path with explicit trusted
origins, cookie/proxy configuration and OAuth redirects. Do not remove the local
security checks as a shortcut to exposing this installation publicly.

### 3. Real integrations and business setup

At review time: Claude is selected; no Groq key, Gmail OAuth application,
Microsoft application, SMTP credentials, email-verification key or generated
product-knowledge file is present. Existing provider options remain available.
Choose/configure the actual production integrations and verify controlled
end-to-end discovery, website reading, verification, drafting, approval, sending,
reply threading, opt-outs, bounce handling, reconnect and rate-limit recovery.
Complete applicable OAuth application/provider checks.

### 4. Subscription and customer lifecycle

Connect hosted identity and profile editing, subscription billing, verified
webhooks, entitlement checks, cancellation, renewal/failure handling and invoices.
The Account page accurately says billing is not connected and display name is
not set; it does not simulate a paid plan. Test the billing lifecycle in staging
before displaying an active subscription or opening public checkout.

### 5. Operations and final launch rehearsal

Provision the chosen host and durable storage; configure `zyvello.pro`, HTTPS,
secrets, worker supervision, monitoring and alerts. Establish a backup/restore
and rollback procedure. Run a staged customer journey with controlled mailboxes
and payment test mode, then perform a production smoke test before launch.
Keep approval mode until actual delivery, reply recovery and isolation gates pass.

## Preserved scope

No Git connection was created, no public server was exposed, and no feature or
integration was removed. Local package/command names, environment variables,
database/config paths and existing API routes remain compatible. This report
updates the SMTP read-error finding in the earlier root audit; other launch
blockers there remain open.
