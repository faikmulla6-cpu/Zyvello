# Zyvello — approved launch pricing

**Owner decision:** 13 November 2026. Prices below are confirmed for the
product catalog and displayed as base monthly prices, in INR. These figures
do not constitute approval for real-money charging or a public launch.

| Code | Display name | Base monthly INR | Paise | Launch status |
|---|---|---:|---:|---|
| starter | Starter | ₹499 | 49900 | Approved for launch |
| growth | Growth | ₹1,499 | 149900 | Approved for launch |
| business | Business | ₹3,999 | 399900 | Approved for launch |
| ai_employee | AI Employee | From ₹9,999 | 999900 starting estimate | Future; checkout prohibited |

## Product and commercial boundaries

- Billing is **monthly** only until annual discount terms are approved.
- The amount is the **base price**, with applicable taxes to be determined
  and shown before a live purchase. No representation about whether GST
  is inclusive/exclusive is made beyond the base-price distinction.
- Final feature bundles, user seats, campaign/lead volumes, AI credit costs,
  e-mail sending allowances and fair-use quotas are **not yet decided**.
  Do not advertise unstaged features as working premium benefits.
- AI Employee is neither a currently sellable tier nor a promise that
  autonomous outreach is enabled.
- Refunds, support, taxes/invoices and cancellation terms require their own
  approval and published documentation before live payments.

## Provider readiness and migration behavior

- Existing TEST-only Razorpay plan IDs and historical subscription records
  remain untouched. The current configured TEST plan may be an older legacy
  offer with a different amount. It is deliberately NOT presented for new
  purchases after this decision.
- The public `GET /api/v1/pricing` returns the approved plan catalog and
  future tier independently of whether credentials are configured.
- Workspace `GET /api/v1/workspaces/{id}/billing/plans` lists only the
  approved prices and advertises TEST checkout availability separately.
- A TEST checkout is only possible if the server environment maps that
  plan code to a Razorpay TEST plan with the **exact** agreed price and
  monthly interval; the provider API is also queried to verify currency
  and amount before creating an actual TEST subscription. No merchant
  plan IDs or secrets belong in source code.
- A forged/legacy plan code, missing mapping or mismatched amount is
  rejected server-side even if a client modifies the browser request.
- `ZYVELLO_RAZORPAY_MODE=live` remains disabled by the adapter. Real
  payments and outreach sending are still disabled.
- Existing paid-access entitlement is based on signed captured payment,
  active status and unexpired paid period; merely listing a tier never
  provides entitlement.

## Next acceptance checks

1. Create or identify three exact matching **Razorpay TEST** provider plans
   in the merchant dashboard and provide their IDs through the existing
   secure staging configuration. Never replace secret values in code.
2. Confirm all three display prices with the merchant provider response.
3. Run a fresh signed TEST checkout, renewal success/failure, cancellation,
   tax/invoice and refund/dispute simulations and verify entitlement
   transitions with real authenticated QA accounts.
4. Obtain approval on plan quotas, annual options, applicable tax handling,
   merchant/account compliance and published cancellation/refund policy.
5. Only after those steps, separately authorize a live-payment deployment.
