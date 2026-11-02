"""Local ONLYOFFICE integration. No original document is overwritten."""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from .config import Settings
from .store import Store
from .fulfillment import get_state,save_state,output_path,office_lock
from .word_templates import office_convert

lock=threading.RLock()


@contextmanager
def guard(store):
    """Serialize the web process and the separate Docker callback bridge."""
    with lock:
        with (store.root/'editor-state.lock').open('a+b') as stream:
            stream.seek(0)
            if not stream.read(1):stream.write(b'0');stream.flush()
            stream.seek(0)
            if __import__('os').name=='nt':
                import msvcrt
                deadline=time.monotonic()+90
                while True:
                    try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1);break
                    except OSError:
                        if time.monotonic()>deadline:raise HTTPException(409,'正在保存，请稍后重试')
                        time.sleep(.1)
                try:yield
                finally:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def secret(store):
    path=store.root/'onlyoffice-secret'
    with lock:
        if not path.exists():path.write_text(secrets.token_hex(32),encoding='ascii')
        return path.read_text(encoding='ascii').strip()


def sign(payload,key):
    encode=lambda x:base64.urlsafe_b64encode(x).rstrip(b'=').decode()
    body=encode(b'{"alg":"HS256","typ":"JWT"}')+'.'+encode(json.dumps(payload,separators=(',',':')).encode())
    return body+'.'+encode(hmac.new(key.encode(),body.encode(),hashlib.sha256).digest())


def verify(token,key):
    try:
        header,payload,signature=token.split('.')
        if json.loads(base64.urlsafe_b64decode(header+'==='))['alg']!='HS256':raise ValueError()
        expected=base64.urlsafe_b64encode(hmac.new(key.encode(),(header+'.'+payload).encode(),hashlib.sha256).digest()).rstrip(b'=').decode()
        if not hmac.compare_digest(signature,expected):raise ValueError()
        return json.loads(base64.urlsafe_b64decode(payload+'==='))
    except Exception:raise HTTPException(403,'编辑服务签名无效')


def session(store,tid,sid,token=None):
    task=store.get(tid);state=get_state(store,tid)
    editor=(state or {}).get('editor',{})
    if not task or task['status']=='deleted' or not state or state.get('stale') or editor.get('id')!=sid:
        raise HTTPException(409,'编辑会话已失效')
    if token is not None and not hmac.compare_digest(token,editor['access']):raise HTTPException(403,'编辑访问令牌无效')
    return state,editor


def editor_path(store,tid,sid):return store.root/'editor'/tid/sid/'latest.docx'


class ExportInput(BaseModel):
    nonce:str=Field(min_length=1,max_length=100)
    url:str=Field(min_length=1,max_length=8192)


def save_editor_export(store,tid,cfg,data):
    """Accept only an export from the current document and the latest save request."""
    import re,io,zipfile
    from urllib.parse import unquote
    with guard(store):
        state=get_state(store,tid);editor=(state or {}).get('editor',{})
        if not editor.get('active') or editor.get('save_request')!=data.nonce:raise HTTPException(409,'保存期间内容已变化，请重新保存')
        url=urlsplit(data.url);allowed=urlsplit(cfg.onlyoffice_url)
        path=unquote(url.path)
        if (url.scheme,url.netloc)!=(allowed.scheme,allowed.netloc) or '..' in path.split('/') or not re.match(r'^/cache/files/(?:data|forgotten)/'+re.escape(editor['key'])+r'[/_.-]',path):
            raise HTTPException(403,'导出地址不属于当前编辑文档')
        content=bytearray()
        with httpx.Client(timeout=60,trust_env=False,follow_redirects=False) as client:
            with client.stream('GET',data.url) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(65536):
                    content.extend(chunk)
                    if len(content)>cfg.max_file_mb*1024**2:raise HTTPException(413,'编辑文件过大')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if 'word/document.xml' not in archive.namelist():raise ValueError()
        except Exception:raise HTTPException(400,'导出结果不是有效 Word 文件')
        dest=editor_path(store,tid,editor['id']);temp=dest.with_suffix('.tmp');temp.write_bytes(content);temp.replace(dest)
        editor.update(saved=True,dirty=False,saved_request=data.nonce,saved_at=time.time());save_state(store,tid,state)
    return {'ok':True}


def begin(store,tid,cfg):
    with guard(store):
        state=get_state(store,tid)
        if not state or state.get('stale') or state['stage']!='generated':raise ValueError('请先生成 Word')
        editor=state.get('editor')
        if not editor or not editor.get('active'):
            sid=uuid.uuid4().hex;dest=editor_path(store,tid,sid);dest.parent.mkdir(parents=True,exist_ok=True)
            source=output_path(store,tid,state,'docx')
            if source.suffix=='.doc':
                with office_lock:office_convert(source,dest)
            else:
                import shutil
                shutil.copyfile(source,dest)
            editor={'id':sid,'access':secrets.token_urlsafe(32),'key':uuid.uuid4().hex,'active':True,'saved':False,'dirty':False}
            state['editor']=editor;save_state(store,tid,state)
        sid=editor['id'];base=f'{cfg.onlyoffice_bridge_url}/editor/{tid}/{sid}'
        config={'documentType':'word','type':'desktop','width':'100%','height':'100%',
            'document':{'fileType':'docx','key':editor['key'],'title':Path(state['template_name']).stem+'_在线修改.docx',
                        'url':base+'/file?access='+editor['access'],'permissions':{'edit':True,'download':True,'print':True}},
            'editorConfig':{'mode':'edit','lang':'zh-CN','callbackUrl':base+'/callback?access='+editor['access'],
                'user':{'id':'local-owner','name':'我'},'customization':{'forcesave':True,'autosave':True}}}
        config['token']=sign(config,secret(store))
        return {'config':config,'server':cfg.onlyoffice_url,'session':sid}


def register(app,store,cfg,task_or_404,editable):
    @app.post('/api/tasks/{tid}/editor/cancel')
    def cancel(tid:str):
        editable(tid)
        with guard(store):
            state=get_state(store,tid)
            if state and state.get('editor'):state['editor']['active']=False;save_state(store,tid,state)
        return {'ok':True}

    @app.post('/api/tasks/{tid}/editor/start')
    def start(tid:str):
        editable(tid)
        try:
            with httpx.Client(timeout=3,trust_env=False) as client:
                r=client.get(cfg.onlyoffice_url+'/healthcheck');r.raise_for_status()
            return begin(store,tid,cfg)
        except ValueError as e:raise HTTPException(400,str(e))
        except httpx.HTTPError:raise HTTPException(503,'在线编辑服务尚未启动，请先运行 start-editor.ps1（需要 Docker Desktop）')

    @app.post('/api/tasks/{tid}/editor/dirty')
    def dirty(tid:str):
        editable(tid)
        with guard(store):
            state=get_state(store,tid);editor=(state or {}).get('editor',{})
            if not editor.get('active'):raise HTTPException(409,'编辑器未打开')
            editor['dirty']=True;editor['saved']=False;editor.pop('save_request',None);save_state(store,tid,state)
        return {'ok':True}

    @app.post('/api/tasks/{tid}/editor/save')
    def request_save(tid:str):
        editable(tid)
        with guard(store):
            state=get_state(store,tid);editor=(state or {}).get('editor',{})
            if not editor.get('active'):raise HTTPException(409,'编辑器未打开')
            nonce=uuid.uuid4().hex;editor['save_request']=nonce;editor['saved']=False
            command={'c':'forcesave','key':editor['key'],'userdata':nonce}
            command['token']=sign(command,secret(store));save_state(store,tid,state)
        try:
            with httpx.Client(timeout=15,trust_env=False) as client:
                response=client.post(cfg.onlyoffice_url+'/coauthoring/CommandService.ashx',json=command)
                response.raise_for_status();result=response.json()
            if result.get('error')==4:
                return {'ok':True,'export_required':True,'nonce':nonce}
            elif result.get('error')!=0:raise HTTPException(502,'文档服务无法保存，请保持编辑器打开并重试')
        except httpx.HTTPError:raise HTTPException(502,'保存请求失败，请检查文档服务后重试')
        return {'ok':True}

    @app.post('/api/tasks/{tid}/editor/export')
    def export_current(tid:str,data:ExportInput):
        editable(tid)
        try:return save_editor_export(store,tid,cfg,data)
        except httpx.HTTPError as exc:raise HTTPException(502,'读取最新导出文件失败，请保持编辑器打开并重试') from exc

    @app.post('/api/tasks/{tid}/editor/finish')
    def finish(tid:str):
        editable(tid)
        with guard(store):
            state=get_state(store,tid);editor=(state or {}).get('editor',{})
            if not editor.get('active'):raise HTTPException(409,'编辑器未打开')
            if editor.get('dirty') or not editor.get('saved') or not editor.get('save_request') or editor.get('saved_request')!=editor['save_request']:raise HTTPException(409,'尚未收到最新保存结果，请稍候再试')
            editor['active']=False;state['edited_path']=str(editor_path(store,tid,editor['id']))
            state['word_format']='docx';state['outputs']=['docx'];state['revision']=uuid.uuid4().hex
            state.pop('sent_message_id',None);state.pop('send_request_id',None)
            state['warnings']=['当前文件为人工在线修改版；网页预览根据最新 Word 生成，下载与飞书发送使用此修改版。']
            save_state(store,tid,state)
        return state


def bridge_app(cfg=None):
    cfg=cfg or Settings();store=Store(cfg.data_dir);app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

    @app.get('/editor/{tid}/{sid}/file')
    def file(tid:str,sid:str,access:str):
        session(store,tid,sid,access)
        return FileResponse(editor_path(store,tid,sid),media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    @app.post('/editor/{tid}/{sid}/callback')
    async def callback(tid:str,sid:str,access:str,request:Request):
        raw=await request.body()
        if len(raw)>65536:raise HTTPException(413,'回调过大')
        body=json.loads(raw)
        token=body.get('token') or request.headers.get('authorization','').removeprefix('Bearer ')
        signed=verify(token,secret(store));payload=signed.get('payload',signed)
        with guard(store):
            state,editor=session(store,tid,sid,access)
            if payload.get('key')!=editor['key']:raise HTTPException(403,'文档标识不匹配')
            status=payload.get('status')
            if status not in (2,6):return {'error':0}
            if not editor.get('active'):return {'error':0}
            if status!=6 or not editor.get('save_request') or payload.get('userdata')!=editor['save_request']:return {'error':0}
            url=payload.get('url','');parts=urlsplit(url);allowed=urlsplit(cfg.onlyoffice_url)
            if (parts.scheme,parts.netloc)!=(allowed.scheme,allowed.netloc):raise HTTPException(403,'拒绝未知文件下载地址')
            content=bytearray()
            with httpx.Client(timeout=60,trust_env=False,follow_redirects=False) as client:
                with client.stream('GET',url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes(65536):
                        content.extend(chunk)
                        if len(content)>cfg.max_file_mb*1024**2:raise HTTPException(413,'编辑文件过大')
            import io,zipfile
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as z:
                    if 'word/document.xml' not in z.namelist():raise ValueError()
            except Exception:raise HTTPException(400,'保存结果不是有效的 Word 文件')
            dest=editor_path(store,tid,sid);tmp=dest.with_suffix('.tmp');tmp.write_bytes(content);tmp.replace(dest)
            editor.update(saved=True,dirty=False,saved_at=time.time(),saved_request=editor['save_request']);save_state(store,tid,state)
        return {'error':0}
    return app


bridge=bridge_app()
