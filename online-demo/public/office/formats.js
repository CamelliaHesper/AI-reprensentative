// Format tables for the converter UI.
//
// LibreOffice picks the export filter by document *family* (Writer, Calc,
// Impress, Draw), which is only known for sure once the document is loaded.
// So every target lists one filter per family it supports; lo-thread.js picks
// the right one after loading.

// Extension -> family LibreOffice will most likely open the file as.  Only used
// to pre-select sensible targets in the UI; the real family is detected after
// loading.
export const INPUT_FAMILIES = {
    writer: [
        'odt', 'ott', 'fodt', 'sxw', 'stw', 'doc', 'dot', 'docx', 'docm', 'dotx', 'dotm',
        'rtf', 'txt', 'html', 'htm', 'xhtml', 'md', 'markdown', 'hwp', 'xml',
    ],
    calc: [
        'ods', 'ots', 'fods', 'sxc', 'stc', 'xls', 'xlt', 'xlsx', 'xlsm', 'xltx', 'xltm', 'xlsb',
        'csv', 'tsv', 'tab', 'wk1', 'wks', '123', 'dif', 'slk', 'sylk', 'wb2', 'uos',
    ],
    impress: [
        'odp', 'otp', 'fodp', 'sxi', 'sti', 'ppt', 'pps', 'pot', 'pptx', 'pptm', 'ppsx', 'ppsm',
        'potx', 'potm',
    ],
    draw: [
        'odg', 'otg', 'fodg', 'sxd', 'std', 'svg', 'svgz', 'wmf', 'emf', 'png', 'jpg', 'jpeg',
        'gif', 'bmp', 'tif', 'tiff', 'webp', 'eps', 'pct', 'pict', 'pcx', 'ppm', 'pgm', 'pbm',
        'ras', 'tga', 'xbm', 'xpm', 'met', 'cgm',
    ],
};

const EXT_TO_FAMILY = new Map();
for (const [family, exts] of Object.entries(INPUT_FAMILIES)) {
    for (const ext of exts) EXT_TO_FAMILY.set(ext, family);
}

export function extensionOf(name) {
    const dot = name.lastIndexOf('.');
    return dot > 0 ? name.slice(dot + 1).toLowerCase() : '';
}

export function guessFamily(name) {
    return EXT_TO_FAMILY.get(extensionOf(name)) || 'unknown';
}

export const ACCEPT = Object.values(INPUT_FAMILIES).flat().map((e) => '.' + e).join(',');

// Import filters forced for some extensions, where LibreOffice's type
// detection would otherwise pick something less useful (e.g. Writer/Web for
// HTML, which has very few export filters, or a CSV import in the system
// encoding).
export const INPUT_FILTERS = {
    html: { filter: 'HTML (StarWriter)' },
    htm: { filter: 'HTML (StarWriter)' },
    xhtml: { filter: 'HTML (StarWriter)' },
    txt: { filter: 'Text (encoded)', options: 'UTF8' },
    md: { filter: 'Markdown' },
    markdown: { filter: 'Markdown' },
    // Separator 44 (,) / 9 (tab), text delimiter 34 ("), charset 76 (UTF-8), start at row 1.
    csv: { filter: 'Text - txt - csv (StarCalc)', options: '44,34,76,1' },
    tsv: { filter: 'Text - txt - csv (StarCalc)', options: '9,34,76,1' },
    tab: { filter: 'Text - txt - csv (StarCalc)', options: '9,34,76,1' },
};

// Conversion targets.  `filters` maps the loaded document's family to the
// LibreOffice export filter name.
export const TARGETS = [
    {
        id: 'pdf', label: 'PDF', ext: 'pdf', mime: 'application/pdf',
        filters: {
            writer: 'writer_pdf_Export', web: 'writer_web_pdf_Export', calc: 'calc_pdf_Export',
            impress: 'impress_pdf_Export', draw: 'draw_pdf_Export',
        },
    },
    {
        id: 'pdfa', label: 'PDF/A-2b (archival)', ext: 'pdf', mime: 'application/pdf',
        filters: {
            writer: 'writer_pdf_Export', web: 'writer_web_pdf_Export', calc: 'calc_pdf_Export',
            impress: 'impress_pdf_Export', draw: 'draw_pdf_Export',
        },
        filterData: [['SelectPdfVersion', 2]],
    },
    {
        id: 'docx', label: 'Word (DOCX)', ext: 'docx',
        mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        filters: { writer: 'MS Word 2007 XML' },
    },
    {
        id: 'doc', label: 'Word 97–2003 (DOC)', ext: 'doc', mime: 'application/msword',
        filters: { writer: 'MS Word 97' },
    },
    {
        id: 'odt', label: 'OpenDocument Text (ODT)', ext: 'odt',
        mime: 'application/vnd.oasis.opendocument.text',
        filters: { writer: 'writer8' },
    },
    {
        id: 'rtf', label: 'Rich Text (RTF)', ext: 'rtf', mime: 'application/rtf',
        filters: { writer: 'Rich Text Format' },
    },
    {
        id: 'txt', label: 'Plain text (TXT)', ext: 'txt', mime: 'text/plain',
        filters: { writer: 'Text (encoded)' },
        filterOptions: 'UTF8,LF',
    },
    {
        id: 'md', label: 'Markdown (MD)', ext: 'md', mime: 'text/markdown',
        filters: { writer: 'Markdown' },
    },
    {
        // Writer's HTML export needs the forms component, which the WASM build
        // leaves out (it comes with database connectivity), so spreadsheets only.
        id: 'html', label: 'HTML', ext: 'html', mime: 'text/html',
        filters: { calc: 'HTML (StarCalc)' },
    },
    {
        id: 'xlsx', label: 'Excel (XLSX)', ext: 'xlsx',
        mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        filters: { calc: 'Calc MS Excel 2007 XML' },
    },
    {
        id: 'xls', label: 'Excel 97–2003 (XLS)', ext: 'xls', mime: 'application/vnd.ms-excel',
        filters: { calc: 'MS Excel 97' },
    },
    {
        id: 'ods', label: 'OpenDocument Spreadsheet (ODS)', ext: 'ods',
        mime: 'application/vnd.oasis.opendocument.spreadsheet',
        filters: { calc: 'calc8' },
    },
    {
        id: 'csv', label: 'CSV (first sheet)', ext: 'csv', mime: 'text/csv',
        filters: { calc: 'Text - txt - csv (StarCalc)' },
        filterOptions: '44,34,76,1',
    },
    {
        id: 'pptx', label: 'PowerPoint (PPTX)', ext: 'pptx',
        mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        filters: { impress: 'Impress MS PowerPoint 2007 XML' },
    },
    {
        id: 'ppt', label: 'PowerPoint 97–2003 (PPT)', ext: 'ppt',
        mime: 'application/vnd.ms-powerpoint',
        filters: { impress: 'MS PowerPoint 97' },
    },
    {
        id: 'odp', label: 'OpenDocument Presentation (ODP)', ext: 'odp',
        mime: 'application/vnd.oasis.opendocument.presentation',
        filters: { impress: 'impress8' },
    },
    {
        id: 'odg', label: 'OpenDocument Drawing (ODG)', ext: 'odg',
        mime: 'application/vnd.oasis.opendocument.graphics',
        filters: { draw: 'draw8' },
    },
    {
        // Writer's and Calc's SVG "filters" are internal only and fail to export.
        id: 'svg', label: 'SVG (first slide)', ext: 'svg', mime: 'image/svg+xml',
        filters: { impress: 'impress_svg_Export', draw: 'draw_svg_Export' },
    },
    {
        id: 'png', label: 'PNG image (first page/slide)', ext: 'png', mime: 'image/png',
        filters: {
            writer: 'writer_png_Export', calc: 'calc_png_Export',
            impress: 'impress_png_Export', draw: 'draw_png_Export',
        },
    },
    {
        id: 'jpg', label: 'JPEG image (first page/slide)', ext: 'jpg', mime: 'image/jpeg',
        filters: {
            writer: 'writer_jpg_Export', calc: 'calc_jpg_Export',
            impress: 'impress_jpg_Export', draw: 'draw_jpg_Export',
        },
    },
    {
        id: 'fodt', label: 'Flat ODF Text (FODT)', ext: 'fodt',
        mime: 'application/vnd.oasis.opendocument.text-flat-xml',
        filters: { writer: 'OpenDocument Text Flat XML' },
    },
    {
        id: 'fods', label: 'Flat ODF Spreadsheet (FODS)', ext: 'fods',
        mime: 'application/vnd.oasis.opendocument.spreadsheet-flat-xml',
        filters: { calc: 'OpenDocument Spreadsheet Flat XML' },
    },
    {
        id: 'fodp', label: 'Flat ODF Presentation (FODP)', ext: 'fodp',
        mime: 'application/vnd.oasis.opendocument.presentation-flat-xml',
        filters: { impress: 'OpenDocument Presentation Flat XML' },
    },
];

export const TARGET_BY_ID = new Map(TARGETS.map((t) => [t.id, t]));

export function targetsForFamily(family) {
    if (family === 'unknown') return TARGETS;
    return TARGETS.filter((t) => t.filters[family]);
}

export const FAMILY_LABELS = {
    writer: 'Text document',
    web: 'Web page',
    calc: 'Spreadsheet',
    impress: 'Presentation',
    draw: 'Drawing / image',
    math: 'Formula',
    unknown: 'Unknown',
};
