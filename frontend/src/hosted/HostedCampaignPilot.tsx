import { useEffect, useState, type FormEvent } from "react";

type Draft = { id: string; contact_id: string; draft_text: string };
type Contact = { id: string; full_name: string; email: string };
type Delivery = {
  id: string;
  recipient: string;
  subject: string;
  status: "reserved" | "accepted" | "unconfirmed";
  last_reply_at: string | null;
  has_thread: boolean;
};
export type CampaignDeliveryData = {
  deliveries: Delivery[];
  pilot_enabled: boolean;
  pilot_recipient: string | null;
  reply_sync_enabled: boolean;
};

type Props = {
  view: "controls" | "outbox" | "conversations";
  drafts: Draft[];
  contacts: Contact[];
  load: () => Promise<CampaignDeliveryData>;
  recordConsent: (contactId: string, evidence: string) => Promise<unknown>;
  send: (draftId: string, subject: string, body: string) => Promise<{ status: string }>;
  sync: (deliveryId: string) => Promise<{ reply_found: boolean }>;
};

export function HostedCampaignPilot({ view, drafts, contacts, load, recordConsent, send, sync }: Props) {
  const [data, setData] = useState<CampaignDeliveryData>();
  const [draftId, setDraftId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [consentEvidence, setConsentEvidence] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    load().then(result => { if (current) setData(result); }).catch(() => {
      if (current) setError("Could not load campaign deliveries.");
    });
    return () => { current = false; };
  }, [load]);

  const contact = contacts.find(item => item.email === data?.pilot_recipient);
  const eligibleDrafts = drafts.filter(item => item.contact_id === contact?.id);
  const ready = Boolean(data?.pilot_enabled && contact && eligibleDrafts.some(item => item.id === draftId));

  function selectDraft(id: string) {
    const text = eligibleDrafts.find(item => item.id === id)?.draft_text ?? "";
    const subjectLine = text.match(/^\s*Subject:\s*([^\r\n]+)\r?\n/i);
    setDraftId(id);
    setSubject(subjectLine?.[1].trim().slice(0, 160) ?? "");
    setBody(subjectLine ? text.slice(subjectLine[0].length).trim() : text.trim());
    setReviewed(false);
    setConsentEvidence("");
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !ready || !contact || !reviewed) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await recordConsent(contact.id, consentEvidence.trim());
      const result = await send(draftId, subject, body);
      setNotice(result.status === "accepted" ? "Gmail accepted the campaign email. Check the test inbox." :
        "Gmail did not confirm the send. Check Gmail before attempting another delivery.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not confirm the campaign send.");
    } finally {
      setBusy(false);
      setReviewed(false);
      try { setData(await load()); } catch { /* Preserve the result of the send. */ }
    }
  }

  async function checkReply(id: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await sync(id);
      setNotice(result.reply_found ? "Gmail found a reply from the campaign recipient." : "No reply from the campaign recipient yet.");
      setData(await load());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not check the Gmail reply.");
    } finally {
      setBusy(false);
    }
  }

  const deliveries = data?.deliveries.filter(item => view !== "conversations" || item.status === "accepted") ?? [];
  return <section className="card" aria-label={view === "controls" ? "Campaign controls" : "Campaign deliveries"}>
    {view === "controls" && data && !data.pilot_enabled && <p className="muted">Campaign sending is awaiting pilot setup.</p>}
    {view === "controls" && data?.pilot_enabled && <>
      <p className="text-sm break-all">Pilot recipient: {data.pilot_recipient}</p>
      {eligibleDrafts.length === 0 && <p className="muted mt-3">Add the pilot recipient to a campaign and generate a draft first.</p>}
      {eligibleDrafts.length > 0 && <form className="mt-4 space-y-3" onSubmit={event => void start(event)}>
        <fieldset disabled={busy} className="space-y-3">
          <label className="block">Draft to send
            <select className="form-input mt-2 w-full" required value={draftId} onChange={event => selectDraft(event.target.value)}>
              <option value="">Select a draft for {contact?.full_name}</option>
              {eligibleDrafts.map(draft => <option key={draft.id} value={draft.id}>{draft.draft_text.trim().split("\n")[0].slice(0, 90)}</option>)}
            </select>
          </label>
          {draftId && <>
            <label className="block">Subject<input className="form-input mt-2 w-full" required maxLength={160}
              value={subject} onChange={event => { setSubject(event.target.value); setReviewed(false); }} /></label>
            <label className="block">Message<textarea className="form-input mt-2 w-full" required maxLength={5000} rows={7}
              value={body} onChange={event => { setBody(event.target.value); setReviewed(false); }} /></label>
            <label className="block">Permission to contact this recipient
              <input className="form-input mt-2 w-full" required minLength={10} maxLength={1000}
                value={consentEvidence} onChange={event => setConsentEvidence(event.target.value)}
                placeholder="e.g. My own test inbox, approved for this pilot" />
            </label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={reviewed} required
              onChange={event => setReviewed(event.target.checked)} />I reviewed this message and approve sending it once.</label>
          </>}
        </fieldset>
        <button className="btn btn-primary" type="submit" disabled={!ready || !reviewed || busy}>
          {busy ? "Sending…" : "Start pilot send"}
        </button>
      </form>}
    </>}
    {view !== "controls" && data && deliveries.length === 0 && <p className="muted">No campaign messages yet.</p>}
    {view !== "controls" && deliveries.map(delivery => <article className="inset mt-3 space-y-2 rounded-xl p-4" key={delivery.id}>
      <h3 className="font-medium">{delivery.subject}</h3>
      <p className="muted break-all">{delivery.recipient}</p>
      <p>{delivery.status === "accepted" ? "Accepted by Gmail" : "Send unconfirmed. Check Gmail before trying again."}</p>
      {view === "conversations" && <>
        <p>{delivery.last_reply_at ? "Reply received " + new Date(delivery.last_reply_at).toLocaleString() : "No reply recorded"}</p>
        {data?.reply_sync_enabled && delivery.has_thread && <button className="btn btn-secondary" type="button"
          disabled={busy} onClick={() => void checkReply(delivery.id)}>Check Gmail for reply</button>}
        {!data?.reply_sync_enabled && <p className="muted">Gmail reply sync requires message metadata access.</p>}
      </>}
    </article>)}
    {notice && <p role="status" className="mt-3">{notice}</p>}
    {error && <p role="alert" className="mt-3 text-[var(--bad)]">{error}</p>}
  </section>;
}
