# Zyvello: Public signup, subscription-only features (decision 2026-11-12)

## Product decision

Zyvello is a public self-service business-outreach SaaS, not an invitation-only
product. A verified email can register without permission, create a starter
workspace, and explore a read-only dashboard and pricing. There is **no free
usage** of operational capabilities. A free account is not a free trial.

## Authorization contract

* Free: account, sign-in/out, password recovery, workspace shell and metadata,
  basic UI walkthrough, plan and billing views, and checkout initiation.
* Paid: contacts, campaigns, recipient assignment, permissions evidence,
  background jobs, AI credential operations, generated drafts, and all other
  operational workspace capabilities. Both reads and writes are denied when
  unpaid, as the user requested.
* The backend independently verifies workspace membership, a verified checkout
  attempt, the signed provider webhook's captured-payment count, status
  `active`, and `paid_through > now()`. Never use client-side flags, an email,
  a plan name, a URL parameter, or a payment callback alone to confer access.
* An unpaid or expired workspace receives 402 for protected endpoints. A
  request to another tenant's workspace receives 403/404 instead of disclosing
  that tenant's payment state.
* Payment entitlement is workspace-scoped. Existing data is retained but
  inaccessible to non-paying members, except metadata and billing.

## Staging implementation

`HostedStore.has_paid_entitlement` checks the signed-webhook-backed billing
ledger through the non-owner API database role with RLS and a workspace
membership prerequisite. `require_paid_workspace` guards all operational API
routes. The subscription check is always enabled in hosted deployments;
unit-test fakes must opt out explicitly to exercise unrelated contracts.
The frontend requests `/api/v1/workspaces/{id}/entitlement`, shows a premium
feature preview to free users, and skips protected data requests until paid.
Billing remains accessible without a subscription.

The `ZYVELLO_ENABLE_NEW_WORKSPACES` server flag and
`VITE_ZYVELLO_ENABLE_SIGNUP=true` /
`VITE_ZYVELLO_ENABLE_NEW_WORKSPACES=true` frontend flags control phased
self-service onboarding. Public signup should be switched on only after the
server-side paywall is deployed and checked, not independently of it.

## Still gated for launch

Current Razorpay integration is TEST MODE only: no real subscription revenue.
Actual production billing, renewal/cancellation testing, payment reconciliation,
refund/tax/policy review, provider security, AI delivery, compliant outreach,
rate limiting, anti-abuse controls, and backup/restore readiness remain release
gates. Showing a premium feature in the UI does not mean that service is
operational. Never switch on real charges, customer emailing, or AI execution
simply because a user owns a paid subscription.
