import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowRight, BriefcaseBusiness, CheckCircle2, CircleAlert, RefreshCw, ShieldCheck } from "lucide-react";
import { authClient } from "./auth-client";

type Workspace = { id: string; slug: string; name: string; role: string; created_at: string };
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

async function requestApi<T>(path: string, init?: RequestInit): Promise<T> {
  if (!authClient) throw new Error("Authentication is unavailable");
  const token = await authClient.getJWTToken();
  if (!token) {
    throw new Error("Your session could not be verified. Please sign in again.");
  }
  const response = await fetch(path, {
    ...init,
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

export function HostedWorkspaceDashboard({ onSignOut, signingOut }: { onSignOut: () => void; signingOut: boolean }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<string>("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [workspaceSlug, setWorkspaceSlug] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const refreshWorkspaces = useCallback(async () => {
    const result = await requestApi<{ workspaces: Workspace[] }>("/api/v1/workspaces");
    setWorkspaces(result.workspaces);
    setActiveWorkspace(current => current && result.workspaces.some(workspace => workspace.id === current)
      ? current : result.workspaces[0]?.id || "");
  }, []);

  const refreshJobs = useCallback(async (workspaceId: string) => {
    const result = await requestApi<{ jobs: Job[] }>(`/api/v1/workspaces/${workspaceId}/jobs`);
    setJobs(result.jobs);
  }, []);

  useEffect(() => {
    let active = true;
    refreshWorkspaces().catch(cause => {
      if (active) setError(cause instanceof Error ? cause.message : "Could not load workspaces");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshWorkspaces]);

  useEffect(() => {
    if (!activeWorkspace) { setJobs([]); return; }
    let cancelled = false;
    void refreshJobs(activeWorkspace).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load jobs");
    });
    const interval = window.setInterval(() => {
      void refreshJobs(activeWorkspace).catch(() => { /* Keep existing state during transient errors. */ });
    }, 8000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, [activeWorkspace, refreshJobs]);

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

  const selected = workspaces.find(workspace => workspace.id === activeWorkspace);
  return (
    <div className="w-full max-w-4xl space-y-6" aria-label="Your Zyvello workspaces">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-2 rounded-full border border-emerald-400/25 px-3 py-1 text-xs text-emerald-200"><ShieldCheck className="h-4 w-4" /> Verified private beta</span>
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
          <select id="workspace-select" className="mt-2 w-full rounded-xl border border-white/20 bg-zinc-950 p-3 text-white" value={activeWorkspace} onChange={event => setActiveWorkspace(event.target.value)}>
            {workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} ({workspace.role})</option>)}
          </select>
          <p className="mt-3 text-xs text-gray-400">Workspace slug: {selected?.slug}</p>
        </section>}

        {workspaces.length === 0 && <section className="rounded-3xl border border-violet-400/20 bg-violet-500/5 p-5 sm:p-7">
          <h2 className="text-xl font-semibold">Create your first agency workspace</h2>
          <p className="mt-2 text-sm text-gray-300">Your customers and future campaign records will belong to this workspace.</p>
          <form onSubmit={event => void createWorkspace(event)} className="mt-6 space-y-4">
            <div><label htmlFor="workspace-name" className="block text-sm text-gray-300">Agency name</label><input id="workspace-name" className="glass-input mt-2 w-full bg-transparent" value={workspaceName} onChange={event => changeName(event.target.value)} required maxLength={200} placeholder="e.g. Faik Digital" /></div>
            <div><label htmlFor="workspace-slug" className="block text-sm text-gray-300">Workspace URL name</label><input id="workspace-slug" className="glass-input mt-2 w-full bg-transparent" value={workspaceSlug} onChange={event => setWorkspaceSlug(event.target.value)} required minLength={3} maxLength={63} pattern="[a-z0-9][a-z0-9-]{2,62}" /></div>
            <button className="button-light flex w-full items-center justify-center gap-2" type="submit" disabled={busy}>{busy ? "Creating…" : "Create workspace"}<ArrowRight className="h-4 w-4" /></button>
          </form>
        </section>}

        {selected && <section className="rounded-3xl border border-white/15 bg-white/5 p-5 sm:p-7">
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
      </>}
      <p className="text-xs leading-5 text-gray-400">Private staging only. Prospecting, email delivery, payment collection and production automation will remain unavailable until verified and enabled separately.</p>
    </div>
  );
}
