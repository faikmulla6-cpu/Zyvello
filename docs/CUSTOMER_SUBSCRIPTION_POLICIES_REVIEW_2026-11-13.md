# Zyvello hosted SaaS — proposed customer terms for legal review

**Status: DRAFT — NOT PUBLISHED OR IN FORCE. DO NOT ENABLE LIVE PAYMENTS.**
Commercial terms below are proposed for the hosted beta. The release notes
at the end are for internal review.

## Seller and customer contact

- **Seller:** Faik Hameed Mulla
- **Business address:** Khwaja Basti, Miraj, Maharashtra, India
- **Customer support:** support@zyvello.pro
- **Grievance contact:** support@zyvello.pro

## The service customers can actually buy (hosted beta)

Zyvello Hosted currently supplies workspace access, manually entered contacts,
draft campaign planning/audiences, basic roles, and workspace health checks.
It DOES NOT provide automated lead discovery, AI-written outreach, email
sending, reply monitoring, sales appointments, or the future "AI Employee"
agent. These features must not be sold or advertised as included.

All limits below are **per subscribed workspace at one time**; they are stored
records/seats, NOT new contacts per month or message volume. Accounts may be
created without payment, but operational writes are paywalled. A separate
subscription is required for each workspace. New workspaces are not a way to
multiply quota without paying.

| Monthly plan | Base price (INR per month) | Stored contacts | Campaign drafts | Total member seats including owner |
|---|---:|---:|---:|---:|
| Starter | 499 | 100 | 5 | 1 |
| Growth | 1,499 | 400 | 20 | 3 |
| Business | 3,999 | 1,000 | 50 | 10 |

Features in ALL three plans: manual contact management, campaign draft
planning, team roster where seats allow, and safe workspace diagnostics.
No outbound emails, purchased leads, AI generations or external email
accounts are included. No usage credits or automatic recharge are sold.
"AI Employee from ₹9,999" is **future only, not purchasable**.

All plans are monthly recurring only. No annual subscriptions are offered.
No free trial charge, paid add-on, credit-card requirement for account
creation, or bundled third-party data/service is promised. Plan upgrades and
downgrades are not available until a separately tested change flow exists.

## Payment authorisation and renewal (proposed)

Checkout shows the plan, total payable, renewal schedule, and the recurring
payment authorisation. Only a signed,
verified Razorpay *captured* payment and an active, unexpired paid term unlock
hosted premium operations. We do not grant access merely because a browser
claims payment success.

Each eligible monthly subscription renews via the customer's authorised
Razorpay recurring mandate until cancelled. The payment method and bank
may require further authorization. Access to paid workspace tools is
suspended when Zyvello verifies a billing state that blocks access or when
the verified paid period ends. Workspace owners can still access Billing.

## Cancellation (proposed)

The workspace owner can request non-renewal in Billing without calling or
emailing support. The API requests cancellation at the end of the provider
billing cycle. Cancellation must not be represented as final until
Razorpay confirms the state. New charges after an effective cancellation
must not occur. Access through the prepaid period depends on the provider's
confirmed state; a premature provider 'cancelled' state would currently
disable premium writes immediately and must be acceptance-tested before
claiming uninterrupted access through the full cycle.

**Do not state that existing access is guaranteed until period end until
this provider-specific behaviour is verified and enforced.** Cancellation
does not automatically erase contacts or workspace records.

## Refunds and service complaints (proposed)

There is no self-service automated refund endpoint yet. The proposed
customer policy grants a **7-calendar-day first-payment review window**
when the paid hosted functionality was not used or was materially
unavailable. Verified duplicate/unauthorised charges and refunds required
by law remain eligible regardless of the goodwill window. Voluntary
mid-cycle cancellation of an already usable, used monthly plan does
not automatically trigger a prorated refund; mandatory statutory rights
are never waived. Subsequent renewal/refund disputes are reviewed case
by case with the payment evidence. Email support@zyvello.pro to request
a review or raise a billing complaint.

An authorised operator must review and communicate the decision and,
where approved, issue the refund via Razorpay to the eligible original
payment method. The bank/payment network determines the final credit
timing; do not promise immediate credit. No full card details should
ever be requested for support. **The operator must approve and
operationally support these refund rules before publication** and
complete the policy review.

## Customer records, cancellations and privacy (proposed)

Contact names/emails, campaign drafts and workspace membership are held
under tenant-scoped permissions. We do not sell customer contact lists.
AI draft execution and sending are off in hosted beta. A customer should
be able to request an export, correction or deletion through verified
support and obtain a clear retention/suppression explanation. Subscription
cancellation is not the same as immediate deletion. Statutory accounting,
fraud-prevention and security audit records may require retention.

**Unresolved:** self-serve export/deletion implementation, exact deletion
windows, operational contact and privacy officer details, and data processor
agreements. Do not publish unverified privacy warranties.

## Internal release notes

- The owner supplied the public seller, address, support and grievance
  details on 13 November 2026. Seller identity and mailbox operation still
  require independent verification for release.
- Razorpay merchant activation, domain approval and recurring payment
  methods were verified in the live dashboard on 13 November 2026.
- Account creation and initial workspace: open and free.
- Signed TEST checkout and captured payment passed for Growth and for a
  separate Starter billing QA workspace. The Starter checkout unlocked paid
  access after the `subscription.charged` webhook.
- Automated failure and cancellation tests passed. Razorpay acknowledged
  the Starter sandbox request to charge as a failure. Staging received no
  `subscription.pending` event during the test and later applied another
  `subscription.charged` event. Provider-side event sequencing and failed
  renewal access suspension remain unverified.
- Zyvello received a successful response to its cycle-end cancellation
  request on the active Starter QA mandate. The paid workspace stayed active
  and no terminal cancellation event was observed during the test.
  Completion and prevention of the next renewal remain unverified.
- The existing Growth workspace and its paid period were preserved.
- Plan quotas must be enforced server-side and database-side before being
  promised on the public site.
- Merge the public landing correction; remove outdated ₹4,999 Agency
  and unshipped automation claims before offering live subscriptions.
- Verify live keys and signed webhook delivery as part of the controlled
  production configuration and pilot.
- Complete the policy review and publish pricing, terms, refund, privacy
  and merchant contact information before the live pilot.
- First live collection should be one internally authorized capped pilot
  with reconciliation and rollback owner, never public mass launch.
