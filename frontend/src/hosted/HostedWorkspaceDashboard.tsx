import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowRight, BriefcaseBusiness, CheckCircle2, CircleAlert, RefreshCw, ShieldCheck } from "lucide-react";
import { authClient } from "./auth-client";

type Workspace = { id: string; slug: string; name: string; role: string; created_at: string };
type Contact = { id: string; full_name: string; email: string; company: string; created_at: string };
type Campaign = { id: string; name: string; goal: string; status: "draft"; created_at: string };
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
type BillingPlan = { code: string; name: string; amount_paise: number; currency: "INR"; period: string; interval: number };
type BillingSubscription = {
  id: string; plan_code: string; status: string; checkout_verified: boolean;
  paid_through: string | null; paid_count: number; entitled: boolean;
};
type RazorpayCheckoutResult = { razorpay_payment_id: string; razorpay_signature: string };
type RazorpayOptions = {
  key: string; subscription_id: string; name: string; description: string;
  handler: (result: RazorpayCheckoutResult) => void;
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
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(body?.detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

function BillingPanel({ workspaceId, owner, onEntitlementChange }: { workspaceId: string; owner: boolean; onEntitlementChange: (paid: boolean) => void }) {
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [subscriptions, setSubscriptions] = useState<BillingSubscription[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [busyPlan, setBusyPlan] = useState("");
  const [cancellingId, setCancellingId] = useState("");
  const [confirmCancelId, setConfirmCancelId] = useState("");
  const [feedback, setFeedback] = useState("");
  const [billingError, setBillingError] = useState("");
  const [checkingBilling, setCheckingBilling] = useState(true);

  const refresh = useCallback(async () => {
    const [configured, status] = await Promise.all([
      requestApi<{ mode: "test"; plans: BillingPlan[] }>(`/api/v1/workspaces/${workspaceId}/billing/plans`),
      requestApi<{ mode: "test"; subscriptions: BillingSubscription[] }>(`/api/v1/workspaces/${workspaceId}/billing`),
    ]);
    if (configured.mode !== "test" || status.mode !== "test") throw new Error("Checkout is not in test mode");
    return { plans: configured.plans, subscriptions: status.subscriptions };
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void refresh().then(result => {
      if (!cancelled) {
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
      setSubscriptions(result.subscriptions);
      onEntitlementChange(result.subscriptions.some(sub => sub.entitled));
      setBillingError("");
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : "Could not refresh billing");
    }
  }

  async function tryCheckout(plan: BillingPlan) {
    if (!owner || !enabled || busyPlan) return;
    setBusyPlan(plan.code);
    setFeedback("");
    setBillingError("");
    try {
      const created = await requestApi<
        { status: "pending_reconciliation" } |
        { mode: "test"; key_id: string; subscription_id: string; intent_id: string; status: "checkout_ready" }
      >(`/api/v1/workspaces/${workspaceId}/billing/subscriptions`, {
        method: "POST",
        body: JSON.stringify({ plan_code: plan.code, idempotency_key: crypto.randomUUID().replace(/-/g, "") }),
      });
      if (created.status === "pending_reconciliation") {
        setBillingError("This test checkout is awaiting provider reconciliation. Contact support before trying another payment.");
        return;
      }
      if (created.mode !== "test" || !created.key_id.startsWith("rzp_test_") || !created.subscription_id.startsWith("sub_")) {
        throw new Error("Checkout safety check failed");
      }
      await loadCheckoutScript();
      if (!window.Razorpay) throw new Error("Razorpay Checkout is unavailable");
      const checkout = new window.Razorpay({
        key: created.key_id,
        subscription_id: created.subscription_id,
        name: "Zyvello — TEST MODE",
        description: `${plan.name} (test payment; no live charges)`,
        handler: result => {
          void requestApi(`/api/v1/workspaces/${workspaceId}/billing/subscriptions/${created.intent_id}/verify`, {
            method: "POST",
            body: JSON.stringify({ razorpay_payment_id: result.razorpay_payment_id,
              razorpay_signature: result.razorpay_signature }),
          }).then(async () => {
            setFeedback("Test checkout verified. Waiting for Razorpay's signed payment webhook…");
            for (let attempt = 0; attempt < 5; attempt += 1) {
              const refreshed = await refresh();
              setSubscriptions(refreshed.subscriptions);
              onEntitlementChange(refreshed.subscriptions.some(sub => sub.entitled));
              const latest = refreshed.subscriptions.find(sub => sub.id === created.intent_id);
              if (latest?.entitled) {
                setFeedback("Test payment confirmed. Test access is active.");
                return;
              }
              if (attempt < 4) await new Promise(resolve => window.setTimeout(resolve, 1500));
            }
            setFeedback("Checkout verified, but payment confirmation is still pending. Use Refresh billing to check again.");
          }).catch(cause => setBillingError(cause instanceof Error ? cause.message : "Checkout verification failed"));
        },
      });
      checkout.open();
      setFeedback("Razorpay test Checkout opened. No real money should be collected.");
    } catch (cause) {
      setBillingError(cause instanceof Error ? cause.message : "Test checkout could not start");
    } finally {
      setBusyPlan("");
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
        setFeedback("This test subscription has already ended.");
      } else if (response.status === "cancellation_requested" && response.at_cycle_end === true) {
        setFeedback("Cancellation at the billing-cycle end was requested in Razorpay TEST mode. Refresh to check its signed webhook status.");
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
    <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7" aria-label="Workspace billing">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-semibold">Billing</h2>
          <p className="mt-1 text-sm text-gray-400">Separate per workspace. Live payments are not enabled.</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="rounded-full border border-white/20 px-3 py-1 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40"
            disabled={!enabled} onClick={() => void refreshBilling()}>Refresh billing</button>
          <span className="rounded-full border border-amber-400/30 px-3 py-1 text-xs text-amber-200">TEST MODE</span>
        </div>
      </div>
      {!enabled ? <p className="mt-4 text-sm text-gray-300">{checkingBilling ? "Checking billing configuration…" : "Payment testing is not configured yet. No payment will be collected."}</p> : <>
        <div className="mt-5 space-y-2">
          {subscriptions.length === 0 ? <p className="text-sm text-gray-400">No subscription for this workspace yet.</p> : subscriptions.map(sub => <div key={sub.id} className="rounded-xl border border-white/10 bg-black/20 p-3 text-sm">
            <span className="font-medium">{sub.plan_code}</span> · {sub.status} · {sub.entitled ? "Test access active" : "No paid access"}
            {sub.paid_through && <p className="mt-1 text-xs text-gray-400">Recorded period end: {new Date(sub.paid_through).toLocaleDateString()}</p>}
            {sub.status === "creating" && <p className="mt-2 text-xs text-amber-200">The provider response is not confirmed. Contact support before starting another checkout.</p>}
            {["pending", "halted"].includes(sub.status) && <p className="mt-2 text-xs text-amber-200">Payment recovery is needed. Paid tools remain locked, and a second checkout is blocked to prevent duplicate recurring subscriptions.</p>}
            {sub.status === "active" && !sub.entitled && <p className="mt-2 text-xs text-amber-200">Payment confirmation or the paid period is missing. No access has been granted.</p>}
            {["cancelled", "completed", "expired"].includes(sub.status) && <p className="mt-2 text-xs text-gray-400">This subscription is no longer active; it does not unlock premium tools.</p>}
            {owner && !["creating", "cancelled", "completed", "expired"].includes(sub.status) && <div className="mt-3">
              {confirmCancelId === sub.id ? <div className="rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
                <p className="text-xs text-amber-100">Request cancellation at the billing-cycle end? This stops future renewals. Razorpay's signed webhook determines the final status.</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="rounded-full border border-rose-300/45 px-3 py-2 text-xs text-rose-100 disabled:opacity-40" disabled={Boolean(cancellingId)} onClick={() => void cancelRenewal(sub)}>
                    {cancellingId === sub.id ? "Requesting…" : "Confirm TEST cancellation"}
                  </button>
                  <button type="button" className="rounded-full border border-white/25 px-3 py-2 text-xs text-gray-200" disabled={Boolean(cancellingId)} onClick={() => setConfirmCancelId("")}>Keep subscription</button>
                </div>
              </div> : <button type="button" className="rounded-full border border-white/25 px-3 py-2 text-xs text-gray-200 hover:bg-white/10 disabled:opacity-40" disabled={Boolean(cancellingId) || Boolean(busyPlan)} onClick={() => setConfirmCancelId(sub.id)}>Cancel test renewal</button>}
            </div>}
          </div>)}
        </div>
        {plans.length > 0 && <div className="mt-5 grid gap-3 sm:grid-cols-2">
          {plans.map(plan => <div className="rounded-2xl border border-white/15 bg-black/20 p-4" key={plan.code}>
            <p className="font-medium">{plan.name}</p>
            <p className="mt-1 text-lg font-semibold">₹{(plan.amount_paise / 100).toLocaleString("en-IN")} <span className="text-xs font-normal text-gray-400">/ {plan.interval} {plan.period}</span></p>
            <button className="mt-4 rounded-full border border-violet-300/40 px-4 py-2 text-xs text-white hover:bg-white/10 disabled:opacity-40" type="button"
              disabled={!owner || Boolean(busyPlan) || subscriptions.some(sub => !["cancelled", "completed", "expired"].includes(sub.status))}
              onClick={() => void tryCheckout(plan)}>{busyPlan === plan.code ? "Opening…" : "Try test checkout"}</button>
          </div>)}
        </div>}
        {!owner && <p className="mt-3 text-xs text-gray-400">Only the workspace owner can manage billing.</p>}
      </>}
      {feedback && <p role="status" className="mt-4 text-xs text-emerald-200">{feedback}</p>}
      {billingError && <p role="alert" className="mt-4 text-xs text-rose-200">{billingError}</p>}
    </section>
  );
}

export function HostedWorkspaceDashboard({ onSignOut, signingOut }: { onSignOut: () => void; signingOut: boolean }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string>("");
  const [entitlement, setEntitlement] = useState<boolean | null>(null);
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlug] = useState("");
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
    setActiveWorkspace(current => current && rows.some(workspace => workspace.id === current)
      ? current : rows[0]?.id || "");
  }, []);

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
    setJobs(result.jobs);
  }, []);

  const refreshContacts = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ contacts: Contact[] }>(`/api/v1/workspaces/${workspaceId}/contacts`);
    setContacts(result.contacts);
    setSelectedContact(current => result.contacts.some(contact => contact.id === current)
      ? current : result.contacts[0]?.id || "");
  }, []);

  const refreshCampaigns = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ campaigns: Campaign[] }>(`/api/v1/workspaces/${workspaceId}/campaigns`);
    setCampaigns(result.campaigns);
    setActiveCampaign(current => result.campaigns.some(campaign => campaign.id === current)
      ? current : result.campaigns[0]?.id || "");
  }, []);

  const refreshCampaignContacts = useCallback(async (workspaceId: string, campaignId: string) => {
    const result = await requestApi<{ contacts: Contact[] }>(
      `/api/v1/workspaces/${workspaceId}/campaigns/${campaignId}/contacts`,
    );
    setCampaignContacts(result.contacts);
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
    setContacts([]);
    setCampaigns([]);
    setActiveCampaign("");
    void refreshJobs(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load jobs");
    });
    void refreshContacts(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load contacts");
    });
    void refreshCampaigns(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load campaigns");
    });
    const interval = window.setInterval(() => {
      void refreshJobs(activeWorkspace).catch(() => { /* Keep existing state during transient errors. */ });
    }, 8000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [activeWorkspace, entitlement, refreshJobs, refreshContacts, refreshCampaigns]);

  useEffect(() => {
    if (!activeWorkspace || entitlement !== true || !activeCampaign) { setCampaignContacts([]); return; }
    let cancelled = false;
    void refreshCampaignContacts(activeWorkspace, activeCampaign).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load campaign contacts");
    });
    return () => { cancelled = true; };
  }, [activeWorkspace, entitlement, activeCampaign, refreshCampaignContacts]);

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
      setEntitlement(false);
      setActiveWorkspace(result.workspace.id);
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
      setMessage("Draft campaign saved. Nothing will be sent.");
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
      setMessage("Contact attached to the draft. No outreach was triggered.");
      await refreshCampaignContacts(activeWorkspace, activeCampaign);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not attach contact");
    } finally { setBusy(false); }
  }

  const selected = workspaces.find(workspace => workspace.id === activeWorkspace);
  return (
    <div className="w-full max-w-4xl space-y-6" aria-label="Your Zyvello workspaces">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 px-3 py-1 text-xs text-emerald-200"><ShieldCheck className="h-4 w-4" /> Verified Zyvello account</span>
          <h1 className="mt-4 text-3xl font-semibold">Your workspace</h1>
          <p className="mt-2 text-sm text-gray-400">Each agency has its own data. Only its members can access it.</p>
        </div>
        <button className="rounded-full border border-white/20 px-5 py-2 text-sm text-white hover:bg-white/10" type="button" disabled={signingOut} onClick={onSignOut}>{signingOut ? "Signing out…" : "Sign out"}</button>
      </div>

      {error && <p role="alert" className="rounded-xl border border-rose-400/30 bg-rose-950/30 p-4 text-sm text-rose-200">{error}</p>}
      {message && <p role="status" className="rounded-xl border border-emerald-400/30 bg-emerald-900/20 p-4 text-sm text-emerald-200">{message}</p>}

      {loading ? <p role="status" className="text-gray-300">Loading your workspaces…</p> : <>
        {workspaces.length > 0 && <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7">
          <div className="flex items-center gap-3"><BriefcaseBusiness className="h-5 w-5 text-violet-300" /><h2 className="text-xl font-semibold">Agency workspaces</h2></div>
          <label className="mt-5 block text-sm text-gray-300" htmlFor="workspace-select">Current workspace</label>
          <select id="workspace-select" className="mt-2 w-full rounded-xl border border-white/20 bg-zinc-950 p-3 text-white" value={activeWorkspace} onChange={event => { setEntitlement(null); setActiveWorkspace(event.target.value); }}>
            {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} ({workspace.role})</option>)}
          </select>
          <p className="mt-3 text-xs text-gray-400">Workspace slug: {selected?.slug}</p>
        </section>}

        {workspaces.length === 0 && import.meta.env.VITE_ZYVELLO_ENABLE_NEW_WORKSPACES !== "true" && <section className="rounded-3xl border border-violet-400/20 bg-violet-500/5 p-5 sm:p-7">
          <h2 className="text-xl font-semibold">Workspace access pending</h2>
          <p className="mt-2 text-sm text-gray-300">Workspace creation is not enabled in this preview yet. Your account is verified, and no customer data is accessible without a workspace. Public onboarding will open with the subscription-gated release.</p>
        </section>}
        {workspaces.length === 0 && import.meta.env.VITE_ZYVELLO_ENABLE_NEW_WORKSPACES === "true" && <section className="rounded-3xl border border-violet-400/20 bg-violet-500/5 p-5 sm:p-7">
          <h2 className="text-xl font-semibold">Create your first agency workspace</h2>
          <p className="mt-2 text-sm text-gray-300">Your customers and future campaign records will belong to this workspace.</p>
          <form onSubmit={event => void createWorkspace(event)} className="mt-6 space-y-4">
            <div><label htmlFor="workspace-name" className="block text-sm text-gray-300">Agency name</label><input id="workspace-name" className="glass-input mt-2 w-full bg-transparent" value={workspaceName} onChange={event => changeName(event.target.value)} required maxLength={200} placeholder="e.g. Faik Digital" /></div>
            <div><label htmlFor="workspace-slug" className="block text-sm text-gray-300">Workspace URL name</label><input id="workspace-slug" className="glass-input mt-2 w-full bg-transparent" value={workspaceSlug} onChange={event => setWorkspaceSlug(event.target.value)} required minLength={3} maxLength={63} pattern="[a-z0-9][a-z0-9-]{2,62}" /></div>
            <button className="button-light flex w-full items-center justify-center gap-2" type="submit" disabled={busy}>{busy ? "Creating…" : "Create workspace"}<ArrowRight className="h-4 w-4" /></button>
          </form>
        </section>}

        {selected && entitlement === null && <p role="status" className="text-sm text-gray-300">Checking subscription access…</p>}
        {selected && entitlement === false && <section className="rounded-3xl border border-violet-400/25 bg-violet-500/5 p-5 sm:p-7">
          <h2 className="text-xl font-semibold">Explore Zyvello — free account</h2>
          <p className="mt-2 text-sm text-gray-300">Your workspace is ready. Contacts, campaigns, AI tools and background jobs are premium features and require an active paid subscription.</p>
          <div className="mt-5 grid gap-3 sm:grid-cols-2">
            {["Business contacts", "Campaign management", "AI-assisted drafts", "Background automation"].map(feature =>
              <div key={feature} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <p className="font-medium text-white">{feature}</p>
                <p className="mt-1 text-xs text-gray-400">Subscription required</p>
              </div>
            )}
          </div>
          <p className="mt-4 text-sm text-violet-200">Review subscription options below to unlock paid tools when checkout is available.</p>
        </section>}
        {selected && entitlement === true && <div className="grid gap-6 lg:grid-cols-2">
          <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7" aria-label="Workspace contacts">
            <h2 className="text-xl font-semibold">Contacts</h2>
            <p className="mt-1 text-sm text-gray-400">Private to {selected.name}. No emails are sent.</p>
            <form className="mt-5 space-y-3" onSubmit={event => void createContact(event)}>
              <label className="block text-sm text-gray-300" htmlFor="contact-name">Contact name</label>
              <input id="contact-name" className="glass-input w-full bg-transparent" required maxLength={160}
                value={contactName} onChange={event => setContactName(event.target.value)} placeholder="e.g. Example Contact" />
              <label className="block text-sm text-gray-300" htmlFor="contact-email">Email address</label>
              <input id="contact-email" type="email" className="glass-input w-full bg-transparent" required maxLength={254}
                value={contactEmail} onChange={event => setContactEmail(event.target.value)} placeholder="person@example.com" />
              <label className="block text-sm text-gray-300" htmlFor="contact-company">Company (optional)</label>
              <input id="contact-company" className="glass-input w-full bg-transparent" maxLength={160}
                value={contactCompany} onChange={event => setContactCompany(event.target.value)} placeholder="Example Agency" />
              <button className="button-light w-full" type="submit" disabled={busy}>{busy ? "Saving…" : "Save contact"}</button>
            </form>
            <div className="mt-6 space-y-2" aria-label="Saved contacts">
              {contacts.length === 0 ? <p className="text-sm text-gray-400">No contacts saved yet.</p> : contacts.map(contact =>
                <div key={contact.id} className="rounded-xl border border-white/10 bg-black/20 p-3">
                  <p className="text-sm font-medium text-white">{contact.full_name}</p>
                  <p className="mt-1 break-all text-xs text-gray-300">{contact.email}</p>
                  {contact.company && <p className="mt-1 text-xs text-gray-400">{contact.company}</p>}
                </div>,
              )}
            </div>
          </section>

          <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7" aria-label="Campaign drafts">
            <h2 className="text-xl font-semibold">Campaign drafts</h2>
            <p className="mt-1 text-sm text-gray-400">Prepare an audience. Sending remains locked.</p>
            <form className="mt-5 space-y-3" onSubmit={event => void createCampaign(event)}>
              <label className="block text-sm text-gray-300" htmlFor="campaign-name">Campaign name</label>
              <input id="campaign-name" className="glass-input w-full bg-transparent" required maxLength={160}
                value={campaignName} onChange={event => setCampaignName(event.target.value)} placeholder="e.g. November agency outreach" />
              <label className="block text-sm text-gray-300" htmlFor="campaign-goal">Goal (optional)</label>
              <textarea id="campaign-goal" className="glass-input w-full bg-transparent" rows={3} maxLength={1000}
                value={campaignGoal} onChange={event => setCampaignGoal(event.target.value)} placeholder="Describe the intended audience and offer" />
              <button className="button-light w-full" type="submit" disabled={busy}>{busy ? "Saving…" : "Create draft"}</button>
            </form>
            {campaigns.length === 0 ? <p className="mt-6 text-sm text-gray-400">No campaign drafts yet.</p> : <div className="mt-6 space-y-3">
              <label className="block text-sm text-gray-300" htmlFor="campaign-select">Draft to prepare</label>
              <select id="campaign-select" className="w-full rounded-xl border border-white/20 bg-zinc-950 p-3 text-white"
                value={activeCampaign} onChange={event => setActiveCampaign(event.target.value)}>
                {campaigns.map(campaign => <option key={campaign.id} value={campaign.id}>{campaign.name} (draft)</option>)}
              </select>
              {contacts.length > 0 && <div className="space-y-3">
                <label className="block text-sm text-gray-300" htmlFor="draft-contact-select">Attach an existing contact</label>
                <select id="draft-contact-select" className="w-full rounded-xl border border-white/20 bg-zinc-950 p-3 text-white"
                  value={selectedContact} onChange={event => setSelectedContact(event.target.value)}>
                  {contacts.map(contact => <option key={contact.id} value={contact.id}>{contact.full_name}</option>)}
                </select>
                <button type="button" className="rounded-full border border-white/25 px-5 py-2 text-sm text-white hover:bg-white/10"
                  disabled={busy || !selectedContact} onClick={() => void assignContact()}>Attach to draft</button>
              </div>}
              <p className="text-xs text-gray-400">Attached contacts: {campaignContacts.length}. No messages can be sent from drafts.</p>
            </div>}
          </section>
        </div>}

        {selected && entitlement === true && <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-xl font-semibold">Background jobs</h2><p className="mt-1 text-sm text-gray-400">Database-backed, retryable, isolated to {selected.name}.</p></div>
            <button type="button" onClick={() => void refreshJobs(activeWorkspace).catch(() => setError("Refresh failed"))} className="flex items-center gap-2 rounded-full border border-white/20 px-4 py-2 text-sm" aria-label="Refresh jobs"><RefreshCw className="h-4 w-4" /> Refresh</button>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button className="button-light" type="button" disabled={busy} onClick={() => void runHealthCheck()}>{busy ? "Processing…" : "Run safe workspace check"}</button>
            <span className="text-xs text-amber-200">AI and outbound email are disabled</span>
          </div>
          <div className="mt-6 space-y-3">
            {jobs.length === 0 ? <p className="text-sm text-gray-400">No jobs yet. Run the safe check to test the queue.</p> : jobs.map(job => <div key={job.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
              <div className="flex items-center justify-between gap-4"><span className="text-sm font-medium">Workspace health check</span><span className={`flex items-center gap-1 text-xs ${job.status === "succeeded" ? "text-emerald-300" : job.status === "dead" ? "text-rose-300" : "text-amber-200"}`}>{job.status === "succeeded" ? <CheckCircle2 className="h-4 w-4" /> : <CircleAlert className="h-4 w-4" />}{job.status}</span></div>
              <p className="mt-2 text-xs text-gray-400">Attempt {job.attempts}/{job.max_attempts} · {new Date(job.created_at).toLocaleString()}</p>
              {job.result?.note && <p className="mt-2 text-sm text-gray-300">{job.result.note}</p>}
              {job.last_error && <p className="mt-2 text-xs text-rose-200">{job.last_error}</p>}
            </div>)}
          </div>
        </section>}
        {selected && <BillingPanel key={selected.id} workspaceId={selected.id} owner={selected.role === "owner"} onEntitlementChange={setEntitlement} />}
      </>}
      <p className="text-xs leading-5 text-gray-400">Private staging only. Prospecting, email delivery, payment collection and production automation will remain unavailable until verified and enabled separately.</p>
    </div>
  );
}
