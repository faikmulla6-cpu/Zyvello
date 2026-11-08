import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva("inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-xl text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-200 disabled:pointer-events-none disabled:opacity-50", {
  variants: { variant: {
    default: "border border-white/20 bg-white text-black hover:bg-white/90",
    destructive: "bg-red-500/15 text-red-200",
    outline: "border border-white/15 bg-white/[0.04] text-white hover:bg-white/10",
    secondary: "bg-white/10 text-white hover:bg-white/15",
    ghost: "text-white/70 hover:bg-white/5 hover:text-white",
    link: "text-white underline-offset-4 hover:underline",
  }, size: { default: "h-11 px-5 py-2", sm: "h-9 px-3", lg: "h-12 px-7", icon: "h-10 w-10" } },
  defaultVariants: { variant: "default", size: "default" },
});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean }
const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild = false, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
});
Button.displayName = "Button";
export { Button, buttonVariants };
