// Publish only public branding assets needed by the standalone landing page.
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

for (const asset of ["favicon.svg", "landing/assets/agency-office.jpg", "landing/assets/dashboard-preview.png"]) {
  const source = asset === "favicon.svg"
    ? new URL("../../harvey/web/favicon.svg", import.meta.url)
    : new URL(`../public/${asset.slice("landing/".length)}`, import.meta.url);
  const target = new URL(`../dist/static/${asset}`, import.meta.url);
  mkdirSync(fileURLToPath(new URL(".", target)), { recursive: true });
  copyFileSync(source, target);
}

// Static hosts must be able to serve direct visits and refreshes without
// relying on the Python server's page routes or host-specific SPA rewrites.
for (const route of ["login", "signup", "welcome"]) {
  const directory = new URL(`../dist/${route}/`, import.meta.url);
  mkdirSync(fileURLToPath(directory), { recursive: true });
  copyFileSync(new URL("../dist/index.html", import.meta.url), new URL("index.html", directory));
}
