/// <reference lib="webworker" />
// Service worker of the VLUX register. Served by Odoo at /vlux-pos/sw.js with
// scope /vlux-pos/.
//
// - Built assets (hashed names) are precached, so the app starts offline.
// - The page itself (/vlux-pos/) is network-first with a cached fallback: a new
//   release is picked up whenever the server is reachable.
// - API calls are never cached here: the catalog and the sales queue live in
//   IndexedDB, and the API answers no-store.
import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";
import { NavigationRoute, registerRoute } from "workbox-routing";
import { NetworkFirst } from "workbox-strategies";

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: Array<{ url: string; revision: string | null }> };

cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

const SHELL_CACHE = "vlux-pos-shell";
const SHELL_URL = "/vlux-pos/";

registerRoute(
  new NavigationRoute(new NetworkFirst({ cacheName: SHELL_CACHE, networkTimeoutSeconds: 4 }), {
    allowlist: [/^\/vlux-pos\/?$/],
  }),
);

self.addEventListener("install", (event) => {
  // The page that installed the worker was loaded before it existed: keep a
  // copy now, so the next start works without the network.
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.add(new Request(SHELL_URL, { cache: "reload" })))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
