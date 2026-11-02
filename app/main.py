import json
import threading
import uuid
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, quote

from fastapi import FastAPI, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from .config import Settings, ROOT
from .store import Store
from .workflow import Analysis, process_task
from .parsers import SUPPORTED


class TaskInput(BaseModel):
    text: str = Field(min_length=1, max_length=100000)


class AnalysisInput(Analysis):
    confirmed: bool = False

class CompletionInput(BaseModel):
    confirmed:bool=False
    revision:str=''


def create_app(settings=None, run_worker=True):
    cfg = settings or Settings()
    store = Store(cfg.data_dir)
    from .auth import Auth,principal,user,COOKIE,register as register_auth
    auth=Auth(store)
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            task = store.claim_next()
            if task: process_task(store, task['id'], cfg)
            else: stop.wait(.8)

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if run_worker:
            store.recover()
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            from .feishu import refresh_initiator_names
            threading.Thread(target=refresh_initiator_names,args=(store,cfg),daemon=True).start()
        yield
        stop.set()
        if thread: thread.join(timeout=2)

    app = FastAPI(title='AI课代表 · 飞书任务助手', lifespan=lifespan)
    app.state.store = store
    app.state.auth=auth
    register_auth(app,auth)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def local_only(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail':'仅允许本机页面访问'}, status_code=403)
        if request.url.path.startswith('/api/') and request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail':'不允许跨站访问'}, status_code=403)
        current=auth.resolve(request.cookies.get(COOKIE,''))
        context_token=principal.set(current)
        try:
            if request.url.path.startswith('/api/') and request.url.path not in {'/api/auth/login','/api/auth/register'}:
                if not current:return JSONResponse({'detail':'请先登录'},status_code=401)
                match=re.match(r'^/api/tasks/([^/]+)(?:/|$)',request.url.path)
                if match:
                    target=store.get(match[1])
                    if not target or not auth.allows(current['id'],target):return JSONResponse({'detail':'任务不存在'},status_code=404)
            response = await call_next(request)
        finally:principal.reset(context_token)
        if request.url.path.startswith('/api/'):response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        office_origin=cfg.onlyoffice_url.rstrip('/')
        if request.url.path=='/editor.html':
            response.headers['Content-Security-Policy'] = f"default-src 'self'; script-src 'self' {office_origin}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src {office_origin}; frame-ancestors 'self'"
        else:
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"
        return response

    def task_or_404(tid):
        task = store.get(tid)
        if not task or task['status']=='deleted': raise HTTPException(404, '任务不存在或已删除')
        return task

    def display_status(task):
        from .fulfillment import get_state,analysis_key,BUSY
        if task['status'] in BUSY or task['status']=='deleted':return task['status']
        state=get_state(store,task['id'])
        revision=state['revision'] if state and not state.get('stale') and state.get('stage')=='generated' else analysis_key(store,task['id'])
        if state and not state.get('stale') and state.get('editor',{}).get('active'):return 'awaiting_confirmation'
        completed=store.value('task_completion:'+task['id'],{})
        if completed.get('revision')==revision or (state and not state.get('stale') and state.get('sent_message_id')):return 'confirmed'
        if state and not state.get('stale'):return 'awaiting_confirmation'
        return task['status']

    def editable(tid):
        task = task_or_404(tid)
        if task['status'] in {'queued', 'processing', 'receiving', 'collecting_files','preparing','generating'}: raise HTTPException(409, '任务排队或处理中，请稍后操作')
        return task

    @app.get('/api/status')
    def status():
        return {'model_ready':cfg.model_ready, 'feishu_configured':bool(cfg.feishu_app_id and cfg.feishu_app_secret),
            'initiator_name_error':store.value('initiator_name_error',''),
            'vision_ready':bool(cfg.vision_api_key and cfg.vision_model and cfg.vision_base_url),
            'feishu':store.value('feishu_status', {'state':'未启动'}), 'max_file_mb':cfg.max_file_mb}

    @app.get('/api/tasks')
    def tasks(deleted: bool=False):
        rows=store.listing(deleted=deleted,owner_id=user()['id'])
        for row in rows:row['display_status']=display_status(store.get(row['id']))
        return rows

    @app.post('/api/initiators/refresh')
    def refresh_names():
        from .feishu import refresh_initiator_names
        with store.connect() as c:row=c.execute('SELECT sender_id FROM feishu_bindings WHERE account_id=?',(user()['id'],)).fetchone()
        return refresh_initiator_names(store,cfg,[row['sender_id']] if row else [])

    @app.delete('/api/tasks/{tid}')
    def delete_task(tid: str):
        task_or_404(tid)
        if not store.set_deleted(tid): raise HTTPException(409,'任务排队、解析或接收附件中，请稍后删除')
        return {'ok':True}

    @app.post('/api/tasks/{tid}/restore')
    def restore_task(tid: str):
        if not store.set_deleted(tid,restore=True): raise HTTPException(404,'已删除的任务不存在')
        return {'ok':True}

    @app.post('/api/tasks', status_code=201)
    def create(data: TaskInput):
        if not data.text.strip(): raise HTTPException(400, '请输入需求文字')
        tid, _ = store.create_task(uuid.uuid4().hex, data.text, 'local', {'owner_id':user()['id']}, status='draft')
        # 人工任务先等待附件上传或用户点击开始，避免与上传发生竞争。
        return {'id':tid}

    @app.post('/api/demo', status_code=201)
    def demo():
        tid, _ = store.create_task(uuid.uuid4().hex,
            '【示例数据】请填写创新实践项目报名表，提供姓名、学校、专业、联系电话、项目名称和申请理由。请于11月6日18:00前将材料发送给负责人。', 'demo', {'owner_id':user()['id']}, status='draft')
        store.add_file(tid, '示例报名表.csv', '姓名,学校,专业,联系电话,项目名称,申请理由\n,,,,,'.encode('utf-8-sig'))
        store.update(tid, status='queued')
        return {'id':tid}

    @app.get('/api/tasks/{tid}')
    def task(tid: str):
        t = task_or_404(tid)
        t['display_status']=display_status(t)
        for f in t['files']:
            f.pop('path', None); f.pop('remote_key', None)
        return t

    @app.post('/api/tasks/{tid}/files')
    async def upload(tid: str, file: UploadFile):
        original=editable(tid)
        name = (file.filename or '附件').replace('\\','/').split('/')[-1][:240]
        if Path(name).suffix.lower() not in SUPPORTED: raise HTTPException(400, '不支持此类型，请上传 Word、Excel、CSV、TXT 或 ZIP')
        if not store.transition(tid, 'receiving'): raise HTTPException(409, '任务排队或处理中，请稍后操作')
        try:
            limit = cfg.max_file_mb*1024**2
            content = bytearray()
            while chunk := await file.read(65536):
                content.extend(chunk)
                if len(content) > limit: raise HTTPException(413, '附件超过文件大小限制')
            if not content: raise HTTPException(400, '附件为空')
            fid = store.add_file(tid, name, bytes(content))
            return {'id':fid}
        finally:
            store.update(tid, status='collecting' if original['metadata'].get('conversation') else 'draft')

    @app.post('/api/tasks/{tid}/retry')
    def retry(tid: str):
        editable(tid)
        if not store.queue_task(tid): raise HTTPException(409, '任务正在处理或在线编辑中，请先保存编辑后再解析')
        return {'ok':True}

    @app.post('/api/tasks/{tid}/complete')
    def complete(tid:str,data:CompletionInput):
        import time
        from .fulfillment import get_state,analysis_key
        task=editable(tid)
        if not data.confirmed:raise HTTPException(400,'请先确认已检查办理结果')
        if task['status'] in {'draft','collecting','awaiting_model','failed'}:raise HTTPException(409,'请先完成材料解析和结果检查')
        state=get_state(store,tid)
        if state and state.get('editor',{}).get('active'):raise HTTPException(409,'请先完成在线编辑保存')
        revision=state['revision'] if state and not state.get('stale') and state.get('stage')=='generated' else analysis_key(store,tid)
        if data.revision and data.revision!=revision:raise HTTPException(409,'结果已变化，请重新核对')
        store.set_value('task_completion:'+tid,{'revision':revision,'confirmed_at':time.time(),'account_id':user()['id']})
        return {'ok':True,'display_status':'confirmed'}

    @app.put('/api/tasks/{tid}/analysis')
    def update_analysis(tid: str, data: AnalysisInput):
        task = editable(tid)
        if task['status']=='collecting': raise HTTPException(409, '请先收齐材料并开始解析，再整理确认需求')
        analysis = data.model_dump(exclude={'confirmed'})
        analysis['mode'] = 'manual' if task['analysis'].get('mode') != 'ai' else 'ai_edited'
        if not store.transition(tid, 'confirmed' if data.confirmed else 'awaiting_confirmation', analysis):
            raise HTTPException(409, '任务排队或处理中，请稍后操作')
        return {'ok':True}

    @app.get('/api/profile')
    def profile(): return store.value('profile:'+user()['id'], {})

    @app.put('/api/profile')
    def put_profile(data: dict[str, str]):
        if len(data)>40 or any(len(k)>80 or len(v)>2000 for k,v in data.items()): raise HTTPException(400,'个人资料内容过长')
        store.set_value('profile:'+user()['id'], data)
        return {'ok':True}

    @app.get('/api/tasks/{tid}/files/{fid}')
    def download(tid: str, fid: str):
        t = task_or_404(tid)
        f = next((x for x in t['files'] if x['id']==fid), None)
        if not f: raise HTTPException(404,'附件不存在')
        path = Path(f['path']).resolve()
        if path.parent != (store.root/'files').resolve() or not path.is_file(): raise HTTPException(404,'附件文件不存在')
        return FileResponse(path, filename=f['name'], media_type='application/octet-stream')

    @app.get('/api/tasks/{tid}/export')
    def export(tid: str, format: str='json'):
        t = task(tid)
        if format == 'json':
            body = json.dumps(t, ensure_ascii=False, indent=2); mime='application/json'; ext='json'
        elif format == 'md':
            a = t['analysis']
            body = '# 任务解析结果\n\n## 原始消息\n'+t['text']+'\n\n## 需求摘要\n'+a.get('summary','未整理')
            for key,label in [('actions','行动事项'),('materials','所需材料'),('fields','待填写字段'),('questions','待确认问题'),('evidence','原文依据')]:
                body += '\n\n## '+label+'\n'+'\n'.join('- '+x for x in a.get(key,[]))
            body += '\n\n截止时间：'+a.get('deadline','未知')+'\n\n提交方式：'+a.get('submission','未知')
            body += '\n\n## 处理提示\n'+t['error']
            for f in t['files']:
                body += '\n\n## 附件：'+f['name']+'\n'+f['result'].get('text','')+'\n'+f['result'].get('error','')
            mime='text/markdown'; ext='md'
        else: raise HTTPException(400,'只支持 json 或 md')
        return Response(body, media_type=mime, headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote('任务解析结果.'+ext)}"})

    from .fulfillment_routes import register
    register(app,store,cfg,task_or_404,editable)
    from .online_word import register as register_editor
    register_editor(app,store,cfg,task_or_404,editable)
    app.mount('/', StaticFiles(directory=ROOT/'static', html=True), name='static')
    return app


app = create_app()
