const pendingKey = "zyvello-gmail-authorization";

export function rememberGmailAuthorization(workspaceId: string, state: string) {
  sessionStorage.setItem(pendingKey, JSON.stringify({ workspaceId, state }));
}

function takeGmailReturn() {
  if (location.pathname.replace(/\/$/, "") !== "/app") return undefined;
  const url = new URL(location.href);
  if (!url.searchParams.has("state") || (!url.searchParams.has("code") && !url.searchParams.has("error"))) {
    return undefined;
  }
  const { searchParams } = url;
  const code = searchParams.get("code");
  const state = searchParams.get("state");
  for (const name of ["code", "state", "error", "error_description", "scope", "authuser", "prompt", "hd"]) {
    searchParams.delete(name);
  }
  url.hash = "settings";
  history.replaceState(history.state, "", url);
  try {
    const saved = sessionStorage.getItem(pendingKey);
    sessionStorage.removeItem(pendingKey);
    const pending = saved ? JSON.parse(saved) : null;
    if (pending && typeof pending.workspaceId === "string" && state && state === pending.state) {
      return { workspaceId: pending.workspaceId as string, state, code };
    }
  } catch {
    // The authorization was not started in this tab.
  }
  return undefined;
}

export const gmailReturn = takeGmailReturn();
