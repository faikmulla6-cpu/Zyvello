import { useEffect, useState } from "react";
import { rememberGmailAuthorization } from "./gmail-return";

export type GmailStatus = { connection_available: boolean; email: string | null };

type Props = {
  workspaceId: string;
  entitled: boolean;
  load: () => Promise<GmailStatus>;
  begin: () => Promise<{ authorization_url: string; state: string }>;
  disconnect: () => Promise<void>;
  sendTest: () => Promise<void>;
};

export function HostedGmailConnection({ workspaceId, entitled, load, begin, disconnect, sendTest }: Props) {
  const [status, setStatus] = useState<GmailStatus>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    load().then(value => { if (active) setStatus(value); }).catch(() => {
      if (active) setError("Could not load the Gmail connection.");
    });
    return () => { active = false; };
  }, [load]);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const result = await begin();
      const target = new URL(result.authorization_url);
      if (target.origin !== "https://accounts.google.com" || target.pathname !== "/o/oauth2/v2/auth" ||
          target.searchParams.get("state") !== result.state) throw new Error("Invalid authorization URL");
      rememberGmailAuthorization(workspaceId, result.state);
      window.location.assign(target.href);
    } catch {
      setError("Could not open Google authorization. Try connecting Gmail again.");
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError("");
    try {
      await disconnect();
      setStatus(current => current ? { ...current, email: null } : undefined);
    } catch {
      setError("Could not disconnect Gmail.");
    } finally {
      setBusy(false);
    }
  }

  async function testMailbox() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await sendTest();
      setNotice("Gmail accepted the test message. Check your inbox.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The Gmail test could not be confirmed.");
    } finally {
      setBusy(false);
    }
  }

  return <section className="card" aria-labelledby="gmail-connection-title">
    <h3 className="text-xl font-semibold" id="gmail-connection-title">Gmail</h3>
    {status?.email && <p className="mt-2 break-all text-sm">Connected as {status.email}</p>}
    {status && !status.connection_available && <p className="muted mt-2">Gmail connection is awaiting Google setup.</p>}
    {status?.connection_available && !status.email && !entitled && <p className="muted mt-2">An active subscription is required to connect Gmail.</p>}
    {status?.connection_available && entitled && <button className="btn btn-primary mt-3" type="button" disabled={busy}
      onClick={() => void connect()}>{status.email ? "Reconnect Gmail" : "Connect Gmail"}</button>}
    {status?.email && entitled && <button className="btn btn-secondary mt-3 ml-2" type="button" disabled={busy}
      onClick={() => void testMailbox()}>Send test to my Gmail</button>}
    {status?.email && <button className="btn btn-secondary mt-3 ml-2" type="button" disabled={busy}
      onClick={() => void remove()}>Disconnect Gmail</button>}
    {notice && <p className="mt-3 text-sm" role="status">{notice}</p>}
    {error && <p className="mt-3 text-sm text-[var(--bad)]" role="alert">{error}</p>}
  </section>;
}
