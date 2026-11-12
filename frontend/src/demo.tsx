import { ArrowRight, Check, Search, Send, MessageSquare, Sparkles } from "lucide-react";
import { Hero2 } from "@/components/ui/hero-2-1";
import { HelpSection } from "@/components/ui/help-section";
import { BrandIcon } from "@/components/ui/brand-icon";

const steps = [
  { icon: Sparkles, title: "Describe your business", text: "Start with your website or a short brief. Set your offer, ideal customer, and the goal of your outreach." },
  { icon: Search, title: "Organize contacts", text: "Add and manage your business contacts manually in a private workspace." },
  { icon: Send, title: "Prepare campaign drafts", text: "Create campaign plans, attach contacts and review your audience before any sending." },
  { icon: MessageSquare, title: "AI outreach is coming later", text: "Automated lead discovery, sending and replies are not available in the hosted preview." },
];

const DemoOne = () => (
  <div className="landing-page">
    <Hero2 />
    <section id="workflow" className="section-wrap border-t border-white/10">
      <p className="eyebrow">From brief to conversation</p>
      <h2 className="section-title">Your offer. An active pipeline.</h2>
      <div className="mt-12 grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
        {steps.map(({ icon: Icon, title, text }, index) => (
          <article key={title} className="landing-glass p-6">
            <div className="flex items-center justify-between text-gray-400">
              <Icon aria-hidden="true" className="h-5 w-5" /><span className="text-xs">0{index + 1}</span>
            </div>
            <h3 className="mt-5 text-lg font-medium">{title}</h3>
            <p className="mt-3 text-sm leading-7 text-gray-400">{text}</p>
          </article>
        ))}
      </div>
    </section>
    <section id="use-cases" className="section-wrap grid items-center gap-10 border-t border-white/10 md:grid-cols-2 md:gap-16">
      <div>
        <p className="eyebrow">Built for small agencies</p>
        <h2 className="section-title">Make room for the conversations that matter.</h2>
        <p className="mt-5 max-w-lg leading-7 text-gray-400">Keep manually added contacts and campaign drafts organized. Automated prospecting, sending and AI replies are not enabled in the hosted product yet.</p>
        <a href="/" className="mt-7 inline-flex items-center gap-2 font-medium">Explore the dashboard <ArrowRight aria-hidden="true" className="h-4 w-4" /></a>
      </div>
      <figure>
        <img src="/static/landing/assets/agency-office.jpg" alt="A bright shared office with desks, plants, and room for a growing team"
          loading="lazy" width="1600" height="1067" className="aspect-[4/3] w-full rounded-2xl object-cover" />
        <figcaption className="mt-3 text-xs text-gray-500">Office photograph from Unsplash.</figcaption>
      </figure>
    </section>
    <section id="pricing" className="section-wrap grid items-center gap-10 border-t border-white/10 md:grid-cols-2 md:gap-16">
      <div>
        <p className="eyebrow">Hosted plans — approved prices</p>
        <h2 className="section-title">Start small.<br />Grow with control.</h2>
        <p className="mt-5 max-w-md leading-7 text-gray-400">Base monthly pricing in INR, plus applicable taxes. Account creation is free. Paid features are limited to the currently available hosted tools, and live payments remain disabled pending merchant and release approval.</p>
        <p className="mt-4 text-sm text-gray-400">AI Employee — from ₹9,999/month, future only. Not available for purchase.</p>
      </div>
      <div className="grid gap-4 sm:grid-cols-3 md:grid-cols-1 lg:grid-cols-3 md:col-span-1">
        {([
          { code: "Starter", price: "₹499", contacts: "100", drafts: "5", seats: "1" },
          { code: "Growth", price: "₹1,499", contacts: "400", drafts: "20", seats: "3" },
          { code: "Business", price: "₹3,999", contacts: "1,000", drafts: "50", seats: "10" },
        ] as const).map(plan => (
          <article className="landing-glass p-5" key={plan.code}>
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-medium">{plan.code}</h3>
              <span className="text-[10px] text-gray-400">PREVIEW</span>
            </div>
            <p className="mt-4 text-2xl font-semibold">{plan.price}<span className="ml-1 text-xs font-normal text-gray-400">/mo</span></p>
            <ul className="mt-4 space-y-2 text-xs text-gray-300">
              {[plan.contacts+" stored contacts",plan.drafts+" campaign drafts",plan.seats+" workspace member(s)"].map(text =>
                <li className="flex items-start gap-2" key={text}><Check aria-hidden="true" className="h-4 w-4 shrink-0 text-gray-400" />{text}</li>
              )}
            </ul>
          </article>
        ))}
      </div>
    </section>
    <section id="resources" className="section-wrap border-t border-white/10">
      <p className="eyebrow">Getting started</p><h2 className="section-title">A few things to know.</h2>
      <div className="landing-faq mt-8 space-y-3">
        <details className="py-6"><summary className="cursor-pointer font-medium">How do I get started?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">Create a free account and open your private business workspace. A paid TEST subscription currently unlocks manual contact and campaign planning in the hosted beta. Live payment collection is not enabled.</p></details>
        <details className="py-6"><summary className="cursor-pointer font-medium">Can this hosted version send emails?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">No. Contact management and campaign planning are available in the hosted preview. Outbound sending, automated AI outreach and reply handling are not enabled.</p></details>
        <details className="py-6"><summary className="cursor-pointer font-medium">Can I connect a mailbox in this hosted beta?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">No. Hosted sending and mailbox integrations are disabled. We will clearly announce when supported mailbox and automation features pass their security and deliverability checks.</p></details>
      </div>
    </section>
    <HelpSection />
    <footer className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5 border-t border-white/10 px-5 py-8 text-sm md:px-8">
      <a href="/welcome" className="inline-flex items-center gap-2.5 text-lg font-semibold"><BrandIcon />Zyvello</a><p className="text-gray-500">Hosted contact management and campaign planning preview.</p><a href="/" className="inline-flex items-center gap-2">Open dashboard <ArrowRight aria-hidden="true" className="h-4 w-4" /></a>
    </footer>
  </div>
);

export { DemoOne };
