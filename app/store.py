import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'files').mkdir(exist_ok=True)
        self.db = self.root / 'tasks.sqlite3'
        with self.connect() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS tasks (
              id TEXT PRIMARY KEY, message_id TEXT UNIQUE, text TEXT, source TEXT,
              metadata TEXT, status TEXT, analysis TEXT, error TEXT,
              created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS files (
              id TEXT PRIMARY KEY, task_id TEXT, name TEXT, path TEXT,
              status TEXT, result TEXT, remote_key TEXT,
              UNIQUE(task_id, remote_key));
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS message_links (
              message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_events (
              message_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,task_id TEXT,source_id TEXT,name TEXT,path TEXT,result TEXT,
              UNIQUE(task_id,source_id,name));
            CREATE TABLE IF NOT EXISTS fulfillment (task_id TEXT PRIMARY KEY,data TEXT);
            ''')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def create_task(self, message_id, text, source, metadata, status='queued'):
        tid, now = uuid.uuid4().hex, time.time()
        with self.connect() as c:
            cur = c.execute('INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)',
                (tid, message_id, text, source, json.dumps(metadata, ensure_ascii=False),
                 status, '{}', '', now, now))
            created = cur.rowcount == 1
            tid = c.execute('SELECT id FROM tasks WHERE message_id=?', (message_id,)).fetchone()['id']
        return tid, created

    def get(self, tid):
        with self.connect() as c:
            row = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if row is None:
                return None
            task = dict(row)
            task['metadata'] = json.loads(task['metadata'])
            task['analysis'] = json.loads(task['analysis'])
            task['initiator']=self.initiator(task['source'],task['metadata'])
            task['files'] = [dict(f) for f in c.execute('SELECT * FROM files WHERE task_id=? ORDER BY rowid', (tid,))]
            for f in task['files']:
                f['result'] = json.loads(f['result'])
            return task

    def listing(self, deleted=False,owner_id=None):
        with self.connect() as c:
            condition="status='deleted'" if deleted else "status!='deleted'"
            params=()
            if owner_id is not None:
                condition+=" AND ((source!='feishu' AND json_extract(metadata,'$.owner_id')=?) OR (source='feishu' AND json_extract(metadata,'$.sender_id') IN (SELECT sender_id FROM feishu_bindings WHERE account_id=?)))"
                params=(owner_id,owner_id)
            rows=[dict(r) for r in c.execute('SELECT id,text,source,status,error,created,updated,metadata FROM tasks WHERE '+condition+' ORDER BY created DESC LIMIT 200',params)]
        for row in rows:row['initiator']=self.initiator(row['source'],json.loads(row.pop('metadata')))
        return rows

    def initiator(self,source,meta):
        if source!='feishu':
            if meta.get('owner_id'):
                with self.connect() as c:
                    row=c.execute('SELECT username FROM accounts WHERE id=?',(meta['owner_id'],)).fetchone()
                if row:return row['username']
            return '示例任务' if source=='demo' else '本地用户'
        uid=meta.get('sender_id','')
        name=self.value('feishu_user_name:'+uid,'') if uid else ''
        return name or meta.get('sender_name') or ('飞书用户 · '+uid if uid else '未知发起人')

    def profile_for_task(self,task):
        owner=task['metadata'].get('owner_id')
        if task['source']=='feishu':
            with self.connect() as c:
                exists=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='feishu_bindings'").fetchone()
                row=c.execute('SELECT account_id FROM feishu_bindings WHERE sender_id=?',(task['metadata'].get('sender_id',''),)).fetchone() if exists else None
            owner=row['account_id'] if row else None
        return self.value('profile:'+owner,{}) if owner else {}

    def set_deleted(self, tid, restore=False):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT status,metadata FROM tasks WHERE id=?',(tid,)).fetchone()
            if not row: return False
            meta=json.loads(row['metadata'])
            if restore:
                if row['status']!='deleted': return False
                status=meta.pop('_deleted_previous_status','draft')
            else:
                if row['status'] in {'queued','processing','receiving','collecting_files','preparing','generating','deleted'}: return False
                meta['_deleted_previous_status']=row['status']
                status='deleted'
            c.execute('UPDATE tasks SET status=?,metadata=?,updated=? WHERE id=?',
                (status,json.dumps(meta,ensure_ascii=False),time.time(),tid))
            return True

    def update(self, tid, **values):
        allowed = {'status', 'analysis', 'error', 'metadata'}
        if not values or not set(values) <= allowed:
            raise ValueError('不支持的更新字段')
        values = {k: json.dumps(v, ensure_ascii=False) if k in {'analysis', 'metadata'} else v for k, v in values.items()}
        values['updated'] = time.time()
        with self.connect() as c:
            c.execute('UPDATE tasks SET '+','.join(f'{k}=?' for k in values)+' WHERE id=?', (*values.values(), tid))

    def claim_next(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute("UPDATE tasks SET status='processing',updated=? WHERE id=?", (time.time(), row['id']))
        return self.get(row['id'])

    def recover(self):
        with self.connect() as c:
            c.execute("UPDATE tasks SET status='queued' WHERE status IN ('processing','receiving')")
            c.execute("UPDATE tasks SET status='awaiting_confirmation' WHERE status IN ('preparing','generating')")

    def recover_collections(self):
        """仅由唯一的飞书接收进程在启动时调用，Web 不恢复此状态。"""
        with self.connect() as c:
            c.execute("UPDATE tasks SET status='collecting' WHERE status='collecting_files'")

    def finish_collection(self, tid):
        with self.connect() as c:
            c.execute("UPDATE tasks SET status='collecting',updated=? WHERE id=? AND status='collecting_files'",(time.time(),tid))

    def link_message(self, message_id, task_id):
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO message_links VALUES (?,?)', (message_id, task_id))

    def transition(self, tid, status, analysis=None):
        """与 worker 的领取互斥；返回 False 表示任务正被处理。"""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT status FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row or row['status'] in {'queued', 'processing', 'receiving', 'collecting_files', 'preparing','generating','deleted'}:
                return False
            if analysis is None:
                c.execute('UPDATE tasks SET status=?,updated=? WHERE id=?', (status,time.time(),tid))
            else:
                c.execute('UPDATE tasks SET status=?,analysis=?,updated=? WHERE id=?',
                    (status,json.dumps(analysis,ensure_ascii=False),time.time(),tid))
            return True

    def queue_task(self,tid):
        """A new parsing attempt invalidates old confirmations even if output text is identical."""
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT metadata,status FROM tasks WHERE id=?',(tid,)).fetchone()
            if not row or row['status'] in {'queued','processing','receiving','collecting_files','preparing','generating','deleted'}:return False
            form=c.execute('SELECT data FROM fulfillment WHERE task_id=?',(tid,)).fetchone()
            if form and json.loads(form['data']).get('editor',{}).get('active'):return False
            meta=json.loads(row['metadata']);meta['analysis_revision']=uuid.uuid4().hex
            c.execute('UPDATE tasks SET status=?,metadata=?,updated=? WHERE id=?',('queued',json.dumps(meta,ensure_ascii=False),time.time(),tid))
            c.execute('DELETE FROM kv WHERE key=?',('task_completion:'+tid,))
        return True

    def add_file(self, tid, name, content, remote_key=None):
        fid = uuid.uuid4().hex
        path = self.root / 'files' / fid
        with self.connect() as c:
            if remote_key:
                old = c.execute('SELECT id FROM files WHERE task_id=? AND remote_key=?', (tid, remote_key)).fetchone()
                if old:
                    return old['id']
            path.write_bytes(content)
            c.execute('INSERT INTO files VALUES (?,?,?,?,?,?,?)', (fid, tid, name, str(path), 'pending', '{}', remote_key))
        return fid

    def file_result(self, fid, result):
        with self.connect() as c:
            c.execute('UPDATE files SET status=?,result=? WHERE id=?',
                (result['status'], json.dumps(result, ensure_ascii=False), fid))

    def set_value(self, key, value):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO kv VALUES (?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def value(self, key, default=None):
        with self.connect() as c:
            r = c.execute('SELECT value FROM kv WHERE key=?', (key,)).fetchone()
            return json.loads(r['value']) if r else default
