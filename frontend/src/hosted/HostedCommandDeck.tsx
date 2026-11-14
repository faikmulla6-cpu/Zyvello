import { LogOut } from "lucide-react";
import type { ReactNode } from "react";
import commandDeck from "./command-deck-assets/index.html?raw";
import baseStyles from "./command-deck-assets/app.css?url";
import glassStyles from "./command-deck-assets/dashboard-glass.css?url";
import premiumStyles from "./command-deck-assets/premium-glass.css?url";

const navGroups = [
  { title: "Work", items: [["today", "Today"], ["outbox", "Outbox"], ["conversations", "Conversations"]] },
  { title: "Pipeline", items: [["signals", "Signals"], ["discover", "Discover"], ["companies", "Companies"], ["contacts", "Contacts"], ["campaigns", "Campaigns"]] },
  { title: "System", items: [["activity", "Activity"], ["usage", "Usage"], ["controls", "Controls"], ["settings", "Settings"]] },
] as const;

const navigation = navGroups.flatMap(group => [...group.items]);
const unreleasedSections = new Set<string>(["signals", "discover"]);
const deliverySections = new Set<string>(["outbox", "conversations", "controls"]);
const iconDefinitions = new DOMParser().parseFromString(commandDeck, "text/html")
  .querySelector("svg > defs")?.outerHTML ?? "";

export type DeckSection = typeof navigation[number][0] | "account";

export function readDeckSection(): DeckSection {
  const section = window.location.hash.slice(1);
  if (section === "account") return "account";
  if (section === "prospects") return "contacts";
  return navigation.find(([id]) => id === section)?.[0] ?? "today";
}

const sectionTitles = new Map<DeckSection, string>([...navigation, ["account", "My account"]]);

type Props = {
  email: string;
  section: DeckSection;
  onNavigate: (section: DeckSection) => void;
  onSignOut: () => void;
  signingOut: boolean;
  campaignDeliveryAvailable: boolean;
  children: ReactNode;
};

export function HostedCommandDeck({ email, section, onNavigate, onSignOut, signingOut, campaignDeliveryAvailable, children }: Props) {
  const visibleGroups = navGroups.map(({ title, items }) => ({
    title,
    items: items.filter(([id]) => !unreleasedSections.has(id)
      && (campaignDeliveryAvailable || !deliverySections.has(id))),
  }));
  return (
    <>
      <link rel="stylesheet" href={baseStyles} />
      <link rel="stylesheet" href={glassStyles} />
      <link rel="stylesheet" href={premiumStyles} />
      <svg aria-hidden="true" width="0" height="0" className="absolute" dangerouslySetInnerHTML={{ __html: iconDefinitions }} />
      <div className="app">
        <aside className="sidebar" id="dashboard-sidebar">
          <div className="brand">
            <a href="/welcome" className="flex items-center gap-2 text-xl font-semibold no-underline">
              <img className="zyvello-brand-icon" src="/static/favicon.svg" alt="" width="32" height="32" />
              Zyvello
            </a>
          </div>
          <nav aria-label="Command Deck">
            {visibleGroups.map(group => (
              <div className="nav-group" key={group.title}>
                <div className="nav-label">{group.title}</div>
                {group.items.map(([id, label]) => (
                  <button type="button" key={id} className={section === id ? "active" : undefined}
                    aria-current={section === id ? "page" : undefined} onClick={() => onNavigate(id)}>
                    <svg aria-hidden="true" className="icon"><use href={`#i-${id}`} /></svg>
                    <span className="label">{label}</span>
                  </button>
                ))}
              </div>
            ))}
          </nav>
          <div className="sidebar-foot">
            <a href="#account" aria-label="My account" className="account-menu account-link"
              aria-current={section === "account" ? "page" : undefined}>
              <span className="account-avatar" aria-hidden="true"><img src="/static/favicon.svg" width="32" height="32" alt="" /></span>
              <span className="account-summary"><strong>My account</strong><small>{email}</small></span>
              <svg aria-hidden="true" className="icon account-arrow"><use href="#i-arrow" /></svg>
            </a>
            <button className="refresh-btn" type="button" disabled={signingOut} onClick={onSignOut}>
              <LogOut size={15} aria-hidden="true" />{signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </aside>
        <main className="content" aria-labelledby="deck-heading">
          <div className="content-inner">
            <header className="section-head">
              <h2 id="deck-heading">{sectionTitles.get(section)}</h2>
            </header>
            {children}
          </div>
        </main>
      </div>
    </>
  );
}
