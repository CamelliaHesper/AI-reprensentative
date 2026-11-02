/* File System Access API helpers: open files and whole folders (keeping their handles),
 * and save results as new files.
 *
 * Nothing here ever writes to a file it was given as input, or replaces any existing file:
 *
 *   - writeNewFile() only creates files.  When the name is taken (by the original, an earlier
 *     result or anything else), it uses "name (1).ext", "name (2).ext", ... instead.
 *   - saveFileAs() lets the user pick the name in the browser's save dialog.  If they pick one
 *     of the originals anyway, its content is put back and the save is refused (the browser
 *     empties a file as soon as it is picked in a save dialog, so the original's bytes must be
 *     passed in `protect`).
 *
 * Every function falls back to plain <input type=file> / webkitGetAsEntry() behaviour where the
 * File System Access API is missing (Firefox, Safari): the entries then have no `handle`.
 */

import { INPUT_FAMILIES, guessFamily } from './formats.js';

const g = globalThis;

/** `showOpenFilePicker()` is available. */
export const canPickFiles = typeof g.showOpenFilePicker === 'function';
/** `showDirectoryPicker()` is available (also needed to save into a folder). */
export const canPickDirectory = typeof g.showDirectoryPicker === 'function';
/** `showSaveFilePicker()` is available. */
export const canPickSaveFile = typeof g.showSaveFilePicker === 'function';
/** `FileSystemObserver` is available (watchDirectory() polls without it). */
export const canObserve = typeof g.FileSystemObserver === 'function';

// One picker type per family, with a real MIME type each (the browser rejects made-up ones);
// the extensions are what actually filters the dialog.
const FAMILY_MIME = {
    writer: 'application/vnd.oasis.opendocument.text',
    calc: 'application/vnd.oasis.opendocument.spreadsheet',
    impress: 'application/vnd.oasis.opendocument.presentation',
    draw: 'application/vnd.oasis.opendocument.graphics',
};

/** `types` for showOpenFilePicker(): every input format the converter knows. */
export const PICKER_TYPES = [{
    description: 'Office documents and images',
    accept: Object.fromEntries(Object.entries(INPUT_FAMILIES).map(
        ([family, exts]) => [FAMILY_MIME[family], exts.map((e) => '.' + e)])),
}];

/**
 * A file to convert.
 * @typedef {object} FileEntry
 * @property {File} file
 * @property {FileSystemFileHandle} [handle] Where it came from, if the browser told us.
 * @property {string} path Relative path, e.g. "Reports/2025/q1.docx" for a file of a dropped
 *   or picked folder "Reports"; just the name for single files.
 * @property {FileSystemDirectoryHandle} [root] The dropped or picked folder (first segment of
 *   `path`) the file is in.
 */

/** Files the converter can open, judged by extension; skips hidden files and Office lock files. */
export function isConvertible(name) {
    return !name.startsWith('.') && !name.startsWith('~$') && guessFamily(name) !== 'unknown';
}

function isAbort(e) {
    return e && e.name === 'AbortError';
}

/**
 * Shows the browser's open dialog.  Resolves with [] when the user cancels.
 * @param {{multiple?: boolean, id?: string, startIn?: FileSystemHandle|string, types?: object[]}} [opts]
 * @returns {Promise<FileEntry[]>}
 */
export async function openFiles({ multiple = true, id = 'office-converter-open', startIn, types = PICKER_TYPES } = {}) {
    let handles;
    try {
        handles = await g.showOpenFilePicker({ multiple, id, startIn, types });
    } catch (e) {
        if (isAbort(e)) return [];
        throw e;
    }
    return Promise.all(handles.map(async (handle) => ({ file: await handle.getFile(), handle, path: handle.name })));
}

/**
 * Shows the browser's folder dialog and lists the convertible files in it (and its subfolders).
 * Resolves with [] when the user cancels.
 * @param {{id?: string, startIn?: FileSystemHandle|string, filter?: (name: string) => boolean, recursive?: boolean}} [opts]
 * @returns {Promise<FileEntry[]>}
 */
export async function openDirectory({ id = 'office-converter-open', startIn, filter = isConvertible, recursive = true } = {}) {
    let dir;
    try {
        dir = await g.showDirectoryPicker({ id, startIn, mode: 'read' });
    } catch (e) {
        if (isAbort(e)) return [];
        throw e;
    }
    return listDirectory(dir, { filter, recursive });
}

/**
 * Lists the files of a directory handle, sorted by path.  Hidden folders are skipped.
 * @param {FileSystemDirectoryHandle} dir
 * @param {{filter?: (name: string) => boolean, recursive?: boolean, root?: FileSystemDirectoryHandle, prefix?: string}} [opts]
 * @returns {Promise<FileEntry[]>}
 */
export async function listDirectory(dir, { filter = isConvertible, recursive = true, root = dir, prefix = dir.name + '/' } = {}) {
    const out = [];
    for await (const handle of dir.values()) {
        if (handle.kind === 'file') {
            if (filter(handle.name)) out.push({ file: await handle.getFile(), handle, path: prefix + handle.name, root });
        } else if (recursive && !handle.name.startsWith('.')) {
            out.push(...await listDirectory(handle, { filter, recursive, root, prefix: prefix + handle.name + '/' }));
        }
    }
    return out.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }));
}

// webkitGetAsEntry() fallback (Firefox, Safari, and Chrome on insecure origins).
async function listEntry(entry, filter, prefix) {
    if (entry.isFile) {
        const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
        return [{ file, path: prefix + entry.name }];
    }
    const reader = entry.createReader();
    const children = [];
    // readEntries() returns at most 100 entries per call.
    for (let batch; (batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))).length;) {
        children.push(...batch);
    }
    const out = [];
    for (const child of children) {
        if (child.isFile ? filter(child.name) : !child.name.startsWith('.')) {
            out.push(...await listEntry(child, filter, prefix + entry.name + '/'));
        }
    }
    return out.sort((a, b) => a.path.localeCompare(b.path, undefined, { numeric: true }));
}

/**
 * The files of a drop, with folders expanded.  Dropped files are all kept (the converter says
 * if it can't open one); files inside dropped folders are filtered with `filter`.
 * Call it synchronously from the drop handler: the DataTransfer is emptied afterwards.
 * With `handles: false` the File System Access API is not used (no handles, plain files only).
 * @param {DataTransfer} dataTransfer
 * @param {{filter?: (name: string) => boolean, handles?: boolean}} [opts]
 * @returns {Promise<FileEntry[]>}
 */
export function entriesFromDataTransfer(dataTransfer, { filter = isConvertible, handles = true } = {}) {
    // Everything has to be requested before the first await.
    const pending = [...dataTransfer.items].filter((item) => item.kind === 'file').map((item) => ({
        handle: handles && typeof item.getAsFileSystemHandle === 'function' ? item.getAsFileSystemHandle() : null,
        entry: typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null,
        file: item.getAsFile(),
    }));
    if (!pending.length) {
        return Promise.resolve([...dataTransfer.files].map((file) => ({ file, path: file.name })));
    }
    return Promise.all(pending.map(async ({ handle, entry, file }) => {
        const h = handle && await handle.catch(() => null);
        if (h && h.kind === 'directory') return listDirectory(h, { filter });
        if (h && h.kind === 'file') return [{ file: file || await h.getFile(), handle: h, path: h.name }];
        if (entry && entry.isDirectory) return listEntry(entry, filter, '');
        return file ? [{ file, path: file.name }] : [];
    })).then((lists) => lists.flat());
}

/**
 * Entries for files picked with <input type=file webkitdirectory> (or a plain one).
 * @param {FileList|File[]} files
 * @param {{filter?: (name: string) => boolean}} [opts]
 * @returns {FileEntry[]}
 */
export function entriesFromInput(files, { filter = isConvertible } = {}) {
    return [...files]
        .filter((f) => !f.webkitRelativePath || filter(f.name))
        .map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

/**
 * Asks for write access to a handle if it is not granted yet (needs a user gesture).
 * @returns {Promise<boolean>}
 */
export async function ensureWritable(handle) {
    const opts = { mode: 'readwrite' };
    if (typeof handle.queryPermission !== 'function') return true; // e.g. the origin private file system
    if (await handle.queryPermission(opts) === 'granted') return true;
    return await handle.requestPermission(opts) === 'granted';
}

/**
 * Shows the browser's folder dialog to choose where to save results (with write access).
 * Resolves with null when the user cancels.
 * @param {{id?: string, startIn?: FileSystemHandle|string}} [opts]
 * @returns {Promise<FileSystemDirectoryHandle|null>}
 */
export async function pickOutputDirectory({ id = 'office-converter-save', startIn } = {}) {
    try {
        return await g.showDirectoryPicker({ id, startIn, mode: 'readwrite' });
    } catch (e) {
        if (isAbort(e)) return null;
        throw e;
    }
}

/** "report.pdf" -> ["report", ".pdf"]; "archive.tar.gz" -> ["archive.tar", ".gz"]. */
function splitName(name) {
    const dot = name.lastIndexOf('.');
    return dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ''];
}

async function exists(dir, name) {
    try {
        await dir.getFileHandle(name);
        return true;
    } catch (e) {
        if (e.name === 'NotFoundError') return false;
        return true; // TypeMismatchError: a folder of that name; anything else: don't risk it
    }
}

/**
 * The first of "name.ext", "name (1).ext", "name (2).ext", ... that does not exist in `dir`
 * (case-insensitively, since most desktop file systems are) and is not in `taken`.
 * @param {FileSystemDirectoryHandle} dir
 * @param {string} name
 * @param {Iterable<string>} [taken]
 */
export async function availableName(dir, name, taken = []) {
    const [stem, ext] = splitName(name);
    const names = new Set();
    for await (const key of dir.keys()) names.add(key.toLowerCase());
    for (const t of taken) names.add(t.toLowerCase());
    for (let i = 0; ; i++) {
        const candidate = i ? `${stem} (${i})${ext}` : name;
        if (!names.has(candidate.toLowerCase()) && !await exists(dir, candidate)) return candidate;
    }
}

// All writes go one after another: handles to the same folder are different objects, so
// there is no cheaper way to make sure two writes never settle on the same free name.
let writeQueue = Promise.resolve();

/**
 * Writes `data` to a **new** file `relPath` under `dir`, creating subfolders as needed.  Never
 * replaces an existing file: if the name is taken, " (1)", " (2)", ... is added before the
 * extension.  Calls are serialised, so concurrent ones cannot pick the same name (the API
 * has no exclusive create, so only another program creating that very name in between could).
 * @param {FileSystemDirectoryHandle} dir
 * @param {string} relPath e.g. "report.pdf" or "Reports/2025/q1.pdf"
 * @param {Blob|BufferSource|string} data
 * @returns {Promise<{handle: FileSystemFileHandle, path: string}>} `path` is the name actually used.
 */
export async function writeNewFile(dir, relPath, data) {
    const parts = relPath.split('/').filter((p) => p && p !== '.' && p !== '..');
    if (!parts.length) throw new TypeError('writeNewFile: empty path');
    const name = parts.pop();
    const job = writeQueue.catch(() => {}).then(async () => {
        let parent = dir;
        for (const part of parts) parent = await parent.getDirectoryHandle(part, { create: true });
        const finalName = await availableName(parent, name);
        const handle = await parent.getFileHandle(finalName, { create: true });
        let writable = null;
        try {
            writable = await handle.createWritable({ keepExistingData: false });
            await writable.write(data);
            await writable.close();
        } catch (e) {
            // Don't leave an empty file behind (the file is new, so this removes nothing else).
            if (writable) await writable.abort().catch(() => {});
            await parent.removeEntry(finalName).catch(() => {});
            throw e;
        }
        return { handle, path: [...parts, finalName].join('/') };
    });
    writeQueue = job;
    return job;
}

/** Thrown by saveFileAs() when the user picked one of the protected originals. */
export class SourceFileError extends Error {
    constructor(name, restored) {
        super(restored
            ? `"${name}" is an original file: it was left as it was. Choose another name.`
            : `"${name}" is an original file. Choose another name.`);
        this.name = 'SourceFileError';
        this.restored = restored;
    }
}

/**
 * Lets the user choose where to save `blob` in the browser's save dialog, and writes it there.
 * Resolves with null when the user cancels.
 *
 * Originals in `protect` are never replaced: picking one of them puts its content back and
 * rejects with a SourceFileError.  Pass each original's bytes (or a promise of them, started
 * before calling this), because the browser empties the picked file before we get to see it.
 *
 * @param {Blob} blob
 * @param {string} suggestedName
 * @param {{id?: string, startIn?: FileSystemHandle|string, types?: object[],
 *          protect?: {handle: FileSystemFileHandle, data?: Blob|BufferSource|Promise<Blob|BufferSource|null>}[]}} [opts]
 * @returns {Promise<FileSystemFileHandle|null>}
 */
export async function saveFileAs(blob, suggestedName, { id = 'office-converter-save', startIn, types, protect = [] } = {}) {
    const ext = splitName(suggestedName)[1];
    if (!types && ext && blob.type) types = [{ description: ext.slice(1).toUpperCase(), accept: { [blob.type]: [ext] } }];
    let handle;
    try {
        handle = await g.showSaveFilePicker({ id, startIn, suggestedName, types });
    } catch (e) {
        if (isAbort(e)) return null;
        throw e;
    }
    for (const original of protect) {
        if (!original.handle || !await handle.isSameEntry(original.handle)) continue;
        const data = await Promise.resolve(original.data).catch(() => null);
        let restored = false;
        if (data != null) {
            const writable = await handle.createWritable({ keepExistingData: false });
            await writable.write(data);
            await writable.close();
            restored = true;
        }
        throw new SourceFileError(handle.name, restored);
    }
    const writable = await handle.createWritable({ keepExistingData: false });
    try {
        await writable.write(blob);
        await writable.close();
    } catch (e) {
        await writable.abort().catch(() => {});
        throw e;
    }
    return handle;
}

/**
 * The name for a converted file: the input's name with the target's extension, or
 * "name (converted).ext" when that would be the input's own name (e.g. DOCX to DOCX).
 * @param {string} inputName
 * @param {string} ext Target extension without the dot.
 */
export function outputName(inputName, ext) {
    const [stem, inExt] = splitName(inputName);
    if (inExt.slice(1).toLowerCase() === ext.toLowerCase()) return `${stem} (converted).${ext}`;
    return `${stem}.${ext}`;
}

/** Same as outputName(), keeping the folders of a relative path ("a/b.docx" -> "a/b.pdf"). */
export function outputPath(inputPath, ext) {
    const slash = inputPath.lastIndexOf('/');
    return inputPath.slice(0, slash + 1) + outputName(inputPath.slice(slash + 1), ext);
}

/**
 * Where `handle` is inside `dir`, as a relative path ("sub/report.docx"), or null when it is
 * not inside it.
 * @param {FileSystemDirectoryHandle} dir
 * @param {FileSystemHandle} handle
 * @returns {Promise<string|null>}
 */
export async function relativePath(dir, handle) {
    const parts = await dir.resolve(handle).catch(() => null);
    return parts && parts.length ? parts.join('/') : null;
}

/**
 * Watches a folder (and its subfolders) for files that appear or change, with
 * FileSystemObserver where available and by polling otherwise.  Changes are collected for
 * `debounceMs`, then the folder is listed again and `onChange` receives the entries (as from
 * listDirectory()) that are new or have a new `lastModified`.  Files for which `ignore(path)`
 * is true (e.g. results written there by the app itself) are left out.
 * @param {FileSystemDirectoryHandle} dir
 * @param {(entries: FileEntry[]) => void} onChange
 * @param {{filter?: (name: string) => boolean, ignore?: (path: string) => boolean,
 *          debounceMs?: number, pollMs?: number, observe?: boolean, onError?: (e: Error) => void}} [opts]
 * @returns {Promise<{stop: () => void, observed: boolean}>} `observed`: false when polling.
 */
export async function watchDirectory(dir, onChange, {
    filter = isConvertible, ignore = () => false, debounceMs = 800, pollMs = 3000,
    observe = canObserve, onError = (e) => console.warn('watchDirectory:', e),
} = {}) {
    const seen = new Map();
    for (const e of await listDirectory(dir, { filter })) seen.set(e.path, e.file.lastModified);
    let stopped = false;
    let timer = null;
    let scanning = null;

    async function scan() {
        const changed = [];
        for (const e of await listDirectory(dir, { filter })) {
            if (seen.get(e.path) === e.file.lastModified) continue;
            seen.set(e.path, e.file.lastModified);
            if (!ignore(e.path)) changed.push(e);
        }
        if (changed.length && !stopped) onChange(changed);
    }
    function schedule() {
        if (stopped) return;
        clearTimeout(timer);
        timer = setTimeout(() => {
            // One scan at a time; changes during a scan schedule another one.
            scanning = (scanning || Promise.resolve()).then(scan).catch(onError);
        }, debounceMs);
    }

    let observer = null;
    let poller = null;
    if (observe) {
        try {
            observer = new g.FileSystemObserver((records) => {
                if (records.some((r) => r.type !== 'disappeared')) schedule();
            });
            await observer.observe(dir, { recursive: true });
        } catch (e) {
            onError(e);
            observer = null;
        }
    }
    if (!observer) poller = setInterval(schedule, pollMs);
    return {
        observed: !!observer,
        stop() {
            stopped = true;
            clearTimeout(timer);
            clearInterval(poller);
            if (observer) observer.disconnect();
        },
    };
}

// Handles can be stored in IndexedDB (and nowhere else): they keep working after a reload,
// although the browser may ask for permission again (ensureWritable()).
const DB_NAME = 'office-converter-handles';

function withStore(mode, fn) {
    return new Promise((resolve, reject) => {
        const open = indexedDB.open(DB_NAME, 1);
        open.onupgradeneeded = () => open.result.createObjectStore('handles');
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
            const db = open.result;
            const tx = db.transaction('handles', mode);
            const request = fn(tx.objectStore('handles'));
            tx.oncomplete = () => { db.close(); resolve(request.result); };
            tx.onerror = tx.onabort = () => { db.close(); reject(tx.error); };
        };
    });
}

/** Stores a handle under `key` (in IndexedDB), or removes it when `handle` is null. */
export function rememberHandle(key, handle) {
    return withStore('readwrite', (store) => (handle ? store.put(handle, key) : store.delete(key)));
}

/** The handle stored under `key`, or null. */
export async function recallHandle(key) {
    return (await withStore('readonly', (store) => store.get(key)).catch(() => null)) || null;
}
