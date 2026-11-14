import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, CircleAlert, RefreshCw } from "lucide-react";
import { HostedCampaignPilot, type CampaignDeliveryData } from "./HostedCampaignPilot";
import { HostedGmailConnection, type GmailStatus } from "./HostedGmailConnection";
import { gmailReturn } from "./gmail-return";
import { HostedGroqConnection, type GroqStatus } from "./HostedGroqConnection";
import { selectedPlan } from "./plan-selection";
import { authClient } from "./auth-client";
import { HostedCommandDeck, readDeckSection, type DeckSection } from "./HostedCommandDeck";

type WorkspaceSettings = { industry: string; website_url: string; timezone: string };
type Workspace = {
  id: string; slug: string; name: string; role: string; created_at: string;
  settings?: Partial<WorkspaceSettings>;
};
type WorkspaceMember = { auth_user_id: string; email: string | null; role: string };
type WorkspaceInvitation = {
  id: string; invitee_email: string; expires_at: string; accepted_at?: string | null; revoked_at?: string | null;
};
type Contact = { id: string; full_name: string; email: string; company: string; created_at: string };
type Campaign = { id: string; name: string; goal: string; status: "draft"; created_at: string };
type GeneratedDraft = { id: string; campaign_id: string; contact_id: string; draft_text: string };
type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead";
  attempts: number;
  max_attempts: number;
  result: { outcome?: string; note?: string } | null;
  last_error: string | null;
  created_at: string;
};
type BillingMode = "test" | "live";

type BillingPlan = {
  code: string; name: string; amount_paise: number; currency: "INR"; period: string; interval: number;
  taxes: "additional_if_applicable"; test_checkout_available: boolean; live_checkout_available: boolean;
  limits: { stored_contacts: number; stored_campaign_drafts: number; workspace_members_including_owner: number };
  available_features: string[]; unavailable_features: string[];
};
type BillingSubscription = {
  id: string; plan_code: string; status: string; checkout_verified: boolean;
  paid_through: string | null; paid_count: number; entitled: boolean;
};
type RazorpayCheckoutResult = { razorpay_payment_id: string; razorpay_signature: string };
type RazorpayOptions = {
  key: string; subscription_id: string; name: string; description: string;
  handler: (result: RazorpayCheckoutResult) => void;
  modal: { ondismiss: () => void };
};

declare global {
  interface Window { Razorpay?: new (options: RazorpayOptions) => { open: () => void } }
}

function loadCheckoutScript(): Promise<void> {
  if (window.Razorpay) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const prior = document.querySelector<HTMLScriptElement>('script[data-zyvello-razorpay="1"]');
    const script = prior || document.createElement("script");
    script.onload = () => window.Razorpay ? resolve() : reject(new Error("Checkout could not initialize"));
    script.onerror = () => reject(new Error("Razorpay Checkout could not load"));
    if (!prior) {
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.async = true;
      script.dataset.zyvelloRazorpay = "1";
      document.head.appendChild(script);
    }
  });
}

function hostedApiUrl(path: string): string {
  const raw = import.meta.env.VITE_ZYVELLO_API_URL;
  if (typeof raw !== "string") throw new Error("The hosted workspace API is not configured.");
  let base: URL;
  try {
    base = new URL(raw);
  } catch {
    throw new Error("The hosted workspace API URL is invalid.");
  }
  if (base.protocol !== "https:" || !base.hostname || base.username || base.password ||
      base.search || base.hash || base.pathname !== "/" || !path.startsWith("/api/v1/")) {
    throw new Error("The hosted workspace API URL is not allowed.");
  }
  return new URL(path, base).toString();
}

async function requestApi<T>(path: string, init?: RequestInit): Promise<T> {
  if (!authClient) throw new Error("Authentication is unavailable");
  // Neon Auth's Better Auth adapter exposes the signed token on its session.
  // The SDK's internal getJWTToken() reads this exact field.
  const session = await authClient.getSession();
  const token = session.data?.session?.token;
  if (!token) {
    throw new Error("Your session could not be verified. Please sign in again.");
  }
  const response = await fetch(hostedApiUrl(path), {
    ...init,
    credentials: "omit",
    redirect: "error",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...init?.headers,
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function BillingPanel({ workspaceId, owner, onEntitlementChange }: { workspaceId: string; owner: boolean; onEntitlementChange: (paid: boolean) => void }) {
  const requestedPlan = selectedPlan();
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [subscriptions, setSubscriptions] = useState<BillingSubscription[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [busyPlan, setBusyPlan] = useState("");
  const [cancellingId, setCancellingId] = useState("");
  const [confirmCancelId, setConfirmCancelId] = useState("");
  const [feedback, setFeedback] = useState("");
  const [billingError, setBillingError] = useState("");
  const [checkingBilling, setCheckingBilling] = useState(true);
  const [billingMode, setBillingMode] = useState<BillingMode>("test");
  const livePilot = billingMode === "live";

  const refresh = useCallback(async () => {
    const [configured, status] = await Promise.all([
      requestApi<{ mode: BillingMode; plans: BillingPlan[] }>(`/api/v1/workspaces/${workspaceId}/billing/plans`),
      requestApi<{ mode: BillingMode; subscriptions: BillingSubscription[] }>(`/api/v1/workspaces/${workspaceId}/billing`),
    ]);
    if ((configured.mode !== "test" && configured.mode !== "live") || status.mode !== configured.mode) {
      throw new Error("Workspace billing mode could not be confirmed");
    }
    return { mode: configured.mode, plans: configured.plans, subscriptions: status.subscriptions };
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void refresh().then(result => {
      if (!cancelled) {
        setBillingMode(result.mode);
        setPlans(result.plans);
        setSubscriptions(result.subscriptions);
        onEntitlementChange(result.subscriptions.some(sub => sub.entitled));
        setEnabled(true);
      }
    }).catch(cause => {
      if (!cancelled) {
        setEnabled(false);
        setBillingError(cause instanceof Error ? cause.message : "Billing configuration check failed");
      }
    }).finally(() => {
      if (!cancelled) setCheckingBilling(false);
    });
    return () => { cancelled = true; };
  }, [refresh, onEntitlementChange]);

  async function refreshBilling() {
    try {
      const result = await refresh();
      setBillingMode(result.mode);
      setPlans(result.plans);
      setSubscriptions(result.subscriptions);
      onEntitlementChange(result.subscriptions.some(sub => sub.entitled));
      setBillingError("");
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : "Could not refresh billing");
    }
  }

  async function tryCheckout(plan: BillingPlan) {
    const checkoutAvailable = livePilot
      ? plan.code === "starter" && plan.amount_paise === 49_900 && plan.live_checkout_available
      : plan.test_checkout_available;
    if (!owner || !enabled || busyPlan || !checkoutAvailable) return;
    setBusyPlan(plan.code);
    let checkoutOpened = false;
    setFeedback("");
    setBillingError("");
    try {
      const created = await requestApi<
        { status: "pending_reconciliation" } |
        { mode: BillingMode; key_id: string; subscription_id: string; intent_id: string; status: "checkout_ready" }
      >(`/api/v1/workspaces/${workspaceId}/billing/subscriptions`, {
        method: "POST",
        body: JSON.stringify({ plan_code: plan.code, idempotency_key: crypto.randomUUID().replace(/-/g, "") }),
      });
      if (created.status === "pending_reconciliation") {
        setBillingError("This checkout is awaiting provider reconciliation. Contact support before trying another payment.");
        return;
      }
      if (created.mode !== billingMode || !created.key_id.startsWith(`rzp_${billingMode}_`)
          || !created.subscription_id.startsWith("sub_")) {
        throw new Error("Checkout safety check failed");
      }
      await loadCheckoutScript();
      if (!window.Razorpay) throw new Error("Razorpay Checkout is unavailable");
      const checkout = new window.Razorpay({
        modal: { ondismiss: () => setBusyPlan("") },
        key: created.key_id,
        subscription_id: created.subscription_id,
        name: livePilot ? "Zyvello" : "Zyvello — TEST MODE",
        description: livePilot ? "Starter · ₹499 per month" : `${plan.name} (test payment; no live charges)`,
        handler: result => {
          void requestApi(`/api/v1/workspaces/${workspaceId}/billing/subscriptions/${created.intent_id}/verify`, {
            method: "POST",
            body: JSON.stringify({ razorpay_payment_id: result.razorpay_payment_id,
              razorpay_signature: result.razorpay_signature }),
          }).then(async () => {
            setFeedback("Checkout verified. Waiting for Razorpay's signed payment webhook…");
            for (let attempt = 0; attempt < 5; attempt += 1) {
              const refreshed = await refresh();
              setSubscriptions(refreshed.subscriptions);
              onEntitlementChange(refreshed.subscriptions.some(sub => sub.entitled));
              const latest = refreshed.subscriptions.find(sub => sub.id === created.intent_id);
              if (latest?.entitled) {
                setFeedback(livePilot ? "Payment confirmed. Starter access is active." : "Test payment confirmed. Test access is active.");
                return;
              }
              if (attempt < 4) await new Promise(resolve => window.setTimeout(resolve, 1500));
            }
            setFeedback("Checkout verified, but payment confirmation is still pending. Use Refresh billing to check again.");
          }).catch(cause => setBillingError(cause instanceof Error ? cause.message : "Checkout verification failed"))
            .finally(() => setBusyPlan(""));
        },
      });
      checkout.open();
      checkoutOpened = true;
      setFeedback(livePilot ? "Razorpay Live Checkout opened for Starter." : "Razorpay test Checkout opened. No real money should be collected.");
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : "Checkout could not start");
    } finally {
      if (!checkoutOpened) setBusyPlan("");
    }
  }

  async function cancelRenewal(sub: BillingSubscription) {
    if (!enabled || !owner || cancellingId || confirmCancelId !== sub.id) return;
    setCancellingId(sub.id);
    setBillingError("");
    setFeedback("");
    try {
      const response = await requestApi<{
        status: "already_ended" | "cancellation_requested"; at_cycle_end?: boolean;
      }>(`/api/v1/workspaces/${workspaceId}/billing/subscriptions/${sub.id}/cancel`, {
        method: "POST", body: JSON.stringify({}),
      });
      if (response.status === "already_ended") {
        setFeedback("This subscription has already ended.");
      } else if (response.status === "cancellation_requested" && response.at_cycle_end === true) {
        setFeedback("Razorpay received your request to cancel at the end of the billing cycle. The effective date is awaiting confirmation. Refresh billing to check the subscription status.");
      } else {
        throw new Error("The provider did not confirm the cancellation request.");
      }
      setConfirmCancelId("");
      const result = await refresh();
      setSubscriptions(result.subscriptions);
      onEntitlementChange(result.subscriptions.some(item => item.entitled));
    } catch (cause) {
      // Do not automatically retry an ambiguous provider cancellation.
      setBillingError(cause instanceof Error ? cause.message : "Cancellation was not confirmed. Please contact support.");
    } finally {
      setCancellingId("");
    }
  }

  return (
    <section className="card" aria-label="Workspace billing">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Billing</h2>
          <p className="mt-1 text-sm text-[var(--text-3)]">{livePilot ? "Private Starter pilot. Checkout authorizes recurring payments of ₹499 per month." : "Billing is managed separately for each workspace."}</p>
          <a href="/subscription-policy" className="mt-2 inline-block text-sm text-[var(--text-2)] underline underline-offset-4">Subscription policy</a>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="rounded-full border border-white/20 px-3 py-1 text-xs text-[var(--text)] hover:bg-white/10 disabled:opacity-40"
            disabled={!enabled} onClick={() => void refreshBilling()}>Refresh billing</button>
          {enabled && <span className="rounded-full border border-amber-400/30 px-3 py-1 text-xs text-[var(--wait)]">{livePilot ? "LIVE PILOT" : "TEST MODE"}</span>}
        </div>
      </div>
      {!enabled ? <p className="mt-4 text-sm text-[var(--text-2)]">{checkingBilling ? "Checking billing configuration…" : "Billing is unavailable for this workspace."}</p> : <>
        <div className="mt-5 space-y-2">
          {subscriptions.length === 0 ? <p className="text-sm text-[var(--text-3)]">No subscription for this workspace yet.</p> : subscriptions.map(sub => <div key={sub.id} className="inset rounded-xl p-3 text-sm">
            <span className="font-medium">{sub.plan_code}</span> · {sub.status} · {sub.entitled ? "Access active" : "No paid access"}
            {sub.paid_through && <p className="mt-1 text-xs text-[var(--text-3)]">Recorded period end: {new Date(sub.paid_through).toLocaleDateString()}</p>}
            {sub.status === "creating" && <p className="mt-2 text-xs text-[var(--wait)]">The provider response is not confirmed. Contact support before starting another checkout.</p>}
            {["pending", "halted"].includes(sub.status) && <p className="mt-2 text-xs text-[var(--wait)]">Payment recovery is needed. Paid tools remain locked, and a second checkout is blocked to prevent duplicate recurring subscriptions.</p>}
            {sub.status === "active" && !sub.entitled && <p className="mt-2 text-xs text-[var(--wait)]">Payment confirmation or the paid period is missing. No access has been granted.</p>}
            {["cancelled", "completed", "expired"].includes(sub.status) && <p className="mt-2 text-xs text-[var(--text-3)]">This subscription is no longer active; it does not unlock premium tools.</p>}
            {owner && !["creating", "cancelled", "completed", "expired"].includes(sub.status) && <div className="mt-3">
              {confirmCancelId === sub.id ? <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
                <p className="text-xs text-[var(--wait)]">Request cancellation at the end of this billing cycle? Check your subscription status for confirmation from Razorpay.</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="rounded-full border border-rose-300/45 px-3 py-2 text-xs text-[var(--bad)] disabled:opacity-40" disabled={Boolean(cancellingId)} onClick={() => void cancelRenewal(sub)}>
                    {cancellingId === sub.id ? "Requesting…" : "Confirm cancellation"}
                  </button>
                  <button type="button" className="rounded-full border border-white/25 px-3 py-2 text-xs text-[var(--text)]" disabled={Boolean(cancellingId)} onClick={() => setConfirmCancelId("")}>Keep subscription</button>
                </div>
              </div> : <button type="button" className="rounded-full border border-white/25 px-3 py-2 text-xs text-[var(--text)] hover:bg-white/10 disabled:opacity-40" disabled={Boolean(cancellingId) || Boolean(busyPlan)} onClick={() => setConfirmCancelId(sub.id)}>Cancel renewal</button>}
            </div>}
          </div>)}
        </div>
        {requestedPlan && plans.some(plan => plan.code === requestedPlan) && (
          <p className="mt-4 text-sm text-[var(--text-2)]">Selected on the pricing page: <strong>{plans.find(plan => plan.code === requestedPlan)?.name}</strong></p>
        )}
        {plans.length > 0 && <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {plans.map(plan => <div className={`inset rounded-2xl p-4 ${requestedPlan === plan.code ? "outline outline-2 outline-[var(--accent)]" : ""}`} key={plan.code}>
            <p className="font-medium">{plan.name}</p>
            <p className="mt-1 text-lg font-semibold">₹{(plan.amount_paise / 100).toLocaleString("en-IN")} <span className="text-xs font-normal text-[var(--text-3)]">/ month</span></p>
            <p className="mt-2 text-xs text-[var(--text-2)]">{plan.limits.stored_contacts.toLocaleString("en-IN")} stored contacts · {plan.limits.stored_campaign_drafts} campaign drafts · {plan.limits.workspace_members_including_owner} member(s) including owner</p>
            <p className="mt-1 text-xs text-[var(--text-3)]">Includes contact management and campaign planning.</p>
            {livePilot && !plan.live_checkout_available && <p className="mt-2 text-xs text-[var(--wait)]">Live checkout is disabled.</p>}
            {!livePilot && !plan.test_checkout_available && <p className="mt-2 text-xs text-[var(--wait)]">Matching Razorpay TEST plan is not connected yet.</p>}
            <button className="mt-4 rounded-full border border-violet-300/40 px-4 py-2 text-xs text-[var(--text)] hover:bg-white/10 disabled:opacity-40" type="button"
              disabled={!owner || !(livePilot ? plan.live_checkout_available : plan.test_checkout_available) || Boolean(busyPlan) || subscriptions.some(sub =>
                !["cancelled", "completed", "expired"].includes(sub.status) && !(livePilot && sub.status === "created"))}
              onClick={() => void tryCheckout(plan)}>{busyPlan === plan.code ? "Opening…" : plan.live_checkout_available ? "Open live checkout" : plan.test_checkout_available ? "Try test checkout" : "Checkout unavailable"}</button>
          </div>)}
        </div>}
        {!livePilot && <p className="mt-4 text-sm text-[var(--text-2)]"><strong>AI Employee</strong> — planned from ₹9,999/month.</p>}
        {!owner && <p className="mt-3 text-xs text-[var(--text-3)]">Only the workspace owner can manage billing.</p>}
      </>}
      {feedback && <p role="status" className="mt-4 text-xs text-[var(--good)]">{feedback}</p>}
      {billingError && <p role="alert" className="mt-4 text-xs text-[var(--bad)]">{billingError}</p>}
    </section>
  );
}

function WorkspaceSettingsPanel({ workspace, onSaved }: { workspace: Workspace; onSaved: (workspace: Workspace) => void }) {
  const [name, setName] = useState(workspace.name);
  const [industry, setIndustry] = useState(workspace.settings?.industry ?? "");
  const [website, setWebsite] = useState(workspace.settings?.website_url ?? "");
  const [timezone, setTimezone] = useState(workspace.settings?.timezone ?? "Asia/Kolkata");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setMessage("");
    setError("");
    if (!name.trim() || !timezone.trim()) {
      setError("Enter a business name and timezone.");
      return;
    }
    setSaving(true);
    try {
      const result = await requestApi<{ workspace: Workspace }>(`/api/v1/workspaces/${workspace.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          settings: { industry: industry.trim(), website_url: website.trim(), timezone: timezone.trim() },
        }),
      });
      onSaved(result.workspace);
      setName(result.workspace.name);
      setIndustry(result.workspace.settings?.industry ?? "");
      setWebsite(result.workspace.settings?.website_url ?? "");
      setTimezone(result.workspace.settings?.timezone ?? "Asia/Kolkata");
      setMessage("Workspace settings saved.");
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : "";
      setError(detail === "Request failed (422)"
        ? "Check the website and timezone. Use a public HTTPS website and a timezone such as Asia/Kolkata."
        : detail || "Could not save workspace settings.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="workspace-settings-title" className="card">
      <h2 id="workspace-settings-title" className="text-xl font-semibold">Workspace settings</h2>
      <form className="mt-4 space-y-4" onSubmit={event => void save(event)}>
        <fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm text-[var(--text-2)]">Business name
            <input className="form-input mt-2 w-full" value={name} onChange={event => setName(event.target.value)} maxLength={200} required />
          </label>
          <label className="block text-sm text-[var(--text-2)]">Industry (optional)
            <input className="form-input mt-2 w-full" value={industry} onChange={event => setIndustry(event.target.value)} maxLength={100} />
          </label>
          <label className="block text-sm text-[var(--text-2)]">Website (optional)
            <input className="form-input mt-2 w-full" type="url" value={website} onChange={event => setWebsite(event.target.value)} maxLength={300} placeholder="https://example.com" />
          </label>
          <label className="block text-sm text-[var(--text-2)]">Timezone
            <input className="form-input mt-2 w-full" value={timezone} onChange={event => setTimezone(event.target.value)} minLength={3} maxLength={64} placeholder="Asia/Kolkata" required />
          </label>
        </fieldset>
        <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "Saving…" : "Save settings"}</button>
        {message && <p role="status" className="text-sm text-[var(--good)]">{message}</p>}
        {error && <p role="alert" className="text-sm text-[var(--bad)]">{error}</p>}
      </form>
    </section>
  );
}

function WorkspaceTeamPanel({ workspace, entitled }: { workspace: Workspace; entitled: boolean }) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const path = `/api/v1/workspaces/${workspace.id}`;
  const canManage = workspace.role === "owner" || workspace.role === "admin";

  useEffect(() => {
    let active = true;
    Promise.all([
      requestApi<{ members: WorkspaceMember[] }>(path + "/members"),
      canManage ? requestApi<{ invitations: WorkspaceInvitation[] }>(path + "/invitations") : Promise.resolve({ invitations: [] }),
    ]).then(([roster, invites]) => {
      if (!active) return;
      setMembers(roster.members);
      setInvitations(invites.invitations);
    }).catch(cause => {
      if (!active) return;
      setLoadFailed(true);
      setError(cause instanceof Error ? cause.message : "Could not load the team.");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [canManage, path]);

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !canManage || !entitled) return;
    setBusy(true);
    setError("");
    try {
      const result = await requestApi<{ invitation: WorkspaceInvitation; token: string }>(path + "/invitations", {
        method: "POST", body: JSON.stringify({ email: email.trim(), role: "member" }),
      });
      setInvitations(current => [result.invitation, ...current]);
      setCode(result.token);
      setEmail("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the invitation.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id: string) {
    if (busy || !canManage) return;
    setBusy(true);
    setError("");
    try {
      const result = await requestApi<{ invitation: Pick<WorkspaceInvitation, "id" | "revoked_at"> }>(`${path}/invitations/${id}/revoke`, { method: "POST" });
      setInvitations(current => current.map(item => item.id === id ? { ...item, revoked_at: result.invitation.revoked_at } : item));
      setCode("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not revoke the invitation.");
    } finally {
      setBusy(false);
    }
  }

  const pending = invitations.filter(item => !item.accepted_at && !item.revoked_at && Date.parse(item.expires_at) > Date.now());

  return (
    <details className="card">
      <summary className="cursor-pointer text-xl font-semibold">Workspace team</summary>
      {loading && <p role="status" className="mt-4 text-sm text-[var(--text-2)]">Loading workspace team…</p>}
      {!loading && !loadFailed && (
        <ul aria-label="Workspace members" className="mt-4 space-y-2 text-sm text-[var(--text-2)]">
          {members.map(member => <li key={member.auth_user_id} className="break-all">{member.email || "Workspace member"} · {member.role}</li>)}
        </ul>
      )}
      {canManage && entitled && !loading && <form className="mt-4 space-y-3" onSubmit={event => void invite(event)}>
        <label className="block text-sm text-[var(--text-2)]">Teammate email
          <input className="form-input mt-2 w-full" value={email} onChange={event => setEmail(event.target.value)} type="email" maxLength={254} required />
        </label>
        <button type="submit" className="btn btn-primary" disabled={busy}>Create invitation</button>
      </form>}
      {code && <label className="mt-4 block text-sm text-[var(--text-2)]">Share this one-time code privately with the invited teammate
        <input className="form-input mt-2 w-full" value={code} onFocus={event => event.currentTarget.select()} readOnly />
      </label>}
      {pending.length > 0 && <ul aria-label="Pending invitations" className="mt-4 space-y-2 text-sm text-[var(--text-2)]">
        {pending.map(item => <li key={item.id} className="flex flex-wrap items-center gap-3">
          <span className="break-all">{item.invitee_email}</span>
          <button type="button" className="underline underline-offset-4" disabled={busy} onClick={() => void revoke(item.id)}>Revoke invitation</button>
        </li>)}
      </ul>}
      {error && <p role="alert" className="mt-4 text-sm text-[var(--bad)]">{error}</p>}
    </details>
  );
}

type DashboardProps = {
  email: string;
  sessionError: string;
  onSignOut: () => void;
  signingOut: boolean;
};

const pendingSections: Partial<Record<DeckSection, string>> = {
  signals: "Hosted lead signals are in development.",
  discover: "Hosted prospect discovery is in development.",
  companies: "The company directory will be available when hosted prospect discovery is connected.",
};

export function HostedWorkspaceDashboard({ email, sessionError, onSignOut, signingOut }: DashboardProps) {
  const [section, setSection] = useState(readDeckSection);
  const currentWorkspace = useRef("");
  const currentCampaign = useRef("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string>("");
  const [entitlement, setEntitlement] = useState<boolean | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlug] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [acceptingInvite, setAcceptingInvite] = useState(false);
  const [groqStatus, setGroqStatus] = useState<GroqStatus>();
  const [draftTarget, setDraftTarget] = useState("");
  const [generatedDrafts, setGeneratedDrafts] = useState<GeneratedDraft[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [activeCampaign, setActiveCampaign] = useState("");
  const [campaignContacts, setCampaignContacts] = useState<Contact[]>([]);
  const [selectedContact, setSelectedContact] = useState("");
  const [contactName, setContactName] = useState("");
  const [contactEmail, setContactEmail] = useState("");
  const [contactCompany, setContactCompany] = useState("");
  const [campaignName, setCampaignName] = useState("");
  const [campaignGoal, setCampaignGoal] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    const onHashChange = () => setSection(readDeckSection());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const changeWorkspace = useCallback((workspaceId: string) => {
    if (currentWorkspace.current === workspaceId) return;
    currentWorkspace.current = workspaceId;
    currentCampaign.current = "";
    setEntitlement(null);
    setGroqStatus(undefined);
    setDraftTarget("");
    setGeneratedDrafts([]);
    setJobs([]);
    setContacts([]);
    setCampaigns([]);
    setCampaignContacts([]);
    setSelectedContact("");
    setActiveCampaign("");
    setContactName("");
    setContactEmail("");
    setContactCompany("");
    setCampaignName("");
    setCampaignGoal("");
    setError("");
    setMessage("");
    setActiveWorkspace(workspaceId);
  }, []);

  const changeCampaign = useCallback((campaignId: string) => {
    if (currentCampaign.current === campaignId) return;
    currentCampaign.current = campaignId;
    setCampaignContacts([]);
    setDraftTarget("");
    setActiveCampaign(campaignId);
  }, []);

  function navigate(next: DeckSection) {
    setSection(next);
    if (readDeckSection() !== next) window.location.hash = next;
  }

  const refreshWorkspaces = useCallback(async () => {
    const result = await requestApi<{ workspaces: Workspace[] }>("/api/v1/workspaces");
    let rows = result.workspaces;
    // Verified new accounts get one free private workspace automatically.
    // The POST is idempotent, including across reloads and parallel tabs.
    if (rows.length === 0 && import.meta.env.VITE_ZYVELLO_ENABLE_NEW_WORKSPACES === "true") {
      const created = await requestApi<{ workspace: Workspace; created: boolean }>("/api/v1/workspaces/bootstrap", {
        method: "POST", body: JSON.stringify({}),
      });
      rows = [created.workspace];
    }
    setWorkspaces(rows);
    if (!rows.some(workspace => workspace.id === currentWorkspace.current)) {
      changeWorkspace(rows.find(workspace => workspace.id === gmailReturn?.workspaceId)?.id || rows[0]?.id || "");
    }
  }, [changeWorkspace]);

  useEffect(() => {
    if (!activeWorkspace) { setEntitlement(null); return; }
    let cancelled = false;
    setEntitlement(null);
    void requestApi<{ entitled: boolean }>(`/api/v1/workspaces/${activeWorkspace}/entitlement`)
      .then(result => { if (!cancelled) setEntitlement(result.entitled); })
      .catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : "Subscription status unavailable"); });
    return () => { cancelled = true; };
  }, [activeWorkspace]);

  const refreshJobs = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ jobs: Job[] }>(`/api/v1/workspaces/${workspaceId}/jobs`);
    if (currentWorkspace.current === workspaceId) setJobs(result.jobs);
  }, []);

  const refreshContacts = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ contacts: Contact[] }>(`/api/v1/workspaces/${workspaceId}/contacts`);
    if (currentWorkspace.current !== workspaceId) return;
    setContacts(result.contacts);
    setSelectedContact(current => result.contacts.some(contact => contact.id === current)
      ? current : result.contacts[0]?.id || "");
  }, []);

  const refreshCampaigns = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ campaigns: Campaign[] }>(`/api/v1/workspaces/${workspaceId}/campaigns`);
    if (currentWorkspace.current !== workspaceId) return;
    setCampaigns(result.campaigns);
    if (!result.campaigns.some(campaign => campaign.id === currentCampaign.current)) {
      changeCampaign(result.campaigns[0]?.id || "");
    }
  }, [changeCampaign]);

  const refreshCampaignContacts = useCallback(async (workspaceId: string, campaignId: string) => {
    const result = await requestApi<{ contacts: Contact[] }>(
      `/api/v1/workspaces/${workspaceId}/campaigns/${campaignId}/contacts`,
    );
    if (currentWorkspace.current === workspaceId && currentCampaign.current === campaignId) {
      setCampaignContacts(result.contacts);
    }
  }, []);

  useEffect(() => {
    let active = true;
    refreshWorkspaces().catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "Could not load workspaces");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshWorkspaces]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true) { setJobs([]); setContacts([]); setCampaigns([]); return; }
    let cancelled = false;
    void refreshContacts(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load contacts");
    });
    void refreshCampaigns(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load campaigns");
    });
    return () => { cancelled = true; };
  }, [activeWorkspace, entitlement, refreshContacts, refreshCampaigns]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true || section !== "activity") return;
    let cancelled = false;
    void refreshJobs(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load jobs");
    });
    const interval = window.setInterval(() => {
      void refreshJobs(activeWorkspace).catch(() => { /* Keep the last visible result during transient errors. */ });
    }, 8000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [activeWorkspace, entitlement, section, refreshJobs]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true || !activeCampaign) { setCampaignContacts([]); return; }
    let cancelled = false;
    void refreshCampaignContacts(activeWorkspace, activeCampaign).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load campaign contacts");
    });
    return () => { cancelled = true; };
  }, [activeWorkspace, entitlement, activeCampaign, refreshCampaignContacts]);

  const manager = workspaces.some(workspace =>
    workspace.id === activeWorkspace && (workspace.role === "owner" || workspace.role === "admin"));
  const gmailPath = `/api/v1/workspaces/${activeWorkspace}/gmail`;
  const [gmailCallback, setGmailCallback] = useState(gmailReturn);
  const gmailCompletion = useRef<Promise<GmailStatus> | undefined>(undefined);
  useEffect(() => {
    if (!gmailCallback || loading) return;
    if (gmailCallback.workspaceId !== activeWorkspace || !gmailCallback.code) {
      setError("Gmail was not connected. Restart the connection in Settings.");
      setGmailCallback(undefined);
      return;
    }
    let active = true;
    const { code, state } = gmailCallback;
    gmailCompletion.current ??= requestApi<GmailStatus>(gmailPath + "/complete", {
      method: "POST", body: JSON.stringify({ code, state }),
    });
    void gmailCompletion.current.then(() => {
      if (active) setMessage("Gmail connected to this workspace.");
    }).catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "Gmail could not be connected.");
    }).finally(() => {
      if (active) setGmailCallback(undefined);
    });
    return () => { active = false; };
  }, [gmailCallback, activeWorkspace, loading, gmailPath]);

  const loadGmail = useCallback(() => requestApi<GmailStatus>(gmailPath), [gmailPath]);
  const beginGmail = useCallback(() => requestApi<{ authorization_url: string; state: string }>(gmailPath + "/begin", {
    method: "POST",
  }), [gmailPath]);
  const disconnectGmail = useCallback(async () => { await requestApi(gmailPath, { method: "DELETE" }); }, [gmailPath]);
  const sendGmailTest = useCallback(async () => { await requestApi(gmailPath + "/test", { method: "POST" }); }, [gmailPath]);
  const workspacePath = `/api/v1/workspaces/${activeWorkspace}`;
  const loadDeliveries = useCallback(() => requestApi<CampaignDeliveryData>(workspacePath + "/campaign-deliveries"), [workspacePath]);
  const recordPilotConsent = useCallback((contactId: string, evidence: string) =>
    requestApi(workspacePath + `/contacts/${contactId}/permission`, {
      method: "POST", body: JSON.stringify({ action: "claimed_consent", source: "campaign_pilot", evidence }),
    }), [workspacePath]);
  const sendPilotDraft = useCallback((draftId: string, subject: string, body: string) =>
    requestApi<{ status: string }>(workspacePath + `/drafts/${draftId}/send`, {
      method: "POST", body: JSON.stringify({ subject, body }),
    }), [workspacePath]);
  const syncPilotReply = useCallback((deliveryId: string) =>
    requestApi<{ reply_found: boolean }>(workspacePath + `/campaign-deliveries/${deliveryId}/sync`, { method: "POST" }),
    [workspacePath]);
  const groqPath = `/api/v1/workspaces/${activeWorkspace}/credentials/groq`;
  const loadGroq = useCallback(() => requestApi<GroqStatus>(groqPath), [groqPath]);
  const saveGroq = useCallback((key: string) => requestApi<GroqStatus>(groqPath, {
    method: "PUT", body: JSON.stringify({ api_key: key }),
  }), [groqPath]);
  const removeGroq = useCallback(async () => { await requestApi(groqPath, { method: "DELETE" }); }, [groqPath]);

  const refreshDrafts = useCallback(async () => {
    const result = await requestApi<{ drafts: GeneratedDraft[] }>(`/api/v1/workspaces/${activeWorkspace}/drafts`);
    if (currentWorkspace.current === activeWorkspace) setGeneratedDrafts(result.drafts);
  }, [activeWorkspace]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true || section !== "campaigns") return;
    let active = true;
    setGroqStatus(undefined);
    if (manager) {
      void loadGroq().then(status => { if (active) setGroqStatus(status); }).catch(cause => {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load the AI connection");
      });
    }
    void refreshDrafts().catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "Could not load generated drafts");
    });
    return () => { active = false; };
  }, [activeWorkspace, entitlement, section, manager, loadGroq, refreshDrafts]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true || section !== "controls" || !manager) return;
    void refreshDrafts().catch(() => setError("Could not load campaign drafts."));
  }, [activeWorkspace, entitlement, section, manager, refreshDrafts]);

  async function generateDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || entitlement !== true || !manager || !groqStatus?.configured ||
      !groqStatus.ai_execution_enabled || !activeCampaign || !draftTarget) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await requestApi<{ dispatch_requested: boolean }>(`/api/v1/workspaces/${activeWorkspace}/drafts`, {
        method: "POST", body: JSON.stringify({
          campaign_id: activeCampaign, contact_id: draftTarget, idempotency_key: crypto.randomUUID(),
        }),
      });
      setMessage(result.dispatch_requested ? "AI draft queued. Follow its progress in Activity." : "AI draft queued. Worker dispatch is pending.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not queue the draft");
    } finally {
      setBusy(false);
    }
  }

  function changeName(value: string) {
    setWorkspaceName(value);
    setWorkspaceSlug(value.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63));
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await requestApi<{ workspace: Workspace }>("/api/v1/workspaces", {
        method: "POST", body: JSON.stringify({ name: workspaceName.trim(), slug: workspaceSlug.trim() }),
      });
      setWorkspaces(current => [result.workspace, ...current]);
      changeWorkspace(result.workspace.id);
      setWorkspaceName("");
      setWorkspaceSlug("");
      setMessage("Your private workspace was created successfully.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create the workspace");
    } finally {
      setBusy(false);
    }
  }

  async function runHealthCheck() {
    if (!activeWorkspace || busy) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const result = await requestApi<{ job: { id: string; created: boolean }; dispatch_requested: boolean }>(
        `/api/v1/workspaces/${activeWorkspace}/jobs`, {
          method: "POST", body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
        },
      );
      setMessage(result.dispatch_requested
        ? "A durable diagnostic job was queued for the background worker."
        : "The job was saved, but dispatch is pending. It can be retried safely.");
      await refreshJobs(activeWorkspace);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not enqueue the job");
    } finally {
      setBusy(false);
    }
  }

  async function acceptInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (acceptingInvite) return;
    setAcceptingInvite(true);
    setError("");
    setMessage("");
    try {
      const { workspace } = await requestApi<{ workspace: Workspace }>("/api/v1/workspace-invitations/accept", {
        method: "POST", body: JSON.stringify({ token: inviteCode.trim() }),
      });
      setWorkspaces(current => [workspace, ...current.filter(item => item.id !== workspace.id)]);
      changeWorkspace(workspace.id);
      setInviteCode("");
      setMessage("You joined the workspace.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not accept the invitation.");
    } finally {
      setAcceptingInvite(false);
    }
  }

  async function createContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeWorkspace || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await requestApi(`/api/v1/workspaces/${activeWorkspace}/contacts`, {
        method: "POST", body: JSON.stringify({
          full_name: contactName.trim(), email: contactEmail.trim(), company: contactCompany.trim(),
        }),
      });
      setContactName(""); setContactEmail(""); setContactCompany("");
      setMessage("Contact saved privately in this workspace.");
      await refreshContacts(activeWorkspace);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save contact");
    } finally { setBusy(false); }
  }

  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeWorkspace || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await requestApi(`/api/v1/workspaces/${activeWorkspace}/campaigns`, {
        method: "POST", body: JSON.stringify({ name: campaignName.trim(), goal: campaignGoal.trim() }),
      });
      setCampaignName(""); setCampaignGoal("");
      setMessage("Campaign draft saved.");
      await refreshCampaigns(activeWorkspace);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not create draft");
    } finally { setBusy(false); }
  }

  async function assignContact() {
    if (!activeWorkspace || !activeCampaign || !selectedContact || busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await requestApi(`/api/v1/workspaces/${activeWorkspace}/campaigns/${activeCampaign}/contacts`, {
        method: "POST", body: JSON.stringify({ contact_id: selectedContact }),
      });
      setMessage("Contact attached to the draft.");
      await refreshCampaignContacts(activeWorkspace, activeCampaign);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not attach contact");
    } finally { setBusy(false); }
  }

  const updateBillingEntitlement = useCallback((paid: boolean) => {
    if (activeWorkspace === currentWorkspace.current) setEntitlement(paid);
  }, [activeWorkspace]);
  const selected = workspaces.find(workspace => workspace.id === activeWorkspace);
  const workspaceHome = section === "today" || !selected;
  const pilotView = section === "controls" || section === "outbox" || section === "conversations" ? section : null;
  const requiresSubscription = section === "contacts" || section === "campaigns" || section === "activity" || !!pilotView;
  return (
    <HostedCommandDeck email={email} section={section} onNavigate={navigate} onSignOut={onSignOut} signingOut={signingOut}>
      <div className="space-y-6">
      {sessionError && <p role="alert" className="banner bad">{sessionError}</p>}
      {error && <p role="alert" className="rounded-xl border border-rose-400/30 bg-rose-950/30 p-4 text-sm text-[var(--bad)]">{error}</p>}
      {message && <p role="status" className="rounded-xl border border-emerald-400/30 bg-emerald-900/20 p-4 text-sm text-[var(--good)]">{message}</p>}

      {loading ? <p role="status" className="text-[var(--text-2)]">Loading your workspaces…</p> : <>
        {selected && <div className="flex flex-wrap items-center gap-3">
          <label className="text-sm font-medium" htmlFor="workspace-select">Workspace</label>
          <select id="workspace-select" className="form-input max-w-sm" value={activeWorkspace} disabled={busy || acceptingInvite || Boolean(gmailCallback)}
            onChange={event => changeWorkspace(event.target.value)}>
            {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} ({workspace.role})</option>)}
          </select>
        </div>}

        {workspaces.length === 0 && import.meta.env.VITE_ZYVELLO_ENABLE_NEW_WORKSPACES !== "true" && <section className="card">
          <h2 className="text-xl font-semibold">Workspace access pending</h2>
          <p className="mt-2 text-sm text-[var(--text-2)]">Workspace creation is not enabled in this preview yet. Your account is verified, and no customer data is accessible without a workspace. Public onboarding will open with the subscription-gated release.</p>
        </section>}
        {workspaces.length === 0 && import.meta.env.VITE_ZYVELLO_ENABLE_NEW_WORKSPACES === "true" && <section className="card">
          <h2 className="text-xl font-semibold">Create your first agency workspace</h2>
          <p className="mt-2 text-sm text-[var(--text-2)]">Your customers and future campaign records will belong to this workspace.</p>
          <form onSubmit={event => void createWorkspace(event)} className="mt-6 space-y-4">
            <div><label htmlFor="workspace-name" className="block text-sm text-[var(--text-2)]">Agency name</label><input id="workspace-name" className="form-input mt-2 w-full" value={workspaceName} onChange={event => changeName(event.target.value)} required maxLength={200} placeholder="e.g. Faik Digital" /></div>
            <div><label htmlFor="workspace-slug" className="block text-sm text-[var(--text-2)]">Workspace URL name</label><input id="workspace-slug" className="form-input mt-2 w-full" value={workspaceSlug} onChange={event => setWorkspaceSlug(event.target.value)} required minLength={3} maxLength={63} pattern="[a-z0-9][a-z0-9-]{2,62}" /></div>
            <button className="btn btn-primary flex w-full items-center justify-center gap-2" type="submit" disabled={busy}>{busy ? "Creating…" : "Create workspace"}<ArrowRight className="h-4 w-4" /></button>
          </form>
        </section>}

        {section === "settings" && selected && (selected.role === "owner" || selected.role === "admin") && (
          <WorkspaceSettingsPanel key={`settings-${selected.id}`} workspace={selected} onSaved={updated =>
            setWorkspaces(current => current.map(workspace => workspace.id === updated.id ? updated : workspace))
          } />
        )}
        {section === "settings" && selected && manager && entitlement === true &&
          <HostedGroqConnection key={selected.id} load={loadGroq} save={saveGroq} remove={removeGroq} />}
        {section === "settings" && selected && manager && (gmailCallback
          ? <p role="status" className="card">Connecting Gmail…</p>
          : <HostedGmailConnection key={selected.id} workspaceId={selected.id} entitled={entitlement === true}
              load={loadGmail} begin={beginGmail} disconnect={disconnectGmail} sendTest={sendGmailTest} />)}
        {section === "settings" && selected && <section className="card" aria-label="LinkedIn">
          <h3 className="text-xl font-semibold">LinkedIn</h3>
          <p className="muted mt-2">Messaging API access pending.</p>
        </section>}
        {section === "settings" && selected && <WorkspaceTeamPanel key={`team-${selected.id}`} workspace={selected} entitled={entitlement === true} />}
        {section === "settings" && <details className="card">
          <summary className="cursor-pointer text-sm font-medium">Join a workspace with an invitation</summary>
          <form className="mt-4 space-y-3" onSubmit={event => void acceptInvitation(event)}>
            <label className="block text-sm text-[var(--text-2)]">Invitation code
              <input className="form-input mt-2 w-full" value={inviteCode} onChange={event => setInviteCode(event.target.value)} autoComplete="off" minLength={32} maxLength={128} required />
            </label>
            <button type="submit" className="btn btn-primary" disabled={acceptingInvite}>{acceptingInvite ? "Joining…" : "Join workspace"}</button>
          </form>
        </details>}
        {selected && (workspaceHome || requiresSubscription) && entitlement === null &&
          <p role="status" className="muted">Checking subscription access…</p>}
        {selected && (workspaceHome || requiresSubscription) && entitlement === false && <section className="card">
          <h3 className="text-xl font-semibold">Explore Zyvello</h3>
          <p className="mt-2 text-sm text-[var(--text-2)]">An active subscription unlocks contacts, campaign drafts, and workspace activity.</p>
          <button type="button" className="btn btn-primary mt-4" onClick={() => navigate("account")}>View subscription options</button>
        </section>}
        {selected && workspaceHome && entitlement === true && <section className="card">
          <h3 className="text-xl font-semibold">{selected.name}</h3>
          <div className="btn-group">
            <button type="button" className="btn btn-secondary" onClick={() => navigate("contacts")}>Open contacts</button>
            <button type="button" className="btn btn-secondary" onClick={() => navigate("campaigns")}>Open campaigns</button>
          </div>
        </section>}
        {selected && entitlement === true && section === "contacts" &&
          <section className="card" aria-label="Workspace contacts">
            <h2 className="text-xl font-semibold">Contacts</h2>
            <p className="mt-1 text-sm text-[var(--text-3)]">Contacts saved to {selected.name}.</p>
            <form className="mt-5 space-y-3" onSubmit={event => void createContact(event)}>
              <label className="block text-sm text-[var(--text-2)]" htmlFor="contact-name">Contact name</label>
              <input id="contact-name" className="form-input w-full" required maxLength={160}
                value={contactName} onChange={event => setContactName(event.target.value)} placeholder="e.g. Example Contact" />
              <label className="block text-sm text-[var(--text-2)]" htmlFor="contact-email">Email address</label>
              <input id="contact-email" type="email" className="form-input w-full" required maxLength={254}
                value={contactEmail} onChange={event => setContactEmail(event.target.value)} placeholder="person@example.com" />
              <label className="block text-sm text-[var(--text-2)]" htmlFor="contact-company">Company (optional)</label>
              <input id="contact-company" className="form-input w-full" maxLength={160}
                value={contactCompany} onChange={event => setContactCompany(event.target.value)} placeholder="Example Agency" />
              <button className="btn btn-primary w-full" type="submit" disabled={busy}>{busy ? "Saving…" : "Save contact"}</button>
            </form>
            <div className="mt-6 space-y-2" aria-label="Saved contacts">
              {contacts.length === 0 ? <p className="text-sm text-[var(--text-3)]">No contacts saved yet.</p> : contacts.map(contact =>
                <div key={contact.id} className="inset rounded-xl p-3">
                  <p className="text-sm font-medium text-[var(--text)]">{contact.full_name}</p>
                  <p className="mt-1 break-all text-xs text-[var(--text-2)]">{contact.email}</p>
                  {contact.company && <p className="mt-1 text-xs text-[var(--text-3)]">{contact.company}</p>}
                </div>,
              )}
            </div>
          </section>}
        {selected && entitlement === true && section === "campaigns" &&
          <section className="card" aria-label="Campaign drafts">
            <h2 className="text-xl font-semibold">Campaign drafts</h2>
            <p className="mt-1 text-sm text-[var(--text-3)]">Prepare a contact list for your campaign.</p>
            <form className="mt-5 space-y-3" onSubmit={event => void createCampaign(event)}>
              <label className="block text-sm text-[var(--text-2)]" htmlFor="campaign-name">Campaign name</label>
              <input id="campaign-name" className="form-input w-full" required maxLength={160}
                value={campaignName} onChange={event => setCampaignName(event.target.value)} placeholder="e.g. November agency outreach" />
              <label className="block text-sm text-[var(--text-2)]" htmlFor="campaign-goal">Goal (optional)</label>
              <textarea id="campaign-goal" className="form-input w-full" rows={3} maxLength={1000}
                value={campaignGoal} onChange={event => setCampaignGoal(event.target.value)} placeholder="Describe the intended audience and offer" />
              <button className="btn btn-primary w-full" type="submit" disabled={busy}>{busy ? "Saving…" : "Create draft"}</button>
            </form>
            {campaigns.length === 0 ? <p className="mt-6 text-sm text-[var(--text-3)]">No campaign drafts yet.</p> : <div className="mt-6 space-y-3">
              <label className="block text-sm text-[var(--text-2)]" htmlFor="campaign-select">Draft to prepare</label>
              <select id="campaign-select" className="w-full form-input"
                value={activeCampaign} onChange={event => changeCampaign(event.target.value)}>
                {campaigns.map(campaign => <option key={campaign.id} value={campaign.id}>{campaign.name} (draft)</option>)}
              </select>
              {contacts.length > 0 && <div className="space-y-3">
                <label className="block text-sm text-[var(--text-2)]" htmlFor="draft-contact-select">Attach an existing contact</label>
                <select id="draft-contact-select" className="w-full form-input"
                  value={selectedContact} onChange={event => setSelectedContact(event.target.value)}>
                  {contacts.map(contact => <option key={contact.id} value={contact.id}>{contact.full_name}</option>)}
                </select>
                <button type="button" className="rounded-full border border-white/25 px-5 py-2 text-sm text-[var(--text)] hover:bg-white/10"
                  disabled={busy || !selectedContact} onClick={() => void assignContact()}>Attach to draft</button>
              </div>}
              <p className="text-xs text-[var(--text-3)]">Attached contacts: {campaignContacts.length}</p>
              {manager && groqStatus?.ai_execution_enabled && !groqStatus.configured &&
                <button className="btn btn-secondary" type="button" onClick={() => navigate("settings")}>Set up Groq</button>}
              {manager && groqStatus?.ai_execution_enabled && groqStatus.configured && campaignContacts.length > 0 &&
                <form className="space-y-3" onSubmit={event => void generateDraft(event)}>
                  <label className="block text-sm" htmlFor="draft-target">Generate a draft for</label>
                  <select className="form-input w-full" id="draft-target" required value={draftTarget}
                    onChange={event => setDraftTarget(event.target.value)}>
                    <option value="">Select an attached contact</option>
                    {campaignContacts.map(contact => <option key={contact.id} value={contact.id}>{contact.full_name}</option>)}
                  </select>
                  <button className="btn btn-primary" type="submit" disabled={busy || !draftTarget}>Generate draft</button>
                </form>}
              <div className="space-y-3">
                <h3 className="text-sm font-medium">AI drafts</h3>
                <button className="btn btn-secondary btn-sm" type="button"
                  onClick={() => void refreshDrafts().catch(() => setError("Could not refresh generated drafts"))}>Refresh AI drafts</button>
                {generatedDrafts.filter(draft => draft.campaign_id === activeCampaign).map(draft =>
                  <p key={draft.id} className="inset whitespace-pre-wrap break-words rounded-xl p-4 text-sm">{draft.draft_text}</p>
                )}
              </div>
            </div>}
          </section>}

        {selected && entitlement === true && section === "activity" && <section className="card">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-xl font-semibold">Background jobs</h2><p className="mt-1 text-sm text-[var(--text-3)]">Database-backed, retryable, isolated to {selected.name}.</p></div>
            <button type="button" onClick={() => void refreshJobs(activeWorkspace).catch(() => setError("Refresh failed"))} className="flex items-center gap-2 rounded-full border border-white/20 px-4 py-2 text-sm" aria-label="Refresh jobs"><RefreshCw className="h-4 w-4" /> Refresh</button>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button className="btn btn-primary" type="button" disabled={busy} onClick={() => void runHealthCheck()}>{busy ? "Processing…" : "Run safe workspace check"}</button>

          </div>
          <div className="mt-6 space-y-3">
            {jobs.length === 0 ? <p className="text-sm text-[var(--text-3)]">No jobs yet. Run the safe check to test the queue.</p> : jobs.map(job => <div key={job.id} className="inset rounded-xl p-4">
              <div className="flex items-center justify-between gap-4"><span className="text-sm font-medium">{job.kind === "groq_draft" ? "Groq draft" : "Workspace health check"}</span><span className={`flex items-center gap-1 text-xs ${job.status === "succeeded" ? "text-[var(--good)]" : job.status === "dead" ? "text-[var(--bad)]" : "text-[var(--wait)]"}`}>{job.status === "succeeded" ? <CheckCircle2 className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}{job.status}</span></div>
              <p className="mt-2 text-xs text-[var(--text-3)]">Attempt {job.attempts}/{job.max_attempts} · {new Date(job.created_at).toLocaleString()}</p>
              {job.result?.note && <p className="mt-2 text-sm text-[var(--text-2)]">{job.result.note}</p>}
              {job.last_error && <p className="mt-2 text-xs text-[var(--bad)]">{job.last_error}</p>}
            </div>)}
          </div>
        </section>}
        {selected && pilotView && entitlement === true && (manager
          ? <HostedCampaignPilot key={selected.id + pilotView} view={pilotView} drafts={generatedDrafts} contacts={contacts}
              load={loadDeliveries} recordConsent={recordPilotConsent} send={sendPilotDraft} sync={syncPilotReply} />
          : <p className="card muted">Campaign sending and reply checks are available to workspace managers.</p>)}
        {selected && pendingSections[section] && <section className="card"><p>{pendingSections[section]}</p></section>}
        {selected && section === "usage" && <section className="card">
          <p>View contact, campaign draft, and team limits under My account.</p>
          <button type="button" className="btn btn-secondary mt-4" onClick={() => navigate("account")}>View plan limits</button>
        </section>}
        {selected && section === "account" && <section className="card" aria-label="Account identity">
          <p className="font-medium break-all">{email}</p>
          <p className="muted mt-1">Verified email</p>
        </section>}
        {selected && <div hidden={section !== "account"}>
          <BillingPanel key={selected.id} workspaceId={selected.id} owner={selected.role === "owner"}
            onEntitlementChange={updateBillingEntitlement} />
        </div>}
      </>}
      </div>
    </HostedCommandDeck>
  );
}
