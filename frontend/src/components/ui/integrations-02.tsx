import { useEffect, useRef, useState } from "react";
import { ArrowRight, ArrowUpRight, Check, CheckCheck, ChevronDown, CircleHelp, CircleCheck, CircleDot, Cloud, ExternalLink, Eye, EyeOff, Fingerprint, LoaderCircle, LockKeyhole, Mail, Send, Settings2, ShieldCheck, X, Zap } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Provider = "gmail" | "microsoft" | "smtp" | "instantly";
type Connection = { id: Provider; status: string; app_ready: boolean; email: string; message: string; checked_at: number | null; redirect_uri: string | null };
type Snapshot = { active_provider: string; providers: Connection[]; fields: Record<string, string>; saved_secrets: Record<string, boolean> };
const providers: {id: Provider; title: string; description: string; detail: string; color: string; icon: typeof Mail}[] = [
  {id: "gmail", title: "Gmail", description: "Google Workspace & personal Gmail", detail: "Continue with Google. Your mailbox, your conversations.", color: "gmail", icon: Mail},
  {id: "microsoft", title: "Microsoft Outlook", description: "Microsoft 365 & supported Outlook accounts", detail: "Secure Microsoft sign-in. Your organization may require approval.", color: "microsoft", icon: Cloud},
  {id: "smtp", title: "Other email provider", description: "Hostinger, Fastmail & SMTP / IMAP", detail: "Bring your business mailbox. We’ll check both sending and inbox access.", color: "smtp", icon: Send},
  {id: "instantly", title: "Instantly", description: "Keep your existing outreach workspace", detail: "Connect your existing Instantly account using its API key.", color: "instantly", icon: Zap},
];
const names: Record<string,string> = {connected: "Connected", saved: "Ready to verify", not_connected: "Not connected", setup_required: "Not connected", reconnect: "Needs attention"};
let token = "";
const session = document.querySelector<HTMLMetaElement>('meta[name="dashboard-session"]');
if (session) { token = session.content; session.remove(); }
async function api<T>(path: string, data?: object): Promise<T> {
  const fetcher = (window as Window & {authFetch?: typeof fetch}).authFetch || fetch;
  const response = await fetcher("/api/mailboxes" + path, {method: data === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: {"Content-Type": "application/json", ...(token ? {Authorization: "Bearer " + token} : {})},
    body: data === undefined ? undefined : JSON.stringify(data), signal: AbortSignal.timeout(50000)});
  if (response.status === 401) { window.location.assign("/login"); throw new Error("Please log in again."); }
  const result = await response.json();
  if (!response.ok || result.success === false) throw new Error(result.message || "We couldn’t complete that action. Please try again.");
  return result;
}
type Feedback = {kind: "success" | "error"; message: string} | null;
function ProviderMark({ id, large = false }: {id: Provider; large?: boolean}) {
  const item = providers.find(p => p.id === id)!; const Icon = item.icon;
  return <span className={cn("connection-mark", item.color, large && "large")}><Icon size={large ? 27 : 21} strokeWidth={1.65} /></span>;
}
function SecretField({name, value, onChange, saved}: {name: string; value: string; onChange: (v:string)=>void; saved?: boolean}) {
  const [show, setShow] = useState(false);
  return <div className="connection-secret"><input id={name} type={show ? "text" : "password"} value={value} autoComplete="off" placeholder={saved ? "Saved securely · enter to replace" : "Enter your credential"} onChange={e=>onChange(e.target.value)} /><button type="button" aria-label={show ? "Hide credential" : "Show credential"} onClick={()=>setShow(!show)}>{show ? <EyeOff size={17}/> : <Eye size={17}/>}</button></div>;
}

export default function Integrations() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [selected, setSelected] = useState<Provider | null>(null);
  const [fields, setFields] = useState<Record<string,string>>({});
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [pageError, setPageError] = useState("");
  const [guide, setGuide] = useState(false);
  const [preset, setPreset] = useState("custom");
  const [confirmSend, setConfirmSend] = useState(false);
  const [view, setView] = useState<"connect" | "advanced">("connect");
  const dialog = useRef<HTMLDialogElement>(null);
  const operation = useRef(false);
  async function load() { const snapshot = await api<Snapshot>(""); setData(snapshot);
    const active = document.getElementById("active-provider");
    if (active) active.textContent = snapshot.active_provider;
    return snapshot; }
  useEffect(()=>{
    const refresh = () => { if (!operation.current && !dialog.current?.open) load().catch(error=>setPageError(error.message)); };
    refresh();
    window.addEventListener("zyvello:settings-open", refresh);
    window.addEventListener("zyvello:mailbox-settings-saved", refresh);
    return () => { window.removeEventListener("zyvello:settings-open", refresh); window.removeEventListener("zyvello:mailbox-settings-saved", refresh); };
  }, []);
  useEffect(()=>{
    if (selected && !dialog.current?.open) dialog.current?.showModal();
    if (!selected && dialog.current?.open) dialog.current.close();
  }, [selected]);
  useEffect(()=>{
    if (!data) return;
    const params = new URLSearchParams(window.location.search);
    const id = params.get("provider") as Provider; const result = params.get("result");
    if (result && providers.some(p=>p.id===id)) {
      setSelected(id); setFields({...data.fields});
      setFeedback({kind: result === "authorized" ? "success" : "error", message: result === "authorized" ? "Account authorized. Test the connection to verify inbox access, then choose Use this mailbox." : result === "cancelled" ? "Sign-in was cancelled. Your existing mailbox hasn’t changed." : "Sign-in couldn’t finish. Check the app’s redirect URL and permissions, then reconnect."});
      window.history.replaceState(null, "", "/#settings");
    }
  }, [data]);
  useEffect(()=>{
    if (!selected) return;
    const targets = [document.documentElement, document.body, document.querySelector("main")].filter((item): item is HTMLElement => item instanceof HTMLElement);
    const previous = targets.map(item => item.style.overflow);
    targets.forEach(item => { item.style.overflow = "hidden"; });
    return () => targets.forEach((item, index) => { item.style.overflow = previous[index]; });
  }, [selected]);
  const connected = data?.providers.filter(p=>p.status==="connected") || [];
  const current = data?.providers.find(p=>p.id===selected);
  const definition = providers.find(p=>p.id===selected);
  const dirty = Object.entries(fields).some(([key,value]) => data?.saved_secrets[key] !== undefined ? !!value : value !== (data?.fields[key] || ""));
  function open(id: Provider) { setFields({...data?.fields}); setFeedback(null); setSelected(id); setConfirmSend(false); setPreset("custom"); setView("connect"); }
  async function run(label: string, action: ()=>Promise<void>) {
    if (operation.current) return;
    operation.current = true; setBusy(label); setFeedback(null);
    try { await action(); } catch (error) { setFeedback({kind:"error", message: error instanceof Error ? error.message : "Something went wrong. Please retry."}); }
    finally { operation.current = false; setBusy(""); }
  }
  function update(name:string, value:string) { setFields(old=>({...old,[name]:value})); }
  function presetChanged(value:string) {
    setPreset(value);
    const presetFields: Record<string,string> = value === "hostinger" ? {SMTP_HOST:"smtp.hostinger.com", SMTP_PORT:"465", IMAP_HOST:"imap.hostinger.com",IMAP_PORT:"993"} : value === "fastmail" ? {SMTP_HOST:"smtp.fastmail.com",SMTP_PORT:"465",IMAP_HOST:"imap.fastmail.com",IMAP_PORT:"993"} : {};
    setFields(old=>({...old,...presetFields}));
  }
  async function saveFields() {
    const prefix = selected === "gmail" ? ["GMAIL_"] : selected === "microsoft" ? ["M365_"] : selected === "smtp" ? ["SMTP_", "IMAP_"] : ["INSTANTLY_"];
    const changes = Object.fromEntries(Object.entries(fields).filter(([key,value])=>prefix.some(p=>key.startsWith(p)) && (data?.saved_secrets[key] === undefined || !!value)));
    const result = await api<{message:string}>(`/${selected}/save`, {fields: changes});
    const updated = await load(); setFields({...updated.fields});
    window.dispatchEvent(new Event("zyvello:mailbox-saved"));
    setFeedback({kind:"success", message:result.message});
  }
  async function connectOAuth() {
    const result = await api<{url:string}>(`/oauth/${selected}/start`, {});
    const url = new URL(result.url);
    if (!["accounts.google.com", "login.microsoftonline.com"].includes(url.hostname) || url.protocol !== "https:") throw new Error("Invalid sign-in destination.");
    window.location.assign(result.url);
  }
  function field(name:string, label:string, secret=false, hint?:string) {
    return <div className="connection-field" key={name} data-field={name}><label htmlFor={name}>{label}</label>{secret ? <SecretField name={name} value={fields[name] || ""} onChange={value=>update(name,value)} saved={data?.saved_secrets[name]} /> : <input id={name} value={fields[name] || ""} onChange={e=>update(name,e.target.value)} autoComplete="off" inputMode={name.endsWith("PORT") ? "numeric" : undefined} />}{hint && <small>{hint}</small>}</div>;
  }
  return <div className="connections-page embedded">
      <div className="connections-content">
        {pageError && <div className="connection-feedback error" role="alert">{pageError}<Button variant="outline" size="sm" onClick={()=>load().then(()=>setPageError("")).catch(e=>setPageError(e.message))}>Try again</Button></div>}
        {guide && <section className="connection-guide"><div><h2>A connection in three steps</h2><Button variant="ghost" size="icon" aria-label="Close guide" onClick={()=>setGuide(false)}><X size={18}/></Button></div><ol><li>Choose your email provider and sign in, or enter your mailbox settings.</li><li>Test the connection. Then send yourself a test email if you wish.</li><li>Select Use this mailbox. Restart automation to apply the change.</li></ol><p>Google/Microsoft app credentials are configured once by the installation administrator. Personal Microsoft accounts require an app that supports them. Your sending provider’s limits still apply.</p></section>}
        <div className="connections-grid"><section className="connections-glass integrations-list"><div className="connections-section-head"><div><h2>Mailbox connections <span>04</span></h2><p>Your inbox is where the next opportunity begins.</p></div><span className="connection-live-dot"/></div>
          <div className="connections-provider-list">{providers.map(item=>{
            const connection = data?.providers.find(p=>p.id===item.id);
            return <div className="connection-provider" key={item.id}><ProviderMark id={item.id}/><div className="connection-provider-copy"><h3>{item.title}{data?.active_provider===item.id && <span className="connection-active-label">Active</span>}</h3><p>{connection?.email || item.description}</p></div><div className="connection-provider-actions">{connection?.status==="connected" ? <Badge className="connection-badge connected"><Check size={12}/>Connected</Badge> : connection?.status==="reconnect" ? <Badge className="connection-badge attention">Needs attention</Badge> : null}<Button variant="outline" size="sm" disabled={!data} onClick={()=>open(item.id)}>{connection?.status==="connected" ? "Manage" : connection?.status==="reconnect" ? "Reconnect" : "Connect"}<ArrowUpRight size={14}/></Button></div></div>;
          })}</div>
          <div className="connections-list-footer"><Fingerprint size={17}/><span>Credentials stay private. You stay in control.</span><ShieldCheck size={16}/></div>
        </section>
        <aside className="connections-right"><section className="connections-glass connection-readiness"><div className="connection-readiness-top"><span className="connection-mini-icon"><CheckCheck size={19}/></span><span>CONNECTION HEALTH</span></div><div className="connection-health-number">{data ? String(connected.length).padStart(2,"0") : "—"}<span>/ 04</span></div><h2>{connected.length ? "You’re making connections." : "Ready when you are."}</h2><p>{connected.length ? "Verified connections are ready for your workflow. Choose which mailbox Zyvello should use." : "Your mailbox is the first step. Connect one to bring your outreach to life."}</p><div className="connection-progress-track"><span style={{width:`${connected.length/4*100}%`}}/></div><div className="connection-readiness-bottom"><span>{connected.length} verified {connected.length===1?"connection":"connections"}</span><CircleDot size={14}/></div></section>
        <section className="connection-security"><span><ShieldCheck size={19}/></span><div><h3>Direct. Secure. Yours.</h3><p>Google and Microsoft use provider sign-in. Secure presets simplify SMTP and IMAP setup for other mailboxes.</p><button onClick={()=>setGuide(true)}>How connections work <ArrowRight size={13}/></button></div></section></aside></div>
      </div>
    <dialog ref={dialog} className="connection-dialog" aria-labelledby="connection-dialog-title" onCancel={event=>{event.preventDefault();if(!busy)setSelected(null);}} onClick={event=>{if(event.target===dialog.current && !busy)setSelected(null);}}>
      {selected && definition && <div className="connection-dialog-inner">
        <Button className="connection-dialog-close" variant="ghost" size="icon" disabled={!!busy} aria-label="Close connection" onClick={()=>setSelected(null)}><X size={20}/></Button>
        <aside className="connection-dialog-summary">
          <ProviderMark id={selected} large/>
          <div className="connections-eyebrow">MAILBOX CONNECTION</div>
          <h2 id="connection-dialog-title">{definition.title}</h2>
          <p className="connection-dialog-description">{definition.detail}</p>
          <Badge className={cn("connection-badge",current?.status==="connected" && "connected")}>{names[current?.status || "not_connected"]}</Badge>
          {current?.checked_at && <p className="connection-admin-hint">Last checked {new Date(current.checked_at * 1000).toLocaleString()}</p>}
          {(current?.status!=="connected" || selected==="instantly") && <div className="connection-dialog-steps"><span><span>1</span>Connect your account</span><span><span>2</span>Verify the connection</span><span><span>3</span>Choose your mailbox</span></div>}
        {selected!=="instantly" && current?.status==="connected" && <div className="connection-send-test"><label><input type="checkbox" disabled={!!busy} checked={confirmSend} onChange={e=>setConfirmSend(e.target.checked)}/>Send one test email to {current.email || "this mailbox"}</label><Button variant="ghost" size="sm" disabled={!!busy || !confirmSend || dirty} onClick={()=>run("Sending test…",async()=>{const result=await api<{message:string}>(`/${selected}/send-test`,{confirm:true});setConfirmSend(false);setFeedback({kind:"success",message:result.message});})}><Send size={14}/>Send test email</Button></div>}
          <p className="connection-summary-security"><ShieldCheck size={16}/>Your credentials stay private.</p>
        </aside>
        <section className="connection-dialog-workspace">
        {selected!=="instantly" ? <div className="connection-dialog-tabs" role="tablist" aria-label="Connection settings" onKeyDown={event=>{
          if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
          event.preventDefault();
          const next = event.key==="Home" ? "connect" : event.key==="End" ? "advanced" : view==="connect" ? "advanced" : "connect";
          setView(next);
          event.currentTarget.querySelector<HTMLButtonElement>(next==="connect"?"#connection-connect-tab":"#connection-advanced-tab")?.focus();
        }}><button id="connection-connect-tab" role="tab" tabIndex={view==="connect"?0:-1} aria-selected={view==="connect"} aria-controls="connection-tab-panel" disabled={!!busy} onClick={()=>setView("connect")}>{selected==="smtp"?"Mailbox details":"Connect account"}</button><button id="connection-advanced-tab" role="tab" tabIndex={view==="advanced"?0:-1} aria-selected={view==="advanced"} aria-controls="connection-tab-panel" disabled={!!busy} onClick={()=>setView("advanced")}>{selected==="smtp"?"Inbox credentials":"Administrator setup"}</button></div> : <h3 className="connection-workspace-title">Connect your workspace</h3>}
        <fieldset id="connection-tab-panel" role="tabpanel" aria-labelledby={selected!=="instantly"?(view==="connect"?"connection-connect-tab":"connection-advanced-tab"):undefined} disabled={!!busy} className="connection-dialog-fields"><legend className="sr-only">Mailbox settings</legend>
        {(selected==="gmail" || selected==="microsoft") && (view==="connect" ? <div className="connection-connect-view">
          <div className="connection-oauth-note"><LockKeyhole size={18}/><p>Your password stays with {selected==="gmail"?"Google":"Microsoft"}. Allow mailbox access on their sign-in screen.</p></div>
          <Button className="w-full" disabled={!current?.app_ready || dirty} onClick={()=>run("Opening sign-in…",connectOAuth)}>{current?.status==="reconnect"?"Reconnect with":"Continue with"} {selected==="gmail"?"Google":"Microsoft"}<ArrowUpRight size={16}/></Button>
          {!current?.app_ready && <div className="connection-setup-notice"><p>A one-time administrator setup is needed to enable sign-in.</p><button onClick={()=>setView("advanced")}>Set up application <ArrowRight size={13}/></button></div>}
        </div> : <div className="connection-admin-view">
          <p className="connection-admin-intro">Configure your app once. Register this exact redirect URL with your provider.</p>
          <code className="connection-redirect-url">{current?.redirect_uri}</code>
          {selected==="gmail"?<>{field("GMAIL_CLIENT_ID","Google client ID")}{field("GMAIL_CLIENT_SECRET","Google client secret",true)}<a className="connection-console-link" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Open Google Cloud Console <ExternalLink size={12}/></a></>:<>{field("M365_CLIENT_ID","Microsoft application ID")}{field("M365_TENANT_ID","Tenant ID or account audience",false,"Match your app: organizations, common, consumers, or tenant ID.")}{field("M365_CLIENT_SECRET","Client secret (web applications only)",true)}<a className="connection-console-link" href="https://entra.microsoft.com/" target="_blank" rel="noreferrer">Open Microsoft Entra <ExternalLink size={12}/></a></>}
          <Button variant="outline" className="w-full" onClick={()=>run("Saving…",saveFields)}>Save administrator settings</Button>
        </div>)}
        {selected==="smtp" && <>
          {view==="connect" ? <><div className="connection-field"><label htmlFor="mailbox-preset">Email provider</label><select id="mailbox-preset" value={preset} onChange={e=>presetChanged(e.target.value)}><option value="custom">Custom / another provider</option><option value="hostinger">Hostinger Email</option><option value="fastmail">Fastmail</option></select></div>
          <div className="connection-credential-grid">{field("SMTP_USERNAME","Email address / SMTP username")}{field("SMTP_PASSWORD","Mailbox or app password",true)}</div>
          <div className="connection-form-grid">{field("SMTP_HOST","SMTP server")}{field("SMTP_PORT","SMTP port")}{field("IMAP_HOST","IMAP server")}{field("IMAP_PORT","IMAP port")}</div>
          <p className="connection-admin-hint">Server presets are editable. Separate inbox credentials are optional.</p></> : <div className="connection-inbox-view"><div className="connection-oauth-note"><Mail size={18}/><p>By default, replies use your SMTP login. Add different IMAP credentials here when needed.</p></div>{field("IMAP_USERNAME","IMAP username",false,"Leave blank to use your SMTP username.")}{field("IMAP_PASSWORD","IMAP password",true,"Blank keeps the saved password; otherwise SMTP is used.")}</div>}
          <Button className="w-full" onClick={()=>run("Saving…",saveFields)}>Save mailbox details<ArrowRight size={15}/></Button>
        </>}
        {selected==="instantly" && <>{field("INSTANTLY_API_KEY","Instantly API key",true)}<p className="connection-admin-hint">Requires an existing Instantly plan with API access. Campaigns already running there remain managed by Instantly.</p><Button className="w-full" onClick={()=>run("Saving…",saveFields)}>Save API key</Button></>}
        {(view==="connect" || selected==="instantly") && <div className="connection-dialog-actions"><Button variant="outline" disabled={!current?.app_ready || dirty} onClick={()=>run("Checking connection…",async()=>{const result=await api<{message:string}>(`/${selected}/test`,{});await load();setFeedback({kind:"success",message:result.message});})}><ShieldCheck size={15}/>Test connection</Button><Button variant="outline" disabled={current?.status!=="connected" || dirty} onClick={()=>run("Updating mailbox…",async()=>{const result=await api<{message:string}>(`/${selected}/activate`,{});await load();setFeedback({kind:"success",message:result.message});})}>Use this mailbox<Check size={15}/></Button></div>}

        </fieldset>
        {busy && <p className="connection-busy" role="status"><LoaderCircle size={16} className="animate-spin"/>{busy}</p>}
        {feedback && <div className={cn("connection-feedback",feedback.kind)} role={feedback.kind==="error"?"alert":"status"}>{feedback.kind==="success"?<CircleCheck size={18}/>:<CircleHelp size={18}/>}<span>{feedback.message}</span></div>}
        <p className="connection-dialog-foot"><LockKeyhole size={12}/>Saving a connection never starts a campaign.</p>
        </section>
      </div>}
    </dialog>
  </div>;
}
