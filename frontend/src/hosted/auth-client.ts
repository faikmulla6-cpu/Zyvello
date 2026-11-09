import { createAuthClient } from "@neon/neon-js/auth";

// The Auth Base URL is public configuration. No PostgreSQL connection string,
// signing secret or provider API key is ever bundled in the browser.
const authBaseUrl = import.meta.env.VITE_NEON_AUTH_URL;

export const authClient =
  typeof authBaseUrl === "string" && authBaseUrl.startsWith("https://")
    ? createAuthClient(authBaseUrl)
    : null;

export const hostedSignUpEnabled = import.meta.env.VITE_ZYVELLO_ENABLE_SIGNUP === "true";
