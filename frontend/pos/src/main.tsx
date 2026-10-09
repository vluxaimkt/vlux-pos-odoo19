import { render } from "preact";

import { App } from "./app";
import { PosDb } from "./db/db";
import { applyTheme, readTheme } from "./lib/theme";
import "./styles.css";

// The device's light/dark choice before the first paint (no flash of the other theme).
applyTheme(readTheme());
// On "automatic", the system switching light/dark while open recolors the browser bar too.
matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => applyTheme(readTheme()));

const SW_URL = "/vlux-pos/sw.js";

const root = document.getElementById("app");
if (root) render(<App db={new PosDb()} />, root);

/**
 * The page runs under Trusted Types (see vlux_pos_app CSP): no plain string
 * may become a script URL. This one policy, "vlux-pos", is the only way to
 * make one, and it accepts exactly the service worker's URL.
 */
function workerUrl(): string {
  const factory = (window as unknown as {
    trustedTypes?: { createPolicy(name: string, rules: { createScriptURL(url: string): string }): { createScriptURL(url: string): unknown } };
  }).trustedTypes;
  if (!factory) return SW_URL;
  const policy = factory.createPolicy("vlux-pos", {
    createScriptURL(url: string) {
      if (url !== SW_URL) throw new TypeError(`Script no permitido: ${url}`);
      return url;
    },
  });
  return policy.createScriptURL(SW_URL) as string;
}

// Odoo serves the worker at /vlux-pos/sw.js so it controls the whole app
// (scope /vlux-pos/). It keeps the app shell and assets for offline use; data
// lives in IndexedDB, never in the worker's caches.
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    void navigator.serviceWorker.register(workerUrl(), { scope: "/vlux-pos/" });
  });
}
