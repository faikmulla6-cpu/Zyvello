import { ArrowRight, Check, Search, Send, MessageSquare, Sparkles } from "lucide-react";
import { Hero2 } from "@/components/ui/hero-2-1";
import { HelpSection } from "@/components/ui/help-section";
import { BrandIcon } from "@/components/ui/brand-icon";

const steps = [
  { icon: Sparkles, title: "Describe your business", text: "Start with your website or a short brief. Set your offer, ideal customer, and the goal of your outreach." },
  { icon: Search, title: "Find and verify leads", text: "Discover companies, choose the signals that matter, and verify contact details before reaching out." },
  { icon: Send, title: "Send relevant outreach", text: "Connect your mailbox. Review personalized drafts or enable autopilot within your sending limits." },
  { icon: MessageSquare, title: "Keep replies moving", text: "Handle routine replies, stop follow-ups when someone answers, and bring exceptions back to you." },
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
        <p className="mt-5 max-w-lg leading-7 text-gray-400">Use business signals to find a reason to reach out. Keep prospects, campaigns, and conversations connected as you build your agency's pipeline.</p>
        <a href="/" className="mt-7 inline-flex items-center gap-2 font-medium">Explore the dashboard <ArrowRight aria-hidden="true" className="h-4 w-4" /></a>
      </div>
      <figure>
        <img src="/static/landing/assets/agency-office.jpg" alt="A bright shared office with desks, plants, and room for a growing team"
          loading="lazy" width="1600" height="1067" className="aspect-[4/3] w-full rounded-2xl object-cover" />
        <figcaption className="mt-3 text-xs text-gray-500">Office photograph from Unsplash.</figcaption>
      </figure>
    </section>
    <section id="pricing" className="section-wrap grid items-center gap-10 border-t border-white/10 md:grid-cols-2 md:gap-16">
      <div><p className="eyebrow">Hosted plan preview</p><h2 className="section-title">One plan.<br />A connected workflow.</h2><p className="mt-5 max-w-md leading-7 text-gray-400">Explore the local edition today. Subscription checkout will be available with the hosted launch.</p></div>
      <article className="landing-glass p-7 md:p-9">
        <div className="flex items-center justify-between gap-3"><h3 className="text-lg font-medium">Zyvello Agency</h3><span className="rounded-full border border-white/20 px-3 py-1 text-[10px] tracking-widest text-gray-300">PLANNED</span></div>
        <p className="mt-6"><strong className="text-5xl font-semibold tracking-tight">₹4,999</strong><span className="ml-2 text-sm text-gray-400">/ month</span></p>
        <ul className="my-7 space-y-4 text-sm text-gray-300">
          {["One workspace and one active sending mailbox", "Up to 300 newly verified contacts per billing cycle", "Up to 600 outbound emails per billing cycle"].map((text) => (
            <li key={text} className="flex items-start gap-3"><Check aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />{text}</li>
          ))}
        </ul>
        <p className="text-xs leading-6 text-gray-500">Proposed pricing and allowances. Checkout opens at launch.</p>
      </article>
    </section>
    <section id="resources" className="section-wrap border-t border-white/10">
      <p className="eyebrow">Getting started</p><h2 className="section-title">A few things to know.</h2>
      <div className="landing-faq mt-8 space-y-3">
        <details className="py-6"><summary className="cursor-pointer font-medium">How do I open the local dashboard?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">Choose Open dashboard to go straight to your workspace. Start the local app with <code>the local dashboard launcher</code> and open the address it prints. Your existing setup is available there.</p></details>
        <details className="py-6"><summary className="cursor-pointer font-medium">Can I review emails before they send?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">Yes. Approval mode sends drafts to the Outbox for your review. You can choose autopilot in Settings, set daily limits and quiet hours, and pause sending at any time.</p></details>
        <details className="py-6"><summary className="cursor-pointer font-medium">Which email providers can I use?</summary><p className="mt-4 max-w-3xl text-sm leading-7 text-gray-400">The local edition supports Gmail, Microsoft 365, SMTP and IMAP mailboxes, and the existing Instantly integration. Connect your provider through the dashboard or CLI setup.</p></details>
      </div>
    </section>
    <HelpSection />
    <footer className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-5 border-t border-white/10 px-5 py-8 text-sm md:px-8">
      <a href="/welcome" className="inline-flex items-center gap-2.5 text-lg font-semibold"><BrandIcon />Zyvello</a><p className="text-gray-500">Lead discovery, outreach, and replies.</p><a href="/" className="inline-flex items-center gap-2">Open dashboard <ArrowRight aria-hidden="true" className="h-4 w-4" /></a>
    </footer>
  </div>
);

export { DemoOne };
