/* Cross-origin isolation for hosts that can't send COOP/COEP headers (e.g. GitHub Pages).
 *
 * LibreOffice's WebAssembly build needs SharedArrayBuffer, which browsers only expose to
 * cross-origin isolated pages.  service-worker.js adds the headers; the first visit is not
 * yet controlled by it, so the page reloads itself once after the worker takes over.
 */

/**
 * @param {string|URL} serviceWorkerUrl URL of a script that imports service-worker.js (see README).
 * @returns {Promise<boolean>} Whether the page is cross-origin isolated now.  When a reload is
 *   needed to become isolated, the page reloads and the promise stays pending.
 */
export async function ensureCrossOriginIsolation(serviceWorkerUrl) {
    const KEY = 'office-converter-coi-reload';
    const reloaded = () => {
        try { return sessionStorage.getItem(KEY); } catch { return null; }
    };
    const mark = () => {
        try { sessionStorage.setItem(KEY, '1'); } catch { /* private mode: reload loop guard is best effort */ }
    };
    if (window.crossOriginIsolated) {
        try { sessionStorage.removeItem(KEY); } catch { /* ignore */ }
        return true;
    }
    if (!('serviceWorker' in navigator)) return false;
    try {
        await navigator.serviceWorker.register(serviceWorkerUrl);
    } catch (error) {
        console.error('Service worker registration failed', error);
        return false;
    }
    if (reloaded()) return false; // already reloaded once and still not isolated
    const reload = () => {
        mark();
        location.reload();
    };
    if (navigator.serviceWorker.controller) {
        reload();
    } else {
        navigator.serviceWorker.addEventListener('controllerchange', reload);
    }
    return new Promise(() => {}); // pending until the reload
}
