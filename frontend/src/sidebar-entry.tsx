import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { createPortal } from "react-dom";
import { Sidebar, SidebarTrigger } from "@/components/ui/sidebar";
import { BrandIcon } from "@/components/ui/brand-icon";

function savedOpen() {
  try { return localStorage.getItem("zyvello-sidebar-open") !== "false"; }
  catch { return true; }
}

function DashboardSidebar() {
  const [mobile, setMobile] = useState(() => matchMedia("(max-width: 900px)").matches);
  const [desktopOpen, setDesktopOpen] = useState(savedOpen);
  const [mobileOpen, setMobileOpen] = useState(false);
  const open = mobile ? mobileOpen : desktopOpen;
  const setOpen = mobile ? setMobileOpen : setDesktopOpen;
  const opener = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const media = matchMedia("(max-width: 900px)");
    const update = () => { setMobile(media.matches); setMobileOpen(false); };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.sidebarEnhanced = "true";
    root.dataset.sidebarOpen = String(open);
    try { localStorage.setItem("zyvello-sidebar-open", String(desktopOpen)); } catch { /* Private mode. */ }
    const sidebar = document.getElementById("dashboard-sidebar")!;
    const main = document.querySelector<HTMLElement>("main.content")!;
    const header = document.querySelector<HTMLElement>(".sidebar-mobile-header")!;
    sidebar.inert = mobile && !open;
    main.inert = mobile && open;
    if (mobile && open) {
      sidebar.setAttribute("role", "dialog");
      sidebar.setAttribute("aria-modal", "true");
      sidebar.setAttribute("aria-label", "Dashboard navigation");
      opener.current = document.activeElement as HTMLElement;
    } else {
      sidebar.removeAttribute("role");
      sidebar.removeAttribute("aria-modal");
      sidebar.removeAttribute("aria-label");
    }
    header.inert = mobile && open;
    const focusFrame = mobile && open ? requestAnimationFrame(() => sidebar.querySelector<HTMLButtonElement>(".sidebar-toggle")?.focus()) : null;
    const previousOverflow = document.body.style.overflow;
    if (mobile && open) document.body.style.overflow = "hidden";
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && mobile && open) { setMobileOpen(false); opener.current?.focus(); }
      if (event.key !== "Tab" || !mobile || !open) return;
      const controls = Array.from(sidebar.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),[tabindex="0"]')).filter(el => el.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const navigate = (event: MouseEvent) => {
      if (mobile && (event.target as Element).closest("nav button,.account-link")) setMobileOpen(false);
    };
    document.addEventListener("keydown", keydown);
    sidebar.addEventListener("click", navigate);
    return () => {
      document.body.style.overflow = previousOverflow;
      main.inert = false;
      header.inert = false;
      if (focusFrame !== null) cancelAnimationFrame(focusFrame);
      if (mobile && open) requestAnimationFrame(() => opener.current?.focus());
      document.removeEventListener("keydown", keydown);
      sidebar.removeEventListener("click", navigate);
    };
  }, [open, mobile, desktopOpen]);

  return <Sidebar open={open} setOpen={setOpen}>
    <div className="sidebar-mobile-header"><a href="/welcome"><BrandIcon />Zyvello</a><SidebarTrigger mobile /></div>
    {createPortal(<SidebarTrigger mobile={mobile} />, document.getElementById("sidebar-toggle-root")!)}
    {mobile && open && <button className="sidebar-backdrop" tabIndex={-1} aria-label="Close sidebar backdrop" onClick={() => { setMobileOpen(false); opener.current?.focus(); }} />}
  </Sidebar>;
}

const host = document.getElementById("sidebar-controls-root");
if (host) {
  // Native DOM is retained: every existing tab, count, handler and integration stays mounted.
  document.querySelectorAll<HTMLButtonElement>("nav button").forEach(button => {
    const name = button.querySelector(".label")?.textContent || "";
    button.setAttribute("aria-label", name);
    button.title = name;
  });
  createRoot(host).render(<DashboardSidebar />);
}
