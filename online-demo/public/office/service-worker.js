/* Service worker for the serverless office converter.
 *
 * Not registered directly: register a small script that sets self.OFFICE_ENGINE_DIR and
 * importScripts() this file (it must live at the root of the scope it should control).
 *
 * 1. Cross-origin isolation.  LibreOffice's WebAssembly build uses pthreads,
 *    which need SharedArrayBuffer, which browsers only expose to pages served
 *    with COOP/COEP headers.  GitHub Pages cannot set custom headers, so this
 *    worker adds them to every same-origin response.
 *
 * 2. LibreOffice binaries.  The build is stored as xz-compressed parts (each
 *    below GitHub's file size limits) described by lo/manifest.json.
 *    Requests for e.g. lo/soffice.wasm are answered by streaming the parts
 *    through an xz decoder, so the Emscripten loader sees plain files.  The
 *    decompressed files are kept in Cache Storage per build version, so the
 *    download (and decompression) happens only once per release.
 */
'use strict';

// Where the engine files (lo/, vendor/) are served from.  Set `self.OFFICE_ENGINE_DIR`
// (relative to the service worker's scope) in the script that imports this one;
// default: the scope root.
const ENGINE_DIR = new URL(self.OFFICE_ENGINE_DIR || './', self.registration.scope).href;
importScripts(ENGINE_DIR + 'vendor/xz-decompress.min.js');

const LO_DIR = ENGINE_DIR + 'lo/';
const MANIFEST_URL = LO_DIR + 'manifest.json';
const CACHE_PREFIX = 'lo-assets-';
const progress = new BroadcastChannel('lo-assets');

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'clear-cache') {
        event.waitUntil(clearCaches(null));
    }
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.cache === 'only-if-cached' && request.mode !== 'same-origin') return;
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (request.method === 'GET' && url.href.startsWith(LO_DIR) && url.href !== MANIFEST_URL) {
        event.respondWith(serveLibreOfficeAsset(event, url));
    } else {
        event.respondWith(fetchIsolated(request));
    }
});

function isolationHeaders(headers) {
    headers.set('Cross-Origin-Opener-Policy', 'same-origin');
    headers.set('Cross-Origin-Embedder-Policy', 'require-corp');
    headers.set('Cross-Origin-Resource-Policy', 'same-origin');
    return headers;
}

async function fetchIsolated(request) {
    const response = await fetch(request);
    if (response.status === 0) return response; // opaque; cannot be modified
    return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: isolationHeaders(new Headers(response.headers)),
    });
}

let manifestPromise = null;
let manifestFetchedAt = 0;

function loadManifest() {
    // A service worker instance is short-lived, but refresh anyway after a
    // while so a new deployment is picked up by a long-lived instance.
    if (!manifestPromise || Date.now() - manifestFetchedAt > 60_000) {
        manifestFetchedAt = Date.now();
        manifestPromise = fetch(MANIFEST_URL, { cache: 'no-cache' })
            .then((r) => (r.ok ? r.json() : null))
            .catch(() => null)
            .then((manifest) => {
                if (manifest) clearCaches(CACHE_PREFIX + manifest.version);
                return manifest;
            });
    }
    return manifestPromise;
}

async function clearCaches(keep) {
    for (const name of await caches.keys()) {
        if (name.startsWith(CACHE_PREFIX) && name !== keep) await caches.delete(name);
    }
}

async function serveLibreOfficeAsset(event, url) {
    const manifest = await loadManifest();
    const name = url.href.slice(LO_DIR.length).split(/[?#]/)[0];
    const entry = manifest && manifest.files && manifest.files[name];
    if (!entry) {
        // Uncompressed development build (or file not in the manifest).
        return fetchIsolated(event.request);
    }
    const total = entry.compressedSize;
    const report = (loaded) => {
        progress.postMessage({ file: name, loaded, total, version: manifest.version });
    };
    const cache = await caches.open(CACHE_PREFIX + manifest.version);
    const cacheKey = LO_DIR + name;
    const cached = await cache.match(cacheKey);
    if (cached) {
        report(total);
        return cached;
    }

    const partUrls = entry.parts.map((p) => new URL(p, LO_DIR).href);
    let index = 0;
    let reader = null;
    let loaded = 0;
    let lastReport = 0;
    const compressed = new ReadableStream({
        async pull(controller) {
            for (;;) {
                if (!reader) {
                    if (index >= partUrls.length) {
                        report(loaded);
                        controller.close();
                        return;
                    }
                    const partUrl = partUrls[index++];
                    const response = await fetch(partUrl, { cache: 'no-cache' });
                    if (!response.ok) {
                        throw new Error('Fetching ' + partUrl + ' failed: ' + response.status);
                    }
                    reader = response.body.getReader();
                }
                const { done, value } = await reader.read();
                if (done) {
                    reader = null;
                    continue;
                }
                loaded += value.byteLength;
                if (Date.now() - lastReport > 150) {
                    lastReport = Date.now();
                    report(loaded);
                }
                controller.enqueue(value);
                return;
            }
        },
        cancel() {
            if (reader) reader.cancel();
        },
    });

    let body;
    if (entry.encoding === 'xz') {
        body = new self['xz-decompress'].XzReadableStream(compressed);
    } else if (entry.encoding === 'gzip') {
        body = compressed.pipeThrough(new DecompressionStream('gzip'));
    } else {
        body = compressed;
    }
    const headers = isolationHeaders(new Headers({
        'Content-Type': entry.type || 'application/octet-stream',
        'Content-Length': String(entry.size),
    }));
    // One copy goes to the page, the other into the cache for the next visit.
    const [forPage, forCache] = body.tee();
    event.waitUntil(
        cache.put(cacheKey, new Response(forCache, { headers }))
            .catch((e) => console.warn('sw: caching ' + name + ' failed', e)));
    return new Response(forPage, { status: 200, headers });
}
