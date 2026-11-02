import json
import uuid
import zipfile
from pathlib import Path
from .parsers import parse_file,_archive_check,SUPPORTED


def list_documents(store,tid):
    with store.connect() as c:
        rows=[dict(x) for x in c.execute('SELECT * FROM documents WHERE task_id=? ORDER BY rowid',(tid,))]
    for r in rows:r['result']=json.loads(r['result'])
    return rows


def ingest_documents(store,tid,cfg,force=False):
    root=store.root/'documents';root.mkdir(exist_ok=True)
    for f in store.get(tid)['files']:
        existing={d['name']:d for d in list_documents(store,tid) if d['source_id']==f['id']}
        def add(name,content):
            if name in existing:
                if force:
                    d=existing[name];result=parse_file(d['path'],name,cfg)
                    with store.connect() as c:c.execute('UPDATE documents SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),d['id']))
                return
            did=uuid.uuid4().hex;path=root/did;path.write_bytes(content)
            result=parse_file(path,name,cfg)
            with store.connect() as c:
                c.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?)',
                    (did,tid,f['id'],name,str(path),json.dumps(result,ensure_ascii=False)))
        try:
            if Path(f['name']).suffix.lower()=='.zip':
                with zipfile.ZipFile(f['path']) as z:
                    _archive_check(z,cfg)
                    for entry in z.infolist():
                        if entry.is_dir():continue
                        if entry.file_size>cfg.max_file_mb*1024**2:continue
                        if Path(entry.filename).suffix.lower() in SUPPORTED- {'.zip'}:
                            add(entry.filename,z.read(entry))
            else:add(f['name'],Path(f['path']).read_bytes())
        except Exception as exc:
            store.file_result(f['id'],{'status':'error','text':'','warnings':[],
                'error':str(exc) if isinstance(exc,ValueError) else '附件读取失败','truncated':False})
            continue
        children=[d for d in list_documents(store,tid) if d['source_id']==f['id']]
        result={'status':'ok','text':'\n\n'.join('文档：'+d['name']+'\n'+d['result']['text'] for d in children),
            'warnings':[],'error':'','truncated':False}
        for d in children:
            result['warnings'].extend(d['name']+'：'+w for w in d['result']['warnings'])
            if d['result']['error']:result['warnings'].append(d['name']+'：'+d['result']['error'])
        store.file_result(f['id'],result)
    return list_documents(store,tid)
