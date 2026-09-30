/// <reference types="vitest/config" />
import preact from "@preact/preset-vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";

// The build lands inside the Odoo addon that serves it (vlux_pos_app), and is
// committed: installers and the cloud image never need Node. CI rebuilds it
// and fails if the committed copy differs from the source.
export default defineConfig({
  base: "/vlux_pos_app/static/dist/",
  plugins: [
    preact(),
    tailwindcss(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      // Odoo serves the worker at /vlux-pos/sw.js (scope /vlux-pos/) and the
      // manifest itself; the app registers the worker.
      injectRegister: false,
      manifest: false,
      injectManifest: {
        globPatterns: ["**/*.{js,css,svg,png,woff2}"],
        // The worker lives at /vlux-pos/sw.js, not next to the files.
        modifyURLPrefix: { "": "/vlux_pos_app/static/dist/" },
      },
    }),
  ],
  build: {
    outDir: "../../vlux_pos_app/static/dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2022",
  },
  server: {
    // `npm run dev` against a local Odoo on 8069.
    proxy: { "/vlux": "http://127.0.0.1:8069", "/vlux_pos_app/static/img": "http://127.0.0.1:8069" },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    // Before any module loads Dexie: it looks for indexedDB once.
    setupFiles: ["fake-indexeddb/auto"],
  },
});
