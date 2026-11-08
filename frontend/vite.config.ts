import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  base: "/static/landing/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: "../harvey/web/landing",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: fileURLToPath(new URL("./index.html", import.meta.url)),
        mailboxes: fileURLToPath(new URL("./src/mailbox-entry.tsx", import.meta.url)),
        sidebar: fileURLToPath(new URL("./src/sidebar-entry.tsx", import.meta.url)),
      },
      output: {
        entryFileNames: chunk => ["mailboxes", "sidebar"].includes(chunk.name) ? `${chunk.name}.js` : "assets/[name]-[hash].js",
      },
    },
  },
});
