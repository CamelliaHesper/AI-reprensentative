"""独立的本机 Word 转换进程，不执行宏，不更新外部链接。"""
import sys
from pathlib import Path
CURRENT_STEP='initialize'


def normalized_text(text):
    import unicodedata
    return ''.join(unicodedata.normalize('NFKC',text).replace('\x07','').replace('\x00','').split())


def locate_change_range(doc,item):
    if item.get('kind')!='body_paragraph':
        return doc.Tables.Item(item['table']).Range.Cells.Item(item['cell']).Range.Paragraphs.Item(item['paragraph']).Range.Duplicate
    paragraphs=[]
    for i in range(1,doc.Paragraphs.Count+1):
        rng=doc.Paragraphs.Item(i).Range
        if not rng.Information(12):paragraphs.append(rng)
    position=item['body_paragraph']-1
    if 0<=position<len(paragraphs) and normalized_text(paragraphs[position].Text)==normalized_text(item['expected']):
        return paragraphs[position].Duplicate
    # Conversion may insert empty paragraphs. Use only an unambiguous exact-text match.
    matches=[rng for rng in paragraphs if normalized_text(rng.Text)==normalized_text(item['expected'])]
    if len(matches)!=1:raise ValueError('body paragraph cannot be uniquely located')
    return matches[0].Duplicate


def run(source,destination,changes=None):
    global CURRENT_STEP
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    app=None; doc=None
    try:
        program='Word.Application'
        if source.lower().endswith('.doc'):
            import olefile
            with olefile.OleFileIO(source) as ole:
                producer=ole.getproperties('\x05SummaryInformation').get(18,b'') if ole.exists('\x05SummaryInformation') else b''
                if b'WPS' in (producer if isinstance(producer,bytes) else str(producer).encode()):program='kwps.Application'
        else:
            import zipfile
            if zipfile.is_zipfile(source):
                with zipfile.ZipFile(source) as archive:
                    if 'docProps/app.xml' in archive.namelist() and archive.getinfo('docProps/app.xml').file_size<65536:
                        if b'WPS' in archive.read('docProps/app.xml'):program='kwps.Application'
        app=win32com.client.DispatchEx(program)
        app.Visible=False;app.DisplayAlerts=0;app.AutomationSecurity=3
        app.Options.UpdateLinksAtOpen=False
        open_source=source
        CURRENT_STEP='open'
        doc=app.Documents.Open(str(Path(open_source).resolve()),ConfirmConversions=False,ReadOnly=True,
            AddToRecentFiles=False,Visible=False,OpenAndRepair=False,NoEncodingDialog=True)
        if changes:
            import json
            for item in json.loads(Path(changes).read_text(encoding='utf-8')):
                CURRENT_STEP='locate-cell'
                rng=locate_change_range(doc,item)
                raw=rng.Text;existing=raw.rstrip('\r\x07').replace('\x0b','\n')
                if normalized_text(existing)!=normalized_text(item['expected']):
                    CURRENT_STEP='template-text-mismatch'
                    raise ValueError('template field mismatch')
                if (existing==item['value'] or item['value']==item['expected']) and not item.get('normalize'):continue
                CURRENT_STEP='replace-text'
                rng.End=rng.Start+len(raw.rstrip('\r\x07').encode('utf-16le'))//2
                rng.Text=item['value'].replace('\n','\x0b')
                if item.get('normalize'):
                    rng.Font.Name='宋体';rng.Font.NameFarEast='宋体';rng.Font.Size=10.5;rng.Font.Color=0;rng.Font.Bold=0;rng.Font.Italic=0
        if destination.lower().endswith('.pdf'):
            doc.Repaginate()
            doc.ExportAsFixedFormat(str(Path(destination).resolve()),17)
        elif destination.lower().endswith('.doc'):
            CURRENT_STEP='save-doc'
            doc.SaveAs2(str(Path(destination).resolve()),FileFormat=0,AddToRecentFiles=False,
                CompatibilityMode=int(doc.CompatibilityMode))
        else:
            doc.SaveAs2(str(Path(destination).resolve()),FileFormat=12,AddToRecentFiles=False,
                CompatibilityMode=int(doc.CompatibilityMode))
    finally:
        if doc is not None: doc.Close(False)
        if app is not None: app.Quit(False)
        pythoncom.CoUninitialize()


if __name__=='__main__':
    try:run(sys.argv[1],sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None)
    except Exception as exc:
        print(type(exc).__name__+':'+CURRENT_STEP+':'+str(getattr(exc,'hresult','')),file=sys.stderr);sys.exit(1)
