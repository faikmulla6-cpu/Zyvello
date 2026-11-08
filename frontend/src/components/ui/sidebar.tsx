"use client";

import React, { createContext, useContext, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Menu, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface SidebarContextProps {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
  animate: boolean;
}
const SidebarContext = createContext<SidebarContextProps | undefined>(undefined);
export function useSidebar() {
  const value = useContext(SidebarContext);
  if (!value) throw new Error("useSidebar must be used within SidebarProvider");
  return value;
}
export function SidebarProvider({ children, open: controlledOpen, setOpen: controlledSetOpen, animate = true }: {
  children: React.ReactNode;
  open?: boolean;
  setOpen?: React.Dispatch<React.SetStateAction<boolean>>;
  animate?: boolean;
}) {
  const [localOpen, setLocalOpen] = useState(true);
  return <SidebarContext.Provider value={{ open: controlledOpen ?? localOpen, setOpen: controlledSetOpen ?? setLocalOpen, animate }}>{children}</SidebarContext.Provider>;
}
export const Sidebar = SidebarProvider;

// Native anchors work with Vite and the dashboard's existing routes.
export function SidebarLink({ link, className, ...props }: React.ComponentProps<"a"> & {
  link: { label: string; href: string; icon: React.ReactNode };
}) {
  const { open, animate } = useSidebar();
  const reduced = useReducedMotion();
  return <a href={link.href} aria-label={link.label} title={!open ? link.label : undefined} className={cn("sidebar-link", className)} {...props}>
    {link.icon}<motion.span animate={{ opacity: open || !animate ? 1 : 0 }} transition={{ duration: reduced ? 0 : .18 }} hidden={!open && animate}>{link.label}</motion.span>
  </a>;
}

export function SidebarTrigger({ mobile = false, ...props }: React.ComponentProps<typeof motion.button> & { mobile?: boolean }) {
  const { open, setOpen, animate } = useSidebar();
  const reduced = useReducedMotion();
  const Icon = mobile ? (open ? X : Menu) : (open ? PanelLeftClose : PanelLeftOpen);
  return <motion.button {...props} type="button" className={cn("sidebar-toggle", props.className)} aria-controls="dashboard-sidebar" aria-expanded={open}
    aria-label={open ? "Close sidebar" : "Open sidebar"} title={open ? "Close sidebar" : "Open sidebar"}
    onClick={() => setOpen(value => !value)} whileTap={animate && !reduced ? { scale: .94 } : undefined}>
    <Icon size={19} aria-hidden="true" />
  </motion.button>;
}
