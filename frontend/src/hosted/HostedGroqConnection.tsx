import { useEffect, useState, type FormEvent } from "react";

export type GroqStatus = {
  configured: boolean;
  credential_storage_ready: boolean;
  ai_execution_enabled: boolean;
};

type Props = {
  load: () => Promise<GroqStatus>;
  save: (key: string) => Promise<GroqStatus>;
  remove: () => Promise<void>;
};

export function HostedGroqConnection({ load, save, remove }: Props) {
  const [status, setStatus] = useState<GroqStatus>();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    load().then(value => { if (active) setStatus(value); }).catch(() => {
      if (active) setError("Could not load the Groq connection. Reopen Settings to retry.");
    });
    return () => { active = false; };
  }, [load]);

  async function saveKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !status?.credential_storage_ready) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      setStatus(await save(key.trim()));
      setKey("");
      setNotice("Groq key saved for this workspace.");
    } catch {
      setError("Could not save the Groq key. Check the key format and your workspace access.");
    } finally {
      setBusy(false);
    }
  }

  async function removeKey() {
    if (busy || !status?.configured) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await remove();
      setStatus({ ...status, configured: false });
      setKey("");
      setNotice("Groq key removed from this workspace.");
    } catch {
      setError("Could not remove the Groq key. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="card" aria-labelledby="groq-connection-title">
    <h3 className="text-xl font-semibold" id="groq-connection-title">AI connection</h3>
    <p className="muted mt-2">Use your own Groq API key for campaign drafts.</p>
    {status && <p className="mt-3 text-sm">{status.configured ? "Groq key saved" : "Groq key needed"}</p>}
    {status && !status.ai_execution_enabled && <p className="muted mt-2 text-sm">AI draft generation is currently unavailable.</p>}
    {status && !status.credential_storage_ready && <p className="muted mt-2 text-sm">Encrypted key storage is currently unavailable.</p>}
    {status?.credential_storage_ready && <form className="mt-4 space-y-3" onSubmit={event => void saveKey(event)}>
      <label className="block text-sm">Groq API key
        <input className="form-input mt-2 w-full" type="password" value={key}
          autoComplete="new-password" spellCheck={false} required minLength={36} maxLength={256}
          pattern="gsk_.{32,252}" onChange={event => setKey(event.target.value)} disabled={busy} />
      </label>
      <button className="btn btn-primary" type="submit" disabled={busy}>{status.configured ? "Replace key" : "Save key"}</button>
    </form>}
    {status?.configured && <button className="btn btn-secondary mt-3" type="button" disabled={busy}
      onClick={() => void removeKey()}>Remove key</button>}
    {notice && <p className="mt-3 text-sm" role="status">{notice}</p>}
    {error && <p className="mt-3 text-sm text-[var(--bad)]" role="alert">{error}</p>}
  </section>;
}
