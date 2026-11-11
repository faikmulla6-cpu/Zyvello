// Guard the static hosting build against silently publishing the local-owner login.
const failures = [];

if (process.env.VITE_ZYVELLO_HOSTED_MODE !== "true") {
  failures.push("VITE_ZYVELLO_HOSTED_MODE must be true");
}
if (process.env.VITE_ZYVELLO_ENABLE_SIGNUP !== "false") {
  failures.push("VITE_ZYVELLO_ENABLE_SIGNUP must be false for private beta");
}

const authBase = process.env.VITE_NEON_AUTH_URL;
try {
  const url = new URL(authBase);
  if (url.protocol !== "https:" || url.username || url.password ||
      !url.hostname.endsWith(".neon.tech") || !/\/auth\/?$/.test(url.pathname)) {
    failures.push("VITE_NEON_AUTH_URL must be a public HTTPS Neon Auth base URL");
  }
} catch {
  failures.push("VITE_NEON_AUTH_URL must be set to a valid public Neon Auth base URL");
}

const apiBase = process.env.VITE_ZYVELLO_API_URL;
try {
  const url = new URL(apiBase);
  if (url.protocol !== "https:" || url.username || url.password ||
      !url.hostname.endsWith(".modal.run") || url.pathname !== "/" ||
      url.search || url.hash) {
    failures.push("VITE_ZYVELLO_API_URL must be the public HTTPS Modal API origin");
  }
} catch {
  failures.push("VITE_ZYVELLO_API_URL must be set to the hosted Modal API origin");
}

if (failures.length) {
  for (const issue of failures) console.error("[Zyvello release gate] " + issue);
  process.exitCode = 1;
} else {
  console.log("Zyvello hosted private-beta build preflight passed.");
}
