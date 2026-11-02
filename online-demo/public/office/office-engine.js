/* Client for the in-browser LibreOffice engine.
 *
 * LibreOffice (WebAssembly) runs in a dedicated Web Worker (`engine-worker.js`
 * under `base`), so conversions never block the page.  The page has to be
 * cross-origin isolated (see README).  Jobs are run one at a time, most
 * important first, so background work such as thumbnails never delays a
 * conversion the user asked for.
 */

import { outputName } from './file-system.js';
import { INPUT_FILTERS, TARGET_BY_ID, extensionOf } from './formats.js';

// Errors that mean the WebAssembly runtime itself is corrupted (not that the document is bad):
// once one has happened, every later job on that worker tends to fail too.
const ENGINE_FAULT = /table index is out of bounds|memory access out of bounds|unreachable|RuntimeError|Aborted|stack overflow/i;

const READY_TEXT = 'Ready. Everything stays on this device.';

export class OfficeEngine extends EventTarget {
    /**
     * @param {object} [options]
     * @param {string|URL} [options.base] Directory that serves engine-worker.js and lo/
     *   (default: the directory this module is loaded from).
     * @param {string|URL} [options.workerUrl] URL of engine-worker.js if it is not directly under `base`.
     * @param {number} [options.timeoutMs] Abort and restart the engine when one job takes longer.
     */
    constructor({ base = new URL('./', import.meta.url), workerUrl, timeoutMs = 5 * 60 * 1000 } = {}) {
        super();
        this.base = new URL(base, document.baseURI).href;
        this.workerUrl = new URL(workerUrl || 'engine-worker.js', this.base).href;
        this.timeoutMs = timeoutMs;
        this.state = 'idle';
        this.worker = null;
        this.ready = null;
        this.pending = new Map(); // running jobs by id
        this.queue = [];
        this.running = false;
        this.nextId = 1;
        this.seq = 0;
    }

    setState(state, text, detail) {
        this.state = state;
        this.dispatchEvent(new CustomEvent('state', { detail: { state, text, detail } }));
    }

    start() {
        if (this.ready) return this.ready;
        this.startedAt = performance.now();
        this.setState('loading', '');
        this.ready = new Promise((resolve, reject) => {
            this.resolveReady = resolve;
            this.rejectReady = reject;
        });
        // Avoid an unhandled rejection when nobody awaits a failed start.
        this.ready.catch(() => {});
        const worker = new Worker(this.workerUrl);
        worker.onmessage = (event) => this.onMessage(worker, event.data || {});
        worker.onerror = (event) => {
            event.preventDefault();
            this.fail(worker, event.message || 'The engine worker could not be started.');
        };
        this.worker = worker;
        return this.ready;
    }

    stop(reason, { fault = false } = {}) {
        if (this.worker) this.worker.terminate();
        this.worker = null;
        if (this.ready && this.rejectReady) this.rejectReady(new Error(reason));
        this.ready = null;
        for (const { reject, timer } of this.pending.values()) {
            clearTimeout(timer);
            reject(Object.assign(new Error(reason), { engineFault: fault }));
        }
        this.pending.clear();
    }

    fail(worker, message) {
        if (worker !== this.worker) return;
        this.stop(message, { fault: true });
        this.setState('error', 'The engine stopped.', message);
    }

    onMessage(worker, msg) {
        if (worker !== this.worker) return; // a worker that has been discarded
        switch (msg.type) {
        case 'status':
            if (this.state === 'loading') this.setState('loading', '', msg.text);
            break;
        case 'ready': {
            const secs = ((performance.now() - this.startedAt) / 1000).toFixed(1);
            this.rejectReady = null;
            this.resolveReady();
            this.setState('ready', READY_TEXT, 'Started in ' + secs + ' s.');
            break;
        }
        case 'result': {
            const job = this.pending.get(msg.id);
            if (!job) break;
            this.pending.delete(msg.id);
            clearTimeout(job.timer);
            if (msg.ok) job.resolve(msg);
            else job.reject(Object.assign(new Error(msg.error), { family: msg.family }));
            break;
        }
        case 'fatal':
            this.fail(worker, msg.message);
            break;
        }
    }

    /**
     * Converts a document.
     * @param {ArrayBuffer} data Input bytes.
     * @param {string} name Input file name (its extension helps format detection).
     * @param {object} target `{ ext, label, filters, filterOptions?, filterData? }`, see formats.js.
     * @param {{filter?: string, options?: string}} [input] Forced import filter.
     * @param {{priority?: number, quiet?: boolean}} [opts] Higher priority runs first;
     *   quiet jobs do not change the engine's 'busy' state (use for background work).
     * @returns {Promise<{data: ArrayBuffer, family: string, filter: string, ms: number}>}
     */
    convert(data, name, target, input, { priority = 0, quiet = false } = {}) {
        return new Promise((resolve, reject) => {
            this.queue.push({ data, name, target, input, quiet, priority, seq: this.seq++, resolve, reject });
            this.pump();
        });
    }

    /**
     * Converts a File/Blob (or a FileSystemFileHandle, which is only read) to one of the
     * targets in formats.js (e.g. 'pdf', 'docx', 'png').
     * Picks the import filter from the file name's extension.
     * @param {Blob|FileSystemFileHandle} file
     * @param {string} targetId Key of TARGET_BY_ID, e.g. 'pdf'.
     * @param {{name?: string, priority?: number, quiet?: boolean}} [opts]
     * @returns {Promise<{blob: Blob, filename: string, family: string, filter: string, ms: number}>}
     */
    async convertFile(file, targetId, { name, ...opts } = {}) {
        if (typeof file.getFile === 'function') file = await file.getFile();
        if (!name) name = file.name || 'document';
        const target = TARGET_BY_ID.get(targetId);
        if (!target) throw new Error('Unknown target format: ' + targetId);
        const result = await this.convert(
            await file.arrayBuffer(), name, target, INPUT_FILTERS[extensionOf(name)], opts);
        return {
            blob: new Blob([result.data], { type: target.mime || 'application/octet-stream' }),
            filename: outputName(name, target.ext),
            family: result.family,
            filter: result.filter,
            ms: result.ms,
        };
    }

    async pump() {
        if (this.running) return;
        this.running = true;
        try {
            while (this.queue.length) {
                this.queue.sort((a, b) => b.priority - a.priority || a.seq - b.seq);
                const job = this.queue.shift();
                try {
                    job.resolve(await this.runJob(job));
                } catch (e) {
                    if ((e.engineFault || ENGINE_FAULT.test(e.message)) && !job.retried) {
                        // The runtime is in a bad state: start a fresh worker and try once more.
                        console.warn('OfficeEngine: restarting after engine fault:', e.message);
                        this.stop('Engine restarted after a fault.', { fault: true });
                        job.retried = true;
                        this.queue.push(job);
                    } else {
                        if (ENGINE_FAULT.test(e.message)) this.stop(e.message, { fault: true });
                        job.reject(e);
                    }
                }
            }
        } finally {
            this.running = false;
            if (this.state === 'busy') this.setState('ready', READY_TEXT);
        }
    }

    async runJob({ data, name, target, input, quiet }) {
        await this.start();
        if (!quiet) this.setState('busy', 'Converting…');
        const id = this.nextId++;
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.stop('Conversion timed out.');
                this.setState('error', 'The engine was restarted after a timeout.');
            }, this.timeoutMs);
            this.pending.set(id, { resolve, reject, timer });
            this.worker.postMessage({
                type: 'convert',
                id,
                name,
                data,
                target: {
                    ext: target.ext,
                    label: target.label,
                    filters: target.filters,
                    filterOptions: target.filterOptions,
                    filterData: target.filterData,
                },
                inputFilter: input && input.filter,
                inputFilterOptions: input && input.options,
            }); // the buffer is copied, not transferred, so the job can be retried
        });
    }
}
