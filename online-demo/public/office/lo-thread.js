/* Runs inside the LibreOffice main thread (an Emscripten pthread Web Worker).
 *
 * soffice.js loads this file through Module.uno_scripts.  Once the UNO
 * runtime is up, it talks to the page-side engine (engine.js) over
 * Module.uno_mainPort and performs conversions with the UNO API:
 * XComponentLoader.loadComponentFromURL + XStorable.storeToURL.
 *
 * Files are exchanged through the Emscripten in-memory file system, which
 * lives on the main runtime thread; the file I/O LibreOffice does from here is
 * proxied there by Emscripten.
 */
'use strict';

Module.uno_init.then(function () {
    const uno = Module.uno;
    const css = uno.com.sun.star;
    const port = Module.uno_mainPort;

    const context = Module.getUnoComponentContext();
    const desktop = css.frame.Desktop.create(context);
    const loader = css.frame.XComponentLoader.query(desktop);

    // uno_Type handles are reusable; create them once.
    const TYPE = {
        boolean: Module.uno_Type.Boolean(),
        long: Module.uno_Type.Long(),
        double: Module.uno_Type.Double(),
        string: Module.uno_Type.String(),
        propertySeq: Module.uno_Type.Sequence(
            Module.uno_Type.Struct('com.sun.star.beans.PropertyValue')),
    };

    // Owned embind handles that need .delete() after a call returns.
    function release(list) {
        for (const obj of list) {
            try {
                if (obj && typeof obj.delete === 'function') obj.delete();
            } catch (e) {
                // Ignore double-free style errors during cleanup.
            }
        }
    }

    function anyOf(value) {
        if (value instanceof Array) {
            // A nested Sequence<PropertyValue> (FilterData and friends).
            const seq = propertySeq(value);
            const any = new Module.uno_Any(TYPE.propertySeq, seq.seq);
            return { any, owned: [...seq.owned, seq.seq] };
        }
        switch (typeof value) {
        case 'boolean':
            return { any: new Module.uno_Any(TYPE.boolean, value), owned: [] };
        case 'number':
            return Number.isInteger(value)
                ? { any: new Module.uno_Any(TYPE.long, value), owned: [] }
                : { any: new Module.uno_Any(TYPE.double, value), owned: [] };
        default:
            return { any: new Module.uno_Any(TYPE.string, String(value)), owned: [] };
        }
    }

    // [[name, value], ...] -> Sequence<com.sun.star.beans.PropertyValue>
    function propertySeq(pairs) {
        const owned = [];
        const structs = pairs.map(([name, value]) => {
            const { any, owned: inner } = anyOf(value);
            owned.push(any, ...inner);
            return {
                Name: name,
                Handle: 0,
                Value: any,
                State: css.beans.PropertyState.DIRECT_VALUE,
            };
        });
        const seq = new Module.uno_Sequence_com$sun$star$beans$PropertyValue(structs);
        return { seq, owned };
    }

    function describeError(e) {
        try {
            const any = Module.catchUnoException(e);
            try {
                const exc = any.get();
                const type = any.getType().toString();
                return type + (exc && exc.Message ? ': ' + exc.Message : '');
            } finally {
                any.delete();
            }
        } catch (notUno) {
            return String(notUno && notUno.message ? notUno.message : notUno);
        }
    }

    function supports(xComponent, service) {
        const info = css.lang.XServiceInfo.query(xComponent);
        if (!info) return false;
        try {
            return info.supportsService(service);
        } finally {
            info.delete();
        }
    }

    function documentFamily(xComponent) {
        if (supports(xComponent, 'com.sun.star.text.WebDocument')) return 'web';
        if (supports(xComponent, 'com.sun.star.text.TextDocument')) return 'writer';
        if (supports(xComponent, 'com.sun.star.sheet.SpreadsheetDocument')) return 'calc';
        if (supports(xComponent, 'com.sun.star.presentation.PresentationDocument')) return 'impress';
        if (supports(xComponent, 'com.sun.star.drawing.DrawingDocument')) return 'draw';
        if (supports(xComponent, 'com.sun.star.formula.FormulaProperties')) return 'math';
        return 'unknown';
    }

    function closeDocument(xComponent) {
        const closeable = css.util.XCloseable.query(xComponent);
        if (closeable) {
            try {
                closeable.close(true);
            } catch (e) {
                // Fall back to dispose() below.
            } finally {
                closeable.delete();
            }
            return;
        }
        xComponent.dispose();
    }

    // Filter options may be given per document family: { writer: '...', calc: '...' }.
    function perFamily(value, family) {
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            return value[family] !== undefined ? value[family] : value.default;
        }
        return value;
    }

    function convert(job) {
        const garbage = [];
        let doc = null;
        try {
            const loadProps = [
                ['Hidden', true],
                ['ReadOnly', false],
                ['UpdateDocMode', 0], // NO_UPDATE: never touch linked content
                ['MacroExecutionMode', 0], // NEVER_EXECUTE
            ];
            if (job.inputFilter) loadProps.push(['FilterName', job.inputFilter]);
            if (job.inputFilterOptions) loadProps.push(['FilterOptions', job.inputFilterOptions]);
            if (job.password) loadProps.push(['Password', job.password]);
            const load = propertySeq(loadProps);
            garbage.push(load.seq, ...load.owned);

            try {
                doc = loader.loadComponentFromURL(job.inUrl, '_blank', 0, load.seq);
            } catch (e) {
                throw new Error('Could not open the document (' + describeError(e) + ')');
            }
            if (!doc) {
                throw new Error('LibreOffice could not open this file (unsupported or damaged format).');
            }

            const family = documentFamily(doc);
            const filter = job.filters[family] || (family === 'web' ? job.filters.writer : undefined);
            if (!filter) {
                const err = new Error('A ' + family + ' document cannot be exported as ' +
                                      job.label + '.');
                err.family = family;
                throw err;
            }

            const storeProps = [['FilterName', filter], ['Overwrite', true]];
            const filterOptions = perFamily(job.filterOptions, family);
            const filterData = perFamily(job.filterData, family);
            if (filterOptions) storeProps.push(['FilterOptions', filterOptions]);
            if (filterData && filterData.length) storeProps.push(['FilterData', filterData]);
            const store = propertySeq(storeProps);
            garbage.push(store.seq, ...store.owned);

            const storable = css.frame.XStorable.query(doc);
            garbage.push(storable);
            try {
                storable.storeToURL(job.outUrl, store.seq);
            } catch (e) {
                const err = new Error('Export failed (' + describeError(e) + ')');
                err.family = family;
                throw err;
            }
            return { family, filter };
        } finally {
            if (doc) {
                try {
                    closeDocument(doc);
                } catch (e) {
                    console.warn('lo-thread: closing document failed', e);
                }
                garbage.push(doc);
            }
            release(garbage);
        }
    }

    port.onmessage = function (event) {
        const msg = event.data;
        if (!msg || msg.cmd !== 'convert') return;
        const started = performance.now();
        try {
            const result = convert(msg);
            port.postMessage({
                cmd: 'converted', id: msg.id, ok: true,
                family: result.family, filter: result.filter,
                ms: Math.round(performance.now() - started),
            });
        } catch (e) {
            port.postMessage({
                cmd: 'converted', id: msg.id, ok: false,
                family: e && e.family, error: e && e.message ? e.message : String(e),
                ms: Math.round(performance.now() - started),
            });
        }
    };

    port.postMessage({ cmd: 'ready' });
});
