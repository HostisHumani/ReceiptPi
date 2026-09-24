// ReceiptPi service worker. Served by app.py's /sw.js route (not as a
// plain static file): a worker's maximum scope is the path it's served
// from, and only a root-level URL can control the whole app ("/"). The
// route also fills in __RECEIPTPI_VERSION__ below, see CACHE_NAME.
// Registered only over HTTPS, see the script at the end of
// templates/base.html.
//
// Deliberately minimal and WHITELIST-only: the only requests this
// worker ever answers are same-origin GETs under /static/ that carry
// the ?v=<content hash> query param added by static_versioning.py
// (static_url() in templates, versioned_style_css() for icons/fonts
// referenced from style.css). Such a URL never changes content - a
// changed file gets a new hash, i.e. a new URL - so serving it
// cache-first can't hand out anything stale. Every other request -
// pages, /health, /print/*, /ui/* form posts, /settings/*, JSON
// endpoints, unversioned static files, anything cross-origin, every
// non-GET - never reaches the cache: the fetch handler returns without
// respondWith(), so the browser handles it exactly as if no service
// worker existed.

const VERSION = "__RECEIPTPI_VERSION__";
const CACHE_PREFIX = "receiptpi-static-";
// Tied to the VERSION file: a release bump changes this script's bytes,
// the browser installs the new worker, and "activate" below deletes
// every cache but this one.
const CACHE_NAME = CACHE_PREFIX + VERSION;

self.addEventListener("install", () => {
  // Nothing to precache - assets are cached on first use. Take over
  // right away instead of waiting for every open tab to close.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Every other cache on this origin goes: previous releases' caches
    // and anything not created by this worker at all.
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name !== CACHE_NAME).map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

function isCacheable(request) {
  if (request.method !== "GET") return false;
  const url = new URL(request.url);
  return url.origin === self.location.origin
    && url.pathname.startsWith("/static/")
    && url.searchParams.has("v");
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response.ok && response.type === "basic") {
    // A deploy that changes a file without a VERSION bump gives it a
    // new ?v= - drop the older variants of the same path first, so the
    // cache holds one copy per file instead of growing with every deploy
    // until the next release.
    const path = new URL(request.url).pathname;
    const stale = (await cache.keys()).filter((key) => new URL(key.url).pathname === path);
    await Promise.all(stale.map((key) => cache.delete(key)));
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  if (!isCacheable(event.request)) return;
  event.respondWith(cacheFirst(event.request));
});
