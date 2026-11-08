import React, { Children, forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { ArrowRight, Mail, Lock, Eye, EyeOff, ArrowLeft, X, AlertCircle, PartyPopper, Loader } from "lucide-react";
import { BrandIcon } from "@/components/ui/brand-icon";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { Transition, Variants } from "framer-motion";
import confetti from "canvas-confetti";
import type { CreateTypes, Options } from "canvas-confetti";

type ConfettiApi = { fire: (options?: Options) => void };
export type ConfettiRef = ConfettiApi | null;

const Confetti = forwardRef<ConfettiApi, React.ComponentPropsWithoutRef<"canvas">>((props, ref) => {
  const canvas = useRef<HTMLCanvasElement>(null);
  const instance = useRef<CreateTypes | null>(null);
  useEffect(() => {
    if (canvas.current) {
      instance.current = confetti.create(canvas.current, { resize: true, useWorker: false });
    }
    return () => { instance.current?.reset(); instance.current = null; };
  }, []);
  const fire = useCallback((options: Options = {}) => {
    void instance.current?.({ ...options, disableForReducedMotion: true });
  }, []);
  useImperativeHandle(ref, () => ({ fire }), [fire]);
  return <canvas ref={canvas} {...props} aria-hidden="true" />;
});
Confetti.displayName = "Confetti";

export function TextLoop({ children, interval = 2, className, transition = { duration: 0.3 }, variants }: {
  children: React.ReactNode[];
  interval?: number;
  className?: string;
  transition?: Transition;
  variants?: Variants;
}) {
  const [index, setIndex] = useState(0);
  const items = useMemo(() => Children.toArray(children), [children]);
  const reducedMotion = useReducedMotion();
  useEffect(() => {
    if (items.length < 2 || reducedMotion) return;
    const timer = window.setInterval(() => setIndex(current => (current + 1) % items.length), interval * 1000);
    return () => window.clearInterval(timer);
  }, [interval, items.length, reducedMotion]);
  return <div className={cn("relative", className)} aria-live="polite">
    <AnimatePresence mode="wait" initial={false}>
      <motion.div key={index} initial="initial" animate="animate" exit="exit" transition={transition}
        variants={variants ?? { initial: { opacity: 0, y: 6 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -6 } }}>
        {items[index % Math.max(items.length, 1)]}
      </motion.div>
    </AnimatePresence>
  </div>;
}

const glassButtonVariants = cva("glass-button inline-flex items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-45", {
  variants: { size: { default: "min-h-12 w-full px-6 py-3 text-sm font-medium", sm: "px-4 py-2 text-sm", icon: "h-11 w-11" } },
  defaultVariants: { size: "default" },
});

interface GlassButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof glassButtonVariants> {}
const GlassButton = forwardRef<HTMLButtonElement, GlassButtonProps>(({ className, size, ...props }, ref) => (
  <button ref={ref} className={cn(glassButtonVariants({ size }), className)} {...props} />
));
GlassButton.displayName = "GlassButton";

function BlurFade({ children, className }: { children: React.ReactNode; className?: string }) {
  const reducedMotion = useReducedMotion();
  return <motion.div initial={{ opacity: 0, y: reducedMotion ? 0 : 8, filter: reducedMotion ? "none" : "blur(6px)" }}
    animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} exit={{ opacity: 0 }}
    transition={{ duration: reducedMotion ? 0 : 0.25 }} className={className}>{children}</motion.div>;
}

function GradientBackground() {
  return <svg aria-hidden="true" viewBox="0 0 800 600" preserveAspectRatio="xMidYMid slice" className="auth-gradient absolute inset-0 h-full w-full">
    <defs>
      <linearGradient id="auth-grad-1"><stop stopColor="#8b5cf6" /><stop offset="1" stopColor="#38bdf8" /></linearGradient>
      <linearGradient id="auth-grad-2"><stop stopColor="#db2777" /><stop offset="1" stopColor="#fbbf24" /></linearGradient>
      <filter id="auth-blur"><feGaussianBlur stdDeviation="65" /></filter>
    </defs>
    <g filter="url(#auth-blur)" opacity=".45">
      <ellipse cx="100" cy="450" rx="320" ry="180" fill="url(#auth-grad-1)" />
      <ellipse cx="700" cy="200" rx="300" ry="180" fill="url(#auth-grad-2)" />
    </g>
  </svg>;
}

interface AuthComponentProps {
  logo?: React.ReactNode;
  brandName?: string;
  mode?: "signup" | "login";
}

export const AuthComponent = ({ logo, brandName = "Zyvello", mode = "signup" }: AuthComponentProps) => {
  const signup = mode === "signup";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);
  const [step, setStep] = useState<"email" | "password" | "confirmPassword">("email");
  const [status, setStatus] = useState<"closed" | "loading" | "error" | "success">("closed");
  const [message, setMessage] = useState("");
  const [ownerExists, setOwnerExists] = useState(false);
  const [checking, setChecking] = useState(true);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const confettiRef = useRef<ConfettiRef>(null);
  const submitting = useRef(false);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (step !== "email") inputRef.current?.focus();
  }, [step]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/auth/session", { credentials: "same-origin", signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("Unable to check your account. Refresh to try again.");
        const data = await response.json();
        if (data.user) window.location.replace("/");
        setOwnerExists(!data.setup_required);
        setChecking(false);
      })
      .catch(error => {
        if (error.name !== "AbortError") {
          setChecking(false);
          setMessage(error.message);
          setStatus("error");
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (status !== "closed") dialog.current?.showModal();
    else dialog.current?.close();
    if (status === "success" && signup && !reducedMotion) {
      confettiRef.current?.fire({ particleCount: 70, spread: 90, origin: { x: 0.5, y: 0.7 } });
    }
  }, [status, signup, reducedMotion]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current || checking) return;
    if (step === "email") { setStep("password"); return; }
    if (signup && step === "password") { setStep("confirmPassword"); return; }
    if (signup && password !== confirmPassword) {
      setMessage("Passwords do not match. Please try again.");
      setStatus("error");
      return;
    }
    submitting.current = true;
    setStatus("loading");
    try {
      const response = await fetch(signup ? "/api/auth/signup" : "/api/auth/login", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, ...(signup ? { confirm_password: confirmPassword } : {}) }),
        signal: AbortSignal.timeout(20000),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.message || data.detail?.[0]?.msg || "Unable to sign in. Please try again.");
      }
      setPassword("");
      setConfirmPassword("");
      setStatus("success");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Unable to connect. Please try again.");
      setStatus("error");
    } finally {
      submitting.current = false;
    }
  }

  function goBack() {
    setStep(step === "confirmPassword" ? "password" : "email");
    setConfirmPassword("");
  }

  const title = step === "email" ? (signup ? "Start with Zyvello." : "Welcome back.")
    : step === "password" ? (signup ? "Create your password." : "Enter your password.") : "One last step.";

  return (
    <div className="auth-page relative isolate flex min-h-dvh flex-col overflow-hidden bg-black text-white">
      <GradientBackground />
      <Confetti ref={confettiRef} className="pointer-events-none fixed inset-0 z-50 h-full w-full" />
      <header className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between gap-3 px-5 py-7 md:px-8">
        <a href="/welcome" className="flex items-center gap-2.5 text-xl font-semibold">
          {logo ?? <BrandIcon />}
          {brandName}
        </a>
        <a href="/welcome" className="inline-flex items-center gap-2 text-sm text-gray-300"><ArrowLeft className="h-4 w-4" aria-hidden="true" />Back to home</a>
      </header>
      <main className="relative z-10 flex flex-1 items-center justify-center px-5 py-12">
        <div className="auth-glass-card w-full max-w-md rounded-3xl border border-white/15 bg-black/45 p-6 shadow-2xl backdrop-blur-2xl sm:p-10">
          <AnimatePresence mode="wait" initial={false}>
            <BlurFade key={step}>
              <p className="text-xs uppercase tracking-[0.2em] text-gray-400">{signup ? "Create account" : "Log in"}</p>
              <h1 className="mt-4 text-3xl font-medium tracking-tight sm:text-4xl">{title}</h1>
              <p className="mt-3 text-sm leading-6 text-gray-400">
                {step === "email" ? (signup ? "Create the owner account for your local Zyvello workspace." : "Sign in to continue to your workspace.")
                  : step === "password" ? "Use at least 8 characters." : "Confirm your password to create your account."}
              </p>
            </BlurFade>
          </AnimatePresence>
          {checking ? <p role="status" className="mt-8 text-sm text-gray-300">Checking your account…</p>
            : signup && ownerExists ? <div className="mt-8 space-y-5">
              <p className="text-sm leading-6 text-gray-300">This installation already has an owner account. Log in to open the workspace.</p>
              <a href="/login" className="button-light w-full">Log in</a>
            </div> : <form onSubmit={submit} className="mt-8 space-y-5">
              <fieldset disabled={status !== "closed"} className="min-w-0 space-y-5">
                <div>
                  <label htmlFor="auth-email" className="mb-2 block text-sm text-gray-300">Email address</label>
                  <div className="glass-input">
                    <Mail className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
                    <input id="auth-email" type="email" autoComplete="email" autoCapitalize="none" spellCheck={false}
                      required maxLength={254} value={email} readOnly={step !== "email"}
                      onChange={event => setEmail(event.target.value)} className="min-w-0 flex-1 bg-transparent py-1 outline-none" placeholder="you@company.com" />
                  </div>
                </div>
                {step !== "email" && <div>
                  <label htmlFor="auth-password" className="mb-2 block text-sm text-gray-300">{step === "confirmPassword" ? "Confirm password" : "Password"}</label>
                  <div className="glass-input">
                    <Lock className="h-4 w-4 shrink-0 text-gray-400" aria-hidden="true" />
                    <input key={step} ref={inputRef} id="auth-password" type={(step === "confirmPassword" ? showConfirmPassword : showPassword) ? "text" : "password"}
                      autoComplete={signup ? "new-password" : "current-password"} required minLength={8} maxLength={256}
                      value={step === "confirmPassword" ? confirmPassword : password}
                      onChange={event => step === "confirmPassword" ? setConfirmPassword(event.target.value) : setPassword(event.target.value)}
                      className="min-w-0 flex-1 bg-transparent py-1 outline-none" />
                    <button type="button" aria-label="Toggle password visibility" className="rounded-full p-1"
                      onClick={() => step === "confirmPassword" ? setShowConfirmPassword(!showConfirmPassword) : setShowPassword(!showPassword)}>
                      {(step === "confirmPassword" ? showConfirmPassword : showPassword) ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>
                </div>}
                <GlassButton type="submit">{step === "email" || (signup && step === "password") ? "Continue" : signup ? "Create account" : "Log in"}<ArrowRight className="h-4 w-4" aria-hidden="true" /></GlassButton>
                {step !== "email" && <button type="button" onClick={goBack} className="flex items-center gap-2 text-sm text-gray-400"><ArrowLeft className="h-4 w-4" aria-hidden="true" />Go back</button>}
              </fieldset>
            </form>}
          <p className="mt-7 text-center text-sm text-gray-400">{signup ? "Already have an account?" : "New to Zyvello?"}{" "}
            <a href={signup ? "/login" : "/signup"} className="font-medium text-white underline underline-offset-4">{signup ? "Log in" : "Sign up"}</a>
          </p>
        </div>
      </main>
      <dialog ref={dialog} aria-label="Account status" onCancel={event => { event.preventDefault(); if (status !== "loading") setStatus("closed"); }}
        className="auth-dialog">
        <div className="relative flex flex-col items-center gap-5 p-8 text-center">
          {status !== "loading" && <button aria-label="Close message" onClick={() => setStatus("closed")} className="absolute right-3 top-3 rounded-full p-2"><X className="h-4 w-4" /></button>}
          {status === "loading" && <><Loader className="h-10 w-10 animate-spin" aria-hidden="true" /><TextLoop>
            {[<p key="save">{signup ? "Creating your account…" : "Signing you in…"}</p>, <p key="wait">Checking your details…</p>]}
          </TextLoop></>}
          {status === "error" && <><AlertCircle className="h-10 w-10 text-rose-300" aria-hidden="true" /><p role="alert">{message}</p><GlassButton onClick={() => setStatus("closed")}>Try again</GlassButton></>}
          {status === "success" && <><PartyPopper className="h-10 w-10 text-emerald-300" aria-hidden="true" /><h2 className="text-xl font-medium">{signup ? "Welcome to Zyvello!" : "You're signed in."}</h2>
            <p className="text-sm text-gray-400">Your workspace is ready to open.</p><a href="/" className="button-light w-full">Open dashboard</a></>}
        </div>
      </dialog>
    </div>
  );
};
