import { BrandIcon } from "@/components/ui/brand-icon";

const supportEmail = "support@zyvello.pro";

export function SubscriptionPolicyPage() {
  return (
    <div className="landing-page min-h-screen">
      <header className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-6 py-8">
        <a href="/welcome" className="inline-flex items-center gap-2.5 text-xl font-semibold">
          <BrandIcon /> Zyvello
        </a>
        <a href="/welcome#pricing" className="text-sm text-gray-300 underline underline-offset-4">View plans</a>
      </header>
      <main className="mx-auto max-w-4xl px-6 pb-16">
        <article className="landing-glass space-y-8 p-6 text-gray-300 sm:p-10">
          <div>
            <h1 className="text-3xl font-semibold text-white sm:text-4xl">Subscription policy</h1>
            <p className="mt-3">Policy preview. Checkout currently runs in Test Mode.</p>
          </div>
          <section aria-labelledby="plans-and-renewal">
            <h2 id="plans-and-renewal" className="mb-2 text-xl font-semibold text-white">Plans and renewal</h2>
            <p>Each monthly subscription covers one workspace. Paid plans include manual contact management, campaign drafts, and workspace checks, with storage and seat limits shown on the pricing page. Checkout shows the total payable and renewal schedule before you authorise recurring payments.</p>
          </section>
          <section aria-labelledby="cancellation">
            <h2 id="cancellation" className="mb-2 text-xl font-semibold text-white">Cancellation and access</h2>
            <p>Workspace owners can request cancellation at the end of the billing cycle from Billing. Razorpay confirms when cancellation takes effect. You can still open Billing when paid access ends.</p>
          </section>
          <section aria-labelledby="refunds">
            <h2 id="refunds" className="mb-2 text-xl font-semibold text-white">Refunds and billing complaints</h2>
            <p>You can ask us to review your first payment within seven calendar days if you did not use the paid tools or they were materially unavailable. We review duplicate or unauthorised charges separately. Your statutory rights still apply. Cancelling partway through a month does not automatically trigger a prorated refund.</p>
            <p className="mt-3">Email support with the payment reference and a short description of the issue. Approved refunds go to the original payment method.</p>
          </section>
          <section aria-labelledby="contact">
            <h2 id="contact" className="mb-2 text-xl font-semibold text-white">Seller and support</h2>
            <address className="not-italic">
              Faik Hameed Mulla<br />
              Khwaja Basti, Miraj, Maharashtra, India
            </address>
            <p className="mt-3">
              Customer support and grievances{" "}
              <a className="text-white underline underline-offset-4" href={`mailto:${supportEmail}`}>{supportEmail}</a>
            </p>
          </section>
        </article>
      </main>
    </div>
  );
}
