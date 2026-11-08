import { createRoot } from "react-dom/client";
import Integrations from "@/components/ui/integrations-02";
import styles from "@/styles/globals.css?inline";

// Keep Tailwind's reset and component styles inside the existing Settings panel.
// Dashboard typography, theme, tabs, tables and forms retain their own CSS.
const host = document.getElementById("mailbox-connections-root");
if (host && !host.shadowRoot) {
  const shadow = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = styles + "\n:host { display:block; font-family:Geist,system-ui,sans-serif; color-scheme:dark; }";
  const container = document.createElement("div");
  shadow.append(style, container);
  host.replaceChildren();
  createRoot(container).render(<Integrations />);
}
