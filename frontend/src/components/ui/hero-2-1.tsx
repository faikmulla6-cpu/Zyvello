"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowRight, ChevronDown, Menu, X } from "lucide-react";
import { BrandIcon } from "@/components/ui/brand-icon";
import { AnimatePresence, motion } from "motion/react";

const navigation = [
  { label: "Use Cases", href: "#use-cases" },
  { label: "Products", href: "#workflow" },
  { label: "Resources", href: "#resources" },
  { label: "Pricing", href: "#pricing" },
  { label: "Help", href: "#help" },
];

function Brand() {
  return (
    <a href="/welcome" aria-label="Zyvello home" className="flex items-center gap-2.5 text-xl font-bold tracking-tight text-white">
      <BrandIcon />
      Zyvello
    </a>
  );
}

const Hero2 = () => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const menuPanel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const panel = menuPanel.current;
    const focusable = panel?.querySelectorAll<HTMLElement>("a[href], button");
    focusable?.[0]?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMobileMenuOpen(false);
      if (event.key !== "Tab" || !focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    const desktop = window.matchMedia("(min-width: 768px)");
    function onResize() {
      if (desktop.matches) setMobileMenuOpen(false);
    }

    document.addEventListener("keydown", onKeyDown);
    desktop.addEventListener("change", onResize);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
      desktop.removeEventListener("change", onResize);
      menuButton.current?.focus({ preventScroll: true });
    };
  }, [mobileMenuOpen]);

  return (
    <div className="landing-hero relative isolate min-h-screen overflow-hidden bg-black text-white">
      <div aria-hidden="true" className="pointer-events-none absolute -right-60 -top-10 z-0 flex flex-col items-end opacity-65 blur-xl">
        <div className="h-40 w-[60rem] rounded-full bg-gradient-to-b from-purple-600 to-sky-600 blur-[6rem]" />
        <div className="h-40 w-[90rem] rounded-full bg-gradient-to-b from-pink-900 to-yellow-400 blur-[6rem]" />
        <div className="h-40 w-[60rem] rounded-full bg-gradient-to-b from-yellow-600 to-sky-500 blur-[6rem]" />
      </div>
      <div aria-hidden="true" className="bg-noise pointer-events-none absolute inset-0 z-0 opacity-30" />

      <div className="relative z-10" inert={mobileMenuOpen}>
        <nav aria-label="Main navigation" className="landing-nav mx-auto mt-3 flex max-w-7xl items-center justify-between px-5 py-5 md:mt-6 md:px-8">
          <Brand />
          <div className="hidden items-center gap-8 md:flex">
            <div className="flex items-center gap-7">
              <NavItem label="Use Cases" href="#use-cases" items={[
                { label: "Small agencies", href: "#use-cases" },
                { label: "Business owners", href: "#use-cases" },
              ]} />
              <NavItem label="Products" href="#workflow" items={[
                { label: "Lead discovery", href: "#workflow" },
                { label: "Outreach & replies", href: "#workflow" },
              ]} />
              <NavItem label="Resources" href="#resources" />
              <NavItem label="Pricing" href="#pricing" />
              <NavItem label="Help" href="#help" />
            </div>
            <a href="/login" className="text-sm font-medium">Log in</a>
            <a href="/signup" className="button-light">Sign up</a>
          </div>
          <button ref={menuButton} type="button" aria-label="Open navigation" aria-expanded={mobileMenuOpen}
            aria-controls="mobile-navigation" onClick={() => setMobileMenuOpen(true)}
            className="flex h-11 w-11 items-center justify-center rounded-full border border-white/20 md:hidden">
            <Menu aria-hidden="true" className="h-5 w-5" />
          </button>
        </nav>

        <main>
          <section aria-labelledby="hero-title" className="mx-auto max-w-7xl px-5 pt-14 text-center md:px-8 md:pt-20">
            <a href="#workflow" className="mx-auto flex w-fit items-center gap-2 rounded-full border border-white/10 bg-white/10 px-4 py-2 text-xs font-medium backdrop-blur-sm sm:text-sm">
              Your next customer starts with a conversation
              <ArrowRight aria-hidden="true" className="h-4 w-4 shrink-0" />
            </a>
            <h1 id="hero-title" className="mx-auto mt-8 max-w-4xl text-5xl font-semibold leading-[1.08] tracking-[-0.055em] text-white md:text-6xl lg:text-7xl">
              Describe your business.<br className="hidden sm:block" /> Let your outreach begin.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base leading-7 text-gray-300 md:text-lg">
              Find the right companies, write emails that mean something, and keep replies moving.
              Zyvello brings your sales workflow into one place.
            </p>
            <div className="mt-9 flex flex-col items-stretch justify-center gap-3 sm:flex-row sm:items-center">
              <a href="/signup" className="button-light gap-2">Create your account <ArrowRight aria-hidden="true" className="h-4 w-4" /></a>
              <a href="#product-preview" className="button-outline gap-2">Explore the workflow <ArrowDown aria-hidden="true" className="h-4 w-4" /></a>
            </div>
            <p className="mt-4 text-xs text-gray-400">Local edition available · Hosted subscription planned</p>

            <figure id="product-preview" className="relative mx-auto mb-0 mt-14 max-w-6xl scroll-mt-6 md:mt-20">
              <div aria-hidden="true" className="pointer-events-none absolute inset-0 rounded-3xl bg-white opacity-10 blur-[7rem]" />
              <div className="relative overflow-hidden rounded-t-xl border border-white/20 bg-neutral-950 shadow-2xl">
                <div className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-3 text-[11px] text-gray-400">
                  <span className="flex items-center gap-1.5" aria-hidden="true"><i className="h-2 w-2 rounded-full bg-white/30" /><i className="h-2 w-2 rounded-full bg-white/20" /><i className="h-2 w-2 rounded-full bg-white/10" /></span>
                  <span>Zyvello · Outreach workspace</span>
                  <span className="text-gray-500">Example</span>
                </div>
                <img src="/static/landing/assets/dashboard-preview.png" alt="Zyvello dashboard showing example agency leads, verified email addresses, and contact filters"
                  className="relative block h-auto w-full" width="1440" height="820" fetchPriority="high" />
              </div>
              <figcaption className="relative bg-black py-3 text-left text-xs text-gray-500">Illustrative workspace with sample data.</figcaption>
            </figure>
          </section>
        </main>
      </div>

      <AnimatePresence>
        {mobileMenuOpen && (
          <motion.div ref={menuPanel} id="mobile-navigation" role="dialog" aria-modal="true" aria-label="Navigation"
            initial={{ y: "-100%" }} animate={{ y: 0 }} exit={{ y: "-100%" }} transition={{ duration: 0.3 }}
            className="fixed inset-0 z-50 flex flex-col overflow-y-auto bg-black/95 p-5 backdrop-blur-xl md:hidden">
            <div className="flex items-center justify-between">
              <Brand />
              <button type="button" onClick={() => setMobileMenuOpen(false)} aria-label="Close navigation"
                className="flex h-11 w-11 items-center justify-center rounded-full border border-white/20">
                <X aria-hidden="true" className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-10 flex flex-col gap-6">
              {navigation.map((item) => (
                <MobileNavItem key={item.label} {...item} onClick={() => setMobileMenuOpen(false)} />
              ))}
              <a href="/login" className="button-outline">Log in</a>
              <a href="/signup" className="button-light">Sign up</a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

function NavItem({ label, href, items }: {
  label: string;
  href: string;
  items?: { label: string; href: string }[];
}) {
  if (!items) return <a href={href} className="text-sm text-gray-300 transition-colors hover:text-white">{label}</a>;
  return (
    <details className="group relative">
      <summary className="flex cursor-pointer list-none items-center gap-1 text-sm text-gray-300 hover:text-white">
        {label}<ChevronDown aria-hidden="true" className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
      </summary>
      <div className="landing-menu absolute left-0 top-full z-20 mt-3 min-w-48 rounded-xl border border-white/15 bg-neutral-950 p-2 shadow-xl">
        {items.map((item) => (
          <a key={item.label} href={item.href} onClick={(event) => event.currentTarget.closest("details")?.removeAttribute("open")}
            className="block rounded-lg px-3 py-2 text-sm text-gray-300 hover:bg-white/10 hover:text-white">{item.label}</a>
        ))}
      </div>
    </details>
  );
}

function MobileNavItem({ label, href, onClick }: { label: string; href: string; onClick: () => void }) {
  return (
    <a href={href} onClick={onClick} className="flex items-center justify-between border-b border-gray-800 pb-4 text-lg text-white">
      {label}<ArrowRight aria-hidden="true" className="h-4 w-4 text-gray-400" />
    </a>
  );
}

export { Hero2 };
