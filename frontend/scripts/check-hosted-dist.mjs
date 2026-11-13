// Fail the isolated hosted release if direct-route assets or API config are absent.
import { existsSync, readdirSync, readFileSync } from "node:fs";

const required = [
  "index.html", "login/index.html", "signup/index.html",
  "welcome/index.html", "app/index.html", "subscription-policy/index.html", "static/favicon.svg",
];
const missing = required.filter((name) => !existsSync(new URL(`../dist/${name}`, import.meta.url)));
if (missing.length) {
  console.error("[Zyvello release gate] Missing built routes/assets:", missing.join(", "));
  process.exit(1);
}

const assets = new URL("../dist/assets/", import.meta.url);
const scripts = readdirSync(assets)
  .filter((name) => name.endsWith(".js"))
  .map((name) => readFileSync(new URL(name, assets), "utf8"));
const apiOrigin = new URL(process.env.VITE_ZYVELLO_API_URL).origin;
if (!scripts.some((script) => script.includes(apiOrigin))) {
  console.error("[Zyvello release gate] Compiled frontend has no configured Modal API origin.");
  process.exit(1);
}
console.log("Zyvello hosted routes and compiled API origin verified.");
