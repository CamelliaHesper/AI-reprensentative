from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from .documents import list_documents
from .fulfillment import get_state,prepare_form,update_values,generate,output_path,doc_for,template_path,office_lock
from .word_templates import office_convert
from .fulfillment import save_state
from .output_naming import output_name

class Selection(BaseModel):
    option_id:str
    template_id:str
class Values(BaseModel):
    revision:str
    values:dict[str,str]
class Confirmation(BaseModel):
    revision:str
    confirmed:bool=False
class AssistantInput(Values):
    message:str

def register(app,store,cfg,task_or_404,editable):
    def operation(tid,fn,state='preparing'):
        old=editable(tid)
        if old['status']=='collecting':raise HTTPException(409,'请先收齐材料并开始解析')
        if not store.transition(tid,state):raise HTTPException(409,'任务正在处理')
        try:return fn()
        except ValueError as e:raise HTTPException(400,str(e))
        except Exception as e:raise HTTPException(502,f'操作失败（{type(e).__name__}），请检查模型或本机 Word')
        finally:store.update(tid,status=old['status'])

    @app.get('/api/tasks/{tid}/documents')
    def docs(tid:str):
        task_or_404(tid)
        return [{k:v for k,v in d.items() if k!='path'} for d in list_documents(store,tid)]

    @app.get('/api/tasks/{tid}/documents/{did}/download')
    def download(tid:str,did:str):
        task_or_404(tid)
        try:d=doc_for(store,tid,did)
        except ValueError as e:raise HTTPException(404,str(e))
        return FileResponse(d['path'],filename=Path(d['name']).name,media_type='application/octet-stream')

    @app.get('/api/tasks/{tid}/documents/{did}/preview')
    def preview(tid:str,did:str):
        task_or_404(tid)
        try:
            d=doc_for(store,tid,did)
            if Path(d['name']).suffix.lower()=='.pdf':path=Path(d['path'])
            else:
                source=template_path(store,d)
                if Path(d['name']).suffix.lower()=='.doc':source=source.parent/'original.doc'
                root=store.root/'prepared'/did;root.mkdir(parents=True,exist_ok=True);path=root/('source-original.pdf' if Path(d['name']).suffix.lower()=='.doc' else 'source.pdf')
                with office_lock:
                    if not path.exists():office_convert(source,path)
            return FileResponse(path,media_type='application/pdf')
        except ValueError as e:raise HTTPException(400,str(e))

    @app.get('/api/tasks/{tid}/fulfillment')
    def get(tid:str):task_or_404(tid);return get_state(store,tid)

    @app.post('/api/tasks/{tid}/selection')
    def choose(tid:str,data:Selection):return operation(tid,lambda:prepare_form(store,tid,data.option_id,data.template_id,cfg))

    @app.put('/api/tasks/{tid}/form')
    def save(tid:str,data:Values):return operation(tid,lambda:update_values(store,tid,data.revision,data.values))

    @app.post('/api/tasks/{tid}/generate')
    def build(tid:str,data:Confirmation):return operation(tid,lambda:generate(store,tid,data.revision,data.confirmed),'generating')

    @app.post('/api/tasks/{tid}/form-assistant')
    def assistant(tid:str,data:AssistantInput):
        from .form_assistant import suggest
        return operation(tid,lambda:suggest(store,tid,cfg,data.revision,data.values,data.message))

    @app.post('/api/tasks/{tid}/send-feishu')
    def send(tid:str,data:Confirmation):
        def perform():
            import uuid
            from .feishu import FeishuAPI
            task=task_or_404(tid);state=get_state(store,tid)
            if not data.confirmed or not state or state.get('stale') or state['revision']!=data.revision or state['stage']!='generated':
                raise ValueError('请先检查生成文件并确认发送')
            if state.get('editor',{}).get('active'):raise ValueError('请先在在线编辑页面保存并完成编辑，再发送最新版本')
            chat=task['metadata'].get('chat_id')
            if not chat:raise ValueError('本任务没有来源飞书群，无法返回群聊')
            if state.get('sent_message_id'):return state
            if not cfg.feishu_app_id or not cfg.feishu_app_secret:raise ValueError('请先配置飞书应用凭证，再发送文件')
            if not state.get('send_request_id'):
                state['send_request_id']=str(uuid.uuid4());save_state(store,tid,state)
            path=output_path(store,tid,state,'docx')
            if not path.is_file():raise ValueError('生成文件不存在，请重新生成后发送')
            name=output_name(task,state)
            try:
                with FeishuAPI(cfg) as api:mid=api.send_file(chat,path,name,state['send_request_id'])
            except ValueError:raise
            except Exception as exc:raise ValueError('飞书发送失败，请检查网络、机器人群成员身份及文件上传和发消息权限，然后重试') from exc
            if not mid:raise ValueError('飞书未返回消息编号，请检查群聊后再重试')
            state['sent_message_id']=mid;state['sent_file_name']=name;save_state(store,tid,state);return state
        return operation(tid,perform)

    @app.get('/api/tasks/{tid}/outputs/{kind}')
    def output(tid:str,kind:str):
        task_or_404(tid);state=get_state(store,tid)
        if not state or state.get('stale'):raise HTTPException(404,'尚未生成文件或材料已变化，请重新确认生成')
        try:path=output_path(store,tid,state,kind)
        except ValueError as e:raise HTTPException(404,str(e))
        if kind=='pdf' and state.get('edited_path'):
            source=output_path(store,tid,state,'docx')
            try:
                with office_lock:
                    if not path.is_file():
                        pending=path.with_name('preview.pending.pdf')
                        office_convert(source,pending)
                        pending.replace(path)
            except Exception as exc:raise HTTPException(502,'修改后的 Word 已保存，但预览转换失败；可下载 Word 或重新打开在线编辑器查看。') from exc
        if not path.is_file():raise HTTPException(404,'文件不存在')
        if kind=='pdf':return FileResponse(path,media_type='application/pdf',headers={'Cache-Control':'no-store'})
        return FileResponse(path,filename=output_name(task_or_404(tid),state),media_type='application/octet-stream')
