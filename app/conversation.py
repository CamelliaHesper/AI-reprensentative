"""飞书会话归属与事件幂等。这里只操作本地数据库，不调用外部接口。"""
import json
import time
import uuid

COLLECTING = {'collecting', 'collecting_files'}


def accept_message(store, msg):
    # 账号操作属于控制消息，不允许落入建任务或补充材料流程。
    from .auth import is_binding_message
    if is_binding_message(msg):return None
    mid = msg.get('message_id')
    if not mid or not msg.get('sender_id') or not msg.get('chat_id'):
        return None
    text = msg.get('text','').strip()
    command=text.rstrip('。！!！ \n')
    start = command == '开始解析'
    upload = command == '开始上传'
    session_key='feishu_upload:'+msg['chat_id']+':'+msg['sender_id']
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('SELECT task_id FROM conversation_events WHERE message_id=?',(mid,)).fetchone()
        if old: return {'action':'duplicate','task_id':old['task_id']}
        linked = None
        # parent 优先，root 只用于明确处于同一个消息树的跟帖。
        for anchor in (msg.get('parent_id'), msg.get('root_id')):
            if anchor:
                linked = c.execute('SELECT t.* FROM tasks t JOIN message_links l ON l.task_id=t.id WHERE l.message_id=?',(anchor,)).fetchone()
                if linked: break
        now = time.time()
        if linked is None and msg.get('resources') and not msg.get('parent_id') and not msg.get('root_id'):
            session=c.execute('SELECT value FROM kv WHERE key=?',(session_key,)).fetchone()
            if session:
                selected=json.loads(session['value'])
                if selected.get('expires',0)>now:
                    linked=c.execute("SELECT * FROM tasks WHERE id=? AND status='collecting'",(selected['task_id'],)).fetchone()
        if linked:
            if linked['status']=='deleted': return None
            meta = json.loads(linked['metadata'])
            if meta.get('chat_id') != msg['chat_id'] or meta.get('sender_id') != msg['sender_id']:
                return None
            tid = linked['id']
            reopen=upload and linked['status'] in {'awaiting_confirmation','awaiting_model','confirmed','partial','failed','draft'}
            if linked['status'] not in COLLECTING and not reopen:
                action='busy'
            else:
                resources = meta.setdefault('resources',[])
                seen = {(r['message_id'],r['file_key']) for r in resources}
                for r in msg.get('resources',[]):
                    key = (r['message_id'],r['file_key'])
                    if key not in seen:
                        resources.append(r); seen.add(key)
                if start:
                    action='start'; status='queued'
                    meta['analysis_revision']=uuid.uuid4().hex
                    c.execute('DELETE FROM kv WHERE key=?',('task_completion:'+tid,))
                    active=c.execute('SELECT value FROM kv WHERE key=?',(session_key,)).fetchone()
                    if active and json.loads(active['value']).get('task_id')==tid:
                        c.execute('DELETE FROM kv WHERE key=?',(session_key,))
                elif upload:
                    action='upload_started'; status='collecting'
                    c.execute('INSERT OR REPLACE INTO kv VALUES (?,?)',
                        (session_key,json.dumps({'task_id':tid,'expires':now+1800})))
                else:
                    action='attachments' if msg.get('resources') else 'supplement'
                    status='collecting'
                    if text:
                        supplements=meta.setdefault('supplements',[])
                        if sum(len(x) for x in supplements)+len(text)>100000:
                            action='limit'
                        else: supplements.append(text)
                if len(resources)>100:
                    action='limit'
                if action!='limit':
                    c.execute('UPDATE tasks SET metadata=?,status=?,updated=? WHERE id=?',
                        (json.dumps(meta,ensure_ascii=False),status,now,tid))
        else:
            if not msg.get('is_mentioned') and msg.get('chat_type')!='p2p': return None
            if start or upload:
                return {'action':'unlinked','task_id':None}
            if not text and not msg.get('resources'): return None
            tid = uuid.uuid4().hex
            meta = {**msg,'conversation':True,'supplements':[]}
            # 在升级前已经处理过的事件不重新创建。
            prior=c.execute('SELECT id FROM tasks WHERE message_id=?',(mid,)).fetchone()
            if prior: return {'action':'duplicate','task_id':prior['id']}
            c.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?)',
                (tid,mid,text or '附件需求任务','feishu',json.dumps(meta,ensure_ascii=False),'collecting','{}','',now,now))
            action='created'
        c.execute('INSERT INTO conversation_events VALUES (?,?)',(mid,tid))
        if action not in {'busy','limit'}:
            c.execute('INSERT OR IGNORE INTO message_links VALUES (?,?)',(mid,tid))
        return {'action':action,'task_id':tid}


def response_text(result, task, web_url):
    action=result['action']
    if action=='unlinked': return '请回复当前任务的材料收集提示，再发送“开始上传”或“开始解析”，以免选错任务。'
    link=web_url.rstrip('/')+'/#task/'+task['id']
    if action=='start':
        if not task['metadata'].get('resources') and not task.get('files'):
            return '尚未收到任何附件，本次只解析通知文字。如需加入附件，请等解析结束后回复本任务发送“开始上传”，收到确认后再发文件。\n'+link
        return '已开始合并通知和附件进行解析。查看进度：'+link
    if action=='busy': return '这个任务已经开始解析或处理完成，未加入本次材料。请等待结果；新需求请重新 @机器人。'
    if action=='limit': return '本任务材料数量或补充文字超过限制，本次未加入，请在网页检查材料。'+link
    if action=='upload_started':
        return ('已选定本任务接收附件，30分钟内有效。\n请直接把文件发送到当前群，不要放入回复框。'
                '同一发送者在当前群接下来发送的文件都会加入本任务。\n'
                '收齐后，回复本消息发送“开始解析”；如需换任务，请回复另一个任务提示发送“开始上传”。\n'+link)
    if action=='created':
        return ('已收到完整需求，正在收集材料，尚未开始分析。\n'
                '请先回复本消息发送“开始上传”，收到确认后，将 ZIP、Word、Excel 或 CSV 普通发送到群里，可分多次发送。\n'
                '补充文字请回复本消息发送。\n'
                '材料收齐后，请回复本消息发送“开始解析”；不需要附件也可以直接开始。\n'
                '飞书不支持把此类文件放入回复框，请先开启上传再发文件。网页：'+link)
    count=len(task['metadata'].get('resources',[]))
    return f'已加入同一个任务，当前关联 {count} 个附件。收齐后请回复本消息发送“开始解析”。\n'+link
