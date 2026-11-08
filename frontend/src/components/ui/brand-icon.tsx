import { cn } from "@/lib/utils";

export function BrandIcon({ className }: { className?: string }) {
  return <img src="/static/favicon.svg?v=20261107" width="32" height="32" alt="" aria-hidden="true"
    className={cn("zyvello-brand-icon h-8 w-8 shrink-0 rounded-lg", className)} />;
}
