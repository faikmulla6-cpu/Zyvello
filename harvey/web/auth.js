// ── Dashboard authorisation ──
//
// The local page initializes its API session automatically, before data loads.
// Older private links also work: their fragment is removed from the address bar.
// The session stays in memory and, when available, this tab's sessionStorage.
// Requests carry it only in a same-origin Authorization header.

const AUTH_KEY = 'harvey-dashboard-token';
const AUTH_TOKEN_RE = /^[A-Za-z0-9_-]{43,128}$/;
let pageToken = '';

(function takeFragmentToken() {
  const hash = window.location.hash || '';
  if (!hash) return;
  if (!new URLSearchParams(hash.slice(1)).has('token')) return;
  const token = new URLSearchParams(hash.slice(1)).get('token');
  history.replaceState(null, '', window.location.pathname + window.location.search);
  if (token && AUTH_TOKEN_RE.test(token)) setAuthToken(token);
})();

(function initializeLocalSession() {
  const session = document.querySelector('meta[name="dashboard-session"]');
  if (!session) return;
  setAuthToken(session.content);
  session.remove();
})();

function authToken() {
  if (pageToken) return pageToken;
  try { return sessionStorage.getItem(AUTH_KEY) || ''; } catch { return ''; }
}

function setAuthToken(token) {
  if (!AUTH_TOKEN_RE.test(token || '')) return false;
  pageToken = token;
  try { sessionStorage.setItem(AUTH_KEY, token); } catch { /* memory is enough */ }
  return true;
}

function clearAuthToken() {
  pageToken = '';
  try { sessionStorage.removeItem(AUTH_KEY); } catch { /* ignore */ }
}

// A same-origin URL for an absolute path ("/api/..."), or null.
function sameOriginUrl(path) {
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//') ||
      path.includes('\\')) return null;
  let url;
  try { url = new URL(path, window.location.origin); } catch { return null; }
  return url.origin === window.location.origin ? url : null;
}

// fetch() for this dashboard's own API, with the token attached. Refuses
// anything that isn't same-origin, and never follows redirects.
function authFetch(path, opts) {
  const url = sameOriginUrl(path);
  if (!url) return Promise.reject(new Error('refusing to send the dashboard token off-origin'));
  const o = Object.assign({}, opts || {});
  const headers = new Headers(o.headers || {});
  const token = authToken();
  if (token) headers.set('Authorization', 'Bearer ' + token);
  o.headers = headers;
  o.mode = 'same-origin';
  o.credentials = 'same-origin';
  o.cache = 'no-store';
  o.redirect = 'error';
  o.referrerPolicy = 'no-referrer';
  return fetch(url.href, o);
}
