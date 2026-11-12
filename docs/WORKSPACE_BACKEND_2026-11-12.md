# Zyvello hosted business workspace backend — 12 November 2026

## Scope and deployment decision

Implementation is on **work/hosted-workspaces-20261112**, branched from the actual
hosted SaaS branch `hosted-saas-foundation`. Its migration
`hosted/migrations/0010_workspace_management.sql` was tested on an **isolated**
Neon copy `dev-workspace-management-20261112`. It is NOT applied to staging
`main`, not deployed to Modal, and not a production launch approval.

The existing checked-in GitHub `main` is older local Harvey code. Do not
merge draft PR #2's unrelated `sql/staging/010_paid_feature_gates.sql` migration
and this `0010` into the same database as if they were independent version 10:
renumber/rebase and reconcile policies before rolling out either.

## Customer experience

1. Anyone may create and verify a Neon Auth account without an invitation.
2. After the first verified dashboard login, if the user has no **owned** business
   workspace, `POST /api/v1/workspaces/bootstrap` creates an owner-only free
   workspace. Repeating the request or opening another tab returns the same
   workspace. It does not start billing, AI, prospecting or sending.
3. The owner may edit the business name, industry, public website and timezone.
   Owners and admins may invite teammates via a manually shared one-time token;
   the invite recipient must log in with the verified **matching email** and
   accept it. Invitations expire after seven days and can be revoked.
4. Members can view the workspace roster and business metadata; owners alone
   can promote/demote non-owner team members. Owner and departing member can
   remove a non-owner member; nobody can remove or demote the owner.
5. Only an owner can delete a workspace with **no** subscriptions, contacts,
   campaigns, jobs, credentials, invitations or other members. Workspaces with
   operational or billing history must go through a separate retention-aware
   closure workflow. This protection is enforced by a definer-owned SQL function.
6. Membership/metadata management remains available **without a subscription**;
   existing paid-feature API gates on contacts, campaigns, drafts, credentials
   and jobs remain intact.

## New endpoints (all require verified bearer JWT)

| Method | Route | Authorization |
| --- | --- | --- |
| POST | /api/v1/workspaces/bootstrap | Verified user; idempotent free owned workspace |
| PATCH | /api/v1/workspaces/{id} | Owner/admin |
| DELETE | /api/v1/workspaces/{id} | Owner and empty workspace only |
| GET | /api/v1/workspaces/{id}/members | Member |
| PATCH | /api/v1/workspaces/{id}/members/{user_id} | Owner; only admin/member target |
| DELETE | /api/v1/workspaces/{id}/members/{user_id} | Owner or self; cannot remove owner |
| GET | /api/v1/workspaces/{id}/invitations | Owner/admin |
| POST | /api/v1/workspaces/{id}/invitations | Owner/admin |
| POST | /api/v1/workspace-invitations/accept | Verified matching invitee email |
| POST | /api/v1/workspaces/{id}/invitations/{invitation_id}/revoke | Owner/admin |

Invitation secrets never go into the database in plaintext, only SHA-256.
The raw token is returned only at creation for the inviter to share.
Invitations do not trigger an email send, and public signup itself is NOT
invitation-only. Email delivery and UI for roster/invite administration are later
frontend work, not needed for this backend review.

## Database security

- Existing `zyvello_api_staging` role is a non-owner and uses transaction-local
  `zyvello.auth_user_id` from a server-verified Neon Auth JWT.
- Workspace metadata updates are column-limited to `name` and `settings`.
  Row-level UPDATE policy requires workspace owner/admin.
- Roster visibility requires membership; invitation creation/read/revocation
  requires owner/admin. SQL definer functions are EXECUTE-granted only to API.
- Invitation acceptance checks the actual verified recipient email in
  `neon_auth.user`, locks the one-time token row, inserts only the invited
  role, and marks the token accepted in the same transaction.
- The owner-only empty-workspace delete function checks for billing and
  operational history, locks the workspace row, and refuses deletion of
  populated workspaces. No unrestricted DELETE permission is issued to API.
- Existing premium entitlement checks remain in the hosted API; no automation
  or payment functionality was enabled by this change.

## Testing evidence

- Isolated Neon branch: v0010 migration applied successfully; `settings`
  column and three invitation RLS policies verified.
- Restricted staging API role on isolated branch: user A could view and
  rename only its workspace; user B could not SELECT or UPDATE user A's
  workspace. Own team roster counted one and foreign roster zero. An
  owner created a pending invite on the cloned database.
- New empty-workspace SQL function was installed on the cloned branch, but
  destructive DELETE and verified-recipient acceptance against real Neon
  Auth users were NOT exercised through the database connector.
- HTTP API mock-identity contract tests cover bootstrap idempotency, unpaid
  workspace access, cross-tenant requests, owner/admin/member changes,
  invite acceptance/replay/revocation, safe workspace deletion, invalid
  settings inputs and existing premium gate invariants.
- Run the focused tests from the worktree with:
  `python -m pytest -q tests/test_workspace_management.py tests/test_hosted_workspace.py tests/test_hosted_auth.py tests/test_hosted_health.py`.
  On the Windows test host set a writable explicit `--basetemp` to avoid
  the host's restricted default temp folder.
- Hosted Vite TypeScript build and release preflight passed with
  `VITE_ZYVELLO_ENABLE_SIGNUP=true` and
  `VITE_ZYVELLO_ENABLE_NEW_WORKSPACES=true`; it was NOT deployed.

## Required before calling item #2 production-ready

1. Code review and migration numbering coordination with other open PRs.
2. Neon snapshot, migration replay and rollback rehearsal on the isolated
   branch; then planned staging `main` rollout using owner credentials.
3. Test real signed-in bearer flows with two different verified accounts:
   bootstrap, workspace edits, invitation acceptance, role mutation, self
   leave and deletion of a deliberately disposable **empty** workspace.
4. Verify session revocation, workspace quotas, concurrent bootstrap/invite
   races, membership limit abuse/rate limits and privacy/security logs.
5. Deploy to the dedicated Modal Zyvello staging environment and perform
   the authenticated browser journey before any public production cutover.

Existing `zyvello.pro` DNS, Razorpay live charging, AI jobs and outbound
email remain untouched. Business workspace APIs must pass these gates before
declaring the hosted backend 100% production-ready.
