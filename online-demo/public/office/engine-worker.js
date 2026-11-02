/* Dedicated Web Worker that hosts the LibreOffice WebAssembly runtime.
 *
 * The page (OfficeEngine) sends `convert` requests via postMessage.  This worker
 * puts the input into the Emscripten file system, asks the LibreOffice thread
 * (lo-thread.js, itself a nested worker) to convert it, and sends the result
 * back.  Nothing here runs on the page's main thread.
 *
 * Files next to this script: lo-thread.js and the lo/ runtime directory.
 */
'use strict';

(function () {
    const LO_BASE = new URL('lo/', self.location.href).href;

    function post(message, transfer) {
        self.postMessage(message, transfer || []);
    }

    if (!self.crossOriginIsolated || typeof SharedArrayBuffer === 'undefined') {
        post({
            type: 'fatal',
            message: 'This page is not cross-origin isolated, so SharedArrayBuffer (needed by ' +
                'the multi-threaded LibreOffice build) is unavailable.',
        });
        return;
    }

    let threadPort = null;
    const jobs = new Map();

    self.Module = {
        uno_scripts: [new URL('lo-thread.js', self.location.href).href],
        // LibreOffice starts its own threads as workers running soffice.js; without this
        // they would load this script instead of the one that is importScripts()ed below.
        mainScriptUrlOrBlob: new Blob(["importScripts('" + new URL('soffice.js', LO_BASE) + "');"],
                                      { type: 'text/javascript' }),
        // soffice command line: no UI, no start center, no restore/lock files.
        arguments: ['--headless', '--norestore', '--nologo', '--nodefault'],
        locateFile: (path) => LO_BASE + path,
        print: (text) => console.log('[soffice]', text),
        printErr: (text) => console.warn('[soffice]', text),
        setStatus: (text) => {
            if (text) post({ type: 'status', text });
        },
        onAbort: (what) => fail('The LibreOffice engine stopped unexpectedly (' + what + ').'),
    };

    function fail(message) {
        for (const job of jobs.values()) cleanup(job);
        jobs.clear();
        post({ type: 'fatal', message });
    }

    self.addEventListener('error', (event) => {
        // Uncaught errors in the runtime (not in a conversion) are fatal.
        if (!threadPort) fail(String(event.message || event.error));
    });

    // The Emscripten file system object.  soffice.js is a classic (non-modular)
    // script, so FS is a global; don't look at Module.FS: with ASSERTIONS that
    // is a trap that aborts the runtime.
    function fs() {
        return self.FS;
    }

    function safeName(name) {
        const cleaned = name.replace(/[^A-Za-z0-9._ -]+/g, '_').replace(/^[. ]+/, '').slice(-120);
        return cleaned || 'document';
    }

    function fileUrl(path) {
        return 'file://' + path.split('/').map(encodeURIComponent).join('/');
    }

    function removeTree(path) {
        const FS = fs();
        let stat;
        try {
            stat = FS.stat(path);
        } catch (e) {
            return;
        }
        if (FS.isDir(stat.mode)) {
            for (const entry of FS.readdir(path)) {
                if (entry !== '.' && entry !== '..') removeTree(path + '/' + entry);
            }
            FS.rmdir(path);
        } else {
            FS.unlink(path);
        }
    }

    function cleanup(job) {
        try {
            removeTree(job.dir);
        } catch (e) {
            console.warn('engine: cleanup failed', e);
        }
    }

    function startJob(message) {
        const FS = fs();
        const id = message.id;
        const dir = '/tmp/convert/' + id;
        const inName = safeName(message.name);
        const dot = inName.lastIndexOf('.');
        const stem = dot > 0 ? inName.slice(0, dot) : inName;
        const inPath = dir + '/' + inName;
        const outPath = dir + '/out/' + stem + '.' + message.target.ext;
        const job = { id, dir, outPath };
        try {
            FS.mkdirTree(dir + '/out');
            FS.writeFile(inPath, new Uint8Array(message.data));
        } catch (e) {
            post({ type: 'result', id, ok: false, error: 'Could not stage the file: ' + e });
            cleanup(job);
            return;
        }
        jobs.set(id, job);
        threadPort.postMessage({
            cmd: 'convert',
            id,
            inUrl: fileUrl(inPath),
            outUrl: fileUrl(outPath),
            label: message.target.label,
            filters: message.target.filters,
            filterOptions: message.target.filterOptions,
            filterData: message.target.filterData,
            inputFilter: message.inputFilter,
            inputFilterOptions: message.inputFilterOptions,
            password: message.password,
        });
    }

    function onThreadMessage(event) {
        const message = event.data;
        if (message.cmd === 'ready') {
            post({ type: 'ready' });
            return;
        }
        if (message.cmd !== 'converted') return;
        const job = jobs.get(message.id);
        if (!job) return;
        jobs.delete(message.id);
        if (!message.ok) {
            post({ type: 'result', id: job.id, ok: false, error: message.error, family: message.family });
            cleanup(job);
            return;
        }
        let bytes;
        try {
            bytes = fs().readFile(job.outPath);
        } catch (e) {
            post({
                type: 'result', id: job.id, ok: false, family: message.family,
                error: 'LibreOffice reported success but wrote no output file.',
            });
            cleanup(job);
            return;
        }
        cleanup(job);
        // FS.readFile returns a fresh, non-shared buffer that can be transferred.
        post({
            type: 'result', id: job.id, ok: true, data: bytes.buffer,
            family: message.family, filter: message.filter, ms: message.ms,
        }, [bytes.buffer]);
    }

    self.addEventListener('message', (event) => {
        const message = event.data;
        if (message && message.type === 'convert') {
            if (!threadPort) {
                post({ type: 'result', id: message.id, ok: false, error: 'Engine is not ready yet.' });
                return;
            }
            startJob(message);
        }
    });

    try {
        importScripts(LO_BASE + 'soffice.js');
    } catch (e) {
        fail('Could not load ' + LO_BASE + 'soffice.js: ' + e);
        return;
    }
    Module.uno_main.then((port) => {
        threadPort = port;
        threadPort.onmessage = onThreadMessage;
    });
})();
