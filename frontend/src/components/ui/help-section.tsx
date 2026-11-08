import helpContent from "@/content/help.html?raw";

// Static, repository-owned documentation only. Never insert API/customer HTML here.
export function HelpSection() {
  return <section id="help" aria-label="Help" className="section-wrap landing-help border-t border-white/10">
    <div className="landing-glass help-document" dangerouslySetInnerHTML={{ __html: helpContent }} />
  </section>;
}
