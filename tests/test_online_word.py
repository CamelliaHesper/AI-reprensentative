import io
import json
from pathlib import Path
import httpx
import pytest
from docx import Document
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.config import Settings
from app.store import Store
from app.fulfillment import save_state,get_state,analysis_key,output_path
from app.online_word import begin,sign,verify,secret,bridge_app


def fixture(tmp_path):
    cfg=Settings(data_dir=tmp_path/'data');store=Store(cfg.data_dir)
    tid,_=store.create_task('m','通知','feishu',{'chat_id':'oc_test'},status='awaiting_confirmation')
    root=store.root/'outputs'/tid/'rev';root.mkdir(parents=True)
    Document().save(root/'filled.docx')
    state={'revision':'rev','stage':'generated','word_format':'docx','template_name':'申请.docx',
        'analysis_key':analysis_key(store,tid),'outputs':['docx','pdf'],'fields':[],'sent_message_id':'old'}
    save_state(store,tid,state)
    return cfg,store,tid


def test_jwt_tampering_rejected():
    token=sign({'key':'k'},'secret')
    assert verify(token,'secret')=={'key':'k'}
    with pytest.raises(HTTPException):verify(token,'wrong')


def test_editor_session_is_signed_and_original_kept(tmp_path):
    cfg,store,tid=fixture(tmp_path)
    result=begin(store,tid,cfg)
    assert result['config']['document']['fileType']=='docx'
    assert verify(result['config']['token'],secret(store))['document']['key']==result['config']['document']['key']
    assert begin(store,tid,cfg)['session']==result['session']
    with TestClient(bridge_app(cfg)) as client:
        path=f'/editor/{tid}/{result["session"]}/file'
        assert client.get(path+'?access=wrong').status_code==403
        access=get_state(store,tid)['editor']['access']
        assert client.get(path+'?access='+access).status_code==200


def test_callback_save_finish_switches_download_version(tmp_path,monkeypatch):
    cfg,store,tid=fixture(tmp_path);result=begin(store,tid,cfg)
    state=get_state(store,tid);state['editor']['save_request']='latest-save';save_state(store,tid,state)
    editor=get_state(store,tid)['editor'];buffer=io.BytesIO();doc=Document();doc.add_paragraph('人工修改后的内容');doc.save(buffer)
    real_client=httpx.Client
    def handle(request):return httpx.Response(200,content=buffer.getvalue())
    def local_client(**kwargs):return real_client(transport=httpx.MockTransport(handle),**kwargs)
    # 建立 TestClient 后再替换下载客户端，避免影响 ASGI 传输。
    with TestClient(bridge_app(cfg)) as client:
        monkeypatch.setattr('app.online_word.httpx.Client',local_client)
        body={'key':editor['key'],'status':6,'url':cfg.onlyoffice_url+'/cache/edited.docx','userdata':'latest-save'}
        path=f'/editor/{tid}/{result["session"]}/callback?access='+editor['access']
        old={**body,'userdata':'obsolete-save'}
        assert client.post(path,json={'token':sign(old,secret(store))}).json()=={'error':0}
        assert not get_state(store,tid)['editor']['saved']
        assert client.post(path,json={'token':sign(body,secret(store))}).json()=={'error':0}
        body['url']='http://unknown.invalid/file'
        assert client.post(path,json={'token':sign(body,secret(store))}).status_code==403
        monkeypatch.setattr('app.online_word.httpx.Client',real_client)
    from app.main import create_app
    with TestClient(create_app(cfg,run_worker=False)) as client:
        response=client.post(f'/api/tasks/{tid}/editor/finish',json={})
        assert response.status_code==200
        state=response.json()
        assert not state['editor']['active'] and state['outputs']==['docx']
        assert 'sent_message_id' not in state and state['revision']!='rev'
        assert Document(output_path(store,tid,state,'docx')).paragraphs[0].text=='人工修改后的内容'
        assert not Document(store.root/'outputs'/tid/'rev'/'filled.docx').paragraphs
        downloaded=client.get(f'/api/tasks/{tid}/outputs/docx')
        assert downloaded.status_code==200
        assert Document(io.BytesIO(downloaded.content)).paragraphs[0].text=='人工修改后的内容'
        converted=[]
        def preview(source,destination):
            assert Document(source).paragraphs[0].text=='人工修改后的内容'
            converted.append(source);Path(destination).write_bytes(b'%PDF-1.4 modified-version')
        monkeypatch.setattr('app.fulfillment_routes.office_convert',preview)
        pdf=client.get(f'/api/tasks/{tid}/outputs/pdf')
        assert pdf.status_code==200 and b'modified-version' in pdf.content
        assert pdf.headers['cache-control']=='no-store'
        assert client.get(f'/api/tasks/{tid}/outputs/pdf').status_code==200
        assert len(converted)==1


def test_unsaved_editor_cannot_finish_or_send(tmp_path):
    cfg,store,tid=fixture(tmp_path);begin(store,tid,cfg)
    from app.main import create_app
    with TestClient(create_app(cfg,run_worker=False)) as client:
        assert client.post(f'/api/tasks/{tid}/editor/finish',json={}).status_code==409
        assert client.post(f'/api/tasks/{tid}/send-feishu',json={'revision':'rev','confirmed':True}).status_code==400
        assert client.post(f'/api/tasks/{tid}/editor/cancel',json={}).status_code==200
        assert not get_state(store,tid)['editor']['active']


def test_cancel_editor_unlocks_form_without_overwriting_output(tmp_path):
    from app.fulfillment import update_values
    cfg,store,tid=fixture(tmp_path)
    original=store.root/'outputs'/tid/'rev'/'filled.docx';before=original.read_bytes()
    begin(store,tid,cfg)
    state=get_state(store,tid);state['fields']=[{'target':'c1','label':'姓名','value':'已保存内容','required':True}];save_state(store,tid,state)
    with pytest.raises(ValueError,match='在线编辑'):update_values(store,tid,'rev',{'c1':'当前表单输入'})
    from app.main import create_app
    with TestClient(create_app(cfg,run_worker=False)) as client:
        assert client.post(f'/api/tasks/{tid}/editor/cancel',json={}).status_code==200
        saved=client.put(f'/api/tasks/{tid}/form',json={'revision':'rev','values':{'c1':'当前表单输入'}})
        assert saved.status_code==200 and saved.json()['fields'][0]['value']=='当前表单输入'
    assert original.read_bytes()==before


def test_force_save_command_is_signed_and_correlated(tmp_path,monkeypatch):
    cfg,store,tid=fixture(tmp_path);begin(store,tid,cfg)
    from app.main import create_app
    real_client=httpx.Client
    def handle(request):
        body=json.loads(request.content)
        signed=verify(body['token'],secret(store))
        assert signed['c']=='forcesave'
        assert signed['userdata']==get_state(store,tid)['editor']['save_request']
        return httpx.Response(200,json={'error':0})
    with TestClient(create_app(cfg,run_worker=False)) as client:
        monkeypatch.setattr('app.online_word.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
        assert client.post(f'/api/tasks/{tid}/editor/save',json={}).status_code==200
        assert client.post(f'/api/tasks/{tid}/editor/finish',json={}).status_code==409
        monkeypatch.setattr('app.online_word.httpx.Client',real_client)


def test_already_force_saved_document_exports_current_version(tmp_path,monkeypatch):
    from app.online_word import save_editor_export,ExportInput
    cfg,store,tid=fixture(tmp_path);begin(store,tid,cfg)
    state=get_state(store,tid);state['editor']['dirty']=True;save_state(store,tid,state)
    from app.main import create_app
    real_client=httpx.Client;buffer=io.BytesIO();doc=Document();doc.add_paragraph('最新人工修改');doc.save(buffer)
    def handle(request):
        if request.method=='POST':return httpx.Response(200,json={'error':4})
        return httpx.Response(200,content=buffer.getvalue())
    with TestClient(create_app(cfg,run_worker=False)) as client:
        monkeypatch.setattr('app.online_word.httpx.Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handle),**kwargs))
        saving=client.post(f'/api/tasks/{tid}/editor/save',json={})
        assert saving.status_code==200 and saving.json()['export_required']
        nonce=saving.json()['nonce'];editor=get_state(store,tid)['editor']
        for bad_url in [cfg.onlyoffice_url+'/cache/files/data/other-document/output.docx','http://evil.invalid/export.docx']:
            result=client.post(f'/api/tasks/{tid}/editor/export',json={'nonce':nonce,'url':bad_url})
            assert result.status_code==403
        url=cfg.onlyoffice_url+'/cache/files/data/'+editor['key']+'_export/output.docx'
        assert client.post(f'/api/tasks/{tid}/editor/export',json={'nonce':nonce,'url':url}).status_code==200
        assert client.post(f'/api/tasks/{tid}/editor/finish',json={}).status_code==200
        assert Document(output_path(store,tid,get_state(store,tid),'docx')).paragraphs[0].text=='最新人工修改'
        monkeypatch.setattr('app.online_word.httpx.Client',real_client)
