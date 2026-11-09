import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight, LockKeyhole, Mail, ShieldCheck } from "lucide-react";
import { BrandIcon } from "@/components/ui/brand-icon";
import { authClient, hostedSignUpEnabled } from "./auth-client";

type Account = { id: string; email: string; name?: string };
type Page = "login" | "signup" | "app";

// Separate from the single-owner local API. Deliberately no Harvey/dashboard
// imports, and no access to customer data until workspace APIs are verified.
export function HostedAuthPage({ page }: { page: Page }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [checking, setChecking] = useState(true);
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [otp, setOtp] = useState("");
  const [awaitingVerification, setAwaitingVerification] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    if (!authClient) {
      setError("Hosted authentication is not configured for this deployment.");
      setChecking(false);
      return;
    }
    authClient.getSession()
      .then(({ data, error: sessionError }) => {
        if (!active) return;
        if (sessionError) {
          setError("Unable to check your session. Please refresh and try again.");
        } else if (data?.session && data?.user) {
          setAccount({ id: data.user.id, email: data.user.email, name: data.user.name });
        }
      })
      .catch(() => {
        if (active) setError("Authentication service is unavailable. Please try again later.");
      })
      .finally(() => {
        if (active) setChecking(false);
      });
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authClient || busy || (page === "signup" && !hostedSignUpEnabled)) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = page === "signup"
        ? await authClient.signUp.email({ name: name.trim() || email.split("@")[0], email: email.trim(), password })
        : await authClient.signIn.email({ email: email.trim(), password });
      if (result.error) throw new Error(result.error.message || "Authentication failed.");
      setPassword("");
      if (page === "signup" && result.data?.user && !result.data.user.emailVerified) {
        setAwaitingVerification(true);
        setMessage("Enter the one-time verification code sent to your email.");
        return;
      }
      const session = await authClient.getSession();
      if (session.error || !session.data?.session || !session.data?.user) {
        setMessage("Account created or accepted. Please sign in after verifying your email.");
        return;
      }
      window.location.assign("/app");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to authenticate. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function verifyEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authClient || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await authClient.emailOtp.verifyEmail({ email: email.trim(), otp: otp.trim() });
      if (result.error) throw new Error(result.error.message || "The code could not be verified.");
      setOtp("");
      setAwaitingVerification(false);
      setMessage("Email verified. Please sign in to continue.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to verify your code.");
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    if (!authClient || busy) return;
    setBusy(true);
    setError("");
    try {
      const result = await authClient.signOut();
      if (result.error) throw new Error(result.error.message || "Sign out failed.");
      setAccount(null);
      window.location.replace("/login");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not sign out.");
    } finally {
      setBusy(false);
    }
  }

  const isSignUp = page === "signup";
  return (
    <div className="auth-page relative isolate flex min-h-dvh flex-col overflow-hidden bg-black text-white">
      <div aria-hidden="true" className="pointer-events-none absolute -left-32 top-1/3 h-96 w-96 rounded-full bg-violet-700/25 blur-3xl" />
      <div aria-hidden="true" className="pointer-events-none absolute right-0 top-0 h-80 w-80 rounded-full bg-sky-600/20 blur-3xl" />
      <header className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-5 py-7 md:px-8">
        <a href="/welcome" className="flex items-center gap-2.5 text-xl font-semibold"><BrandIcon /> Zyvello</a>
        <a href="/welcome" className="inline-flex items-center gap-2 text-sm text-gray-300"><ArrowLeft className="h-4 w-4" /> Home</a>
      </header>
      <main className="relative z-10 flex flex-1 items-center justify-center px-5 py-12">
        <section className="auth-glass-card w-full max-w-md rounded-3xl border border-white/15 bg-black/50 p-6 shadow-2xl backdrop-blur-2xl sm:p-10" aria-label="Hosted Zyvello account">
          <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-violet-400/25 px-3 py-1 text-xs text-violet-200"><ShieldCheck className="h-3.5 w-3.5" /> Hosted private beta</p>
          {checking ? <p role="status" className="text-sm text-gray-300">Checking your session…</p>
            : !authClient ? <><h1 className="text-2xl font-semibold">Account access unavailable</h1><p className="mt-4 text-gray-300">This preview has not been connected to an authentication endpoint.</p></>
            : page === "app" && account ? <>
              <h1 className="text-3xl font-semibold">You're signed in.</h1>
              <p className="mt-4 break-words text-gray-300">{account.name || account.email}<span className="mt-1 block text-sm text-gray-400">{account.email}</span></p>
              <p className="mt-6 rounded-xl border border-amber-300/20 bg-amber-300/5 p-4 text-sm text-gray-300">The hosted workspace, background jobs and outreach are not enabled yet. No emails or AI actions will be run from this preview.</p>
              <button className="button-light mt-6 w-full" type="button" disabled={busy} onClick={() => void signOut()}>{busy ? "Signing out…" : "Sign out"}</button>
            </> : page === "app" ? <>
              <h1 className="text-3xl font-semibold">Sign in required</h1>
              <p className="mt-4 text-sm text-gray-300">A verified account is required before opening your workspace.</p>
              <a href="/login" className="button-light mt-6 w-full">Go to login</a>
            </> : isSignUp && !hostedSignUpEnabled ? <>
              <h1 className="text-3xl font-semibold">Private beta access</h1>
              <p className="mt-4 text-sm leading-6 text-gray-300">New accounts are currently invitation-only while we test workspace security. Public registration is not yet open.</p>
              <a href="/login" className="button-light mt-6 w-full">Already invited? Log in</a>
            </> : awaitingVerification ? <>
              <h1 className="text-3xl font-semibold">Verify your email</h1>
              <p className="mt-4 text-sm text-gray-300">Check {email} for your one-time code.</p>
              <form onSubmit={event => void verifyEmail(event)} className="mt-6 space-y-4">
                <label className="block text-sm text-gray-200" htmlFor="hosted-otp">Verification code</label>
                <input id="hosted-otp" className="glass-input w-full bg-transparent" type="text" inputMode="numeric" autoComplete="one-time-code" required maxLength={12} value={otp} onChange={event => setOtp(event.target.value)} />
                <button className="button-light w-full" type="submit" disabled={busy}>{busy ? "Verifying…" : "Verify email"}</button>
              </form>
            </> : <>
              <h1 className="text-3xl font-semibold">{isSignUp ? "Create your account." : "Welcome back."}</h1>
              <p className="mt-3 text-sm text-gray-400">{isSignUp ? "Verify your email before accessing Zyvello." : "Sign in securely to your Zyvello account."}</p>
              <form onSubmit={event => void submit(event)} className="mt-8 space-y-5">
                {isSignUp && <div><label htmlFor="hosted-name" className="mb-2 block text-sm text-gray-300">Name</label><input id="hosted-name" className="glass-input w-full bg-transparent" value={name} onChange={event => setName(event.target.value)} maxLength={100} autoComplete="name" required /></div>}
                <div><label htmlFor="hosted-email" className="mb-2 block text-sm text-gray-300">Email address</label><div className="glass-input"><Mail className="h-4 w-4 shrink-0 text-gray-400" /><input id="hosted-email" className="min-w-0 flex-1 bg-transparent py-1 outline-none" type="email" autoComplete="email" required maxLength={254} value={email} onChange={event => setEmail(event.target.value)} /></div></div>
                <div><label htmlFor="hosted-password" className="mb-2 block text-sm text-gray-300">Password</label><div className="glass-input"><LockKeyhole className="h-4 w-4 shrink-0 text-gray-400" /><input id="hosted-password" className="min-w-0 flex-1 bg-transparent py-1 outline-none" type="password" autoComplete={isSignUp ? "new-password" : "current-password"} required minLength={8} maxLength={256} value={password} onChange={event => setPassword(event.target.value)} /></div></div>
                <button className="button-light flex w-full items-center justify-center gap-2" type="submit" disabled={busy}>{busy ? "Please wait…" : isSignUp ? "Create account" : "Log in"}<ArrowRight className="h-4 w-4" /></button>
              </form>
              <p className="mt-6 text-center text-sm text-gray-400">{isSignUp ? "Already have an account?" : "Need access?"} <a className="font-medium text-white underline underline-offset-4" href={isSignUp ? "/login" : "/signup"}>{isSignUp ? "Log in" : "Private beta details"}</a></p>
            </>}
          {message && <p role="status" className="mt-5 text-sm text-emerald-300">{message}</p>}
          {error && <p role="alert" className="mt-5 rounded-lg border border-rose-400/30 bg-rose-950/40 p-3 text-sm text-rose-200">{error}</p>}
        </section>
      </main>
    </div>
  );
}
