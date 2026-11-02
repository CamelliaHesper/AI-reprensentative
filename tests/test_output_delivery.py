import json
import httpx
import pytest
from docx import Document
from docx.shared import RGBColor
from app.word_templates import fill_template,index_template
from app.feishu import FeishuAPI
from app.config import Settings


def test_filled_text_is_uniform_black_even_unchanged(tmp_path):
    source=tmp_path/'template.docx';out=tmp_path/'out.docx'
    doc=Document();p=doc.add_table(rows=1,cols=1).cell(0,0).paragraphs[0]
    p.add_run('示例').font.color.rgb=RGBColor(255,0,0);doc.save(source)
    target=index_template(source)[0]['id']
    fill_template(source,out,{target:'示例'},normalize=True)
    r=Document(out).tables[0].cell(0,0).paragraphs[0].runs[0]
    assert str(r.font.color.rgb)=='000000'
    assert r.font.name=='宋体' and r.font.size.pt==10.5


def test_upload_then_send_file_to_original_chat(tmp_path):
    requests=[]
    def handle(request):
        requests.append(request)
        if request.url.path.endswith('/files'):
            assert b'file_type' in request.content and b'stream' in request.content
            return httpx.Response(200,json={'code':0,'data':{'file_key':'file_test'}})
        body=json.loads(request.content)
        assert body['receive_id']=='oc_original' and body['uuid']=='stable-id'
        assert json.loads(body['content'])=={'file_key':'file_test'}
        return httpx.Response(200,json={'code':0,'data':{'message_id':'om_sent'}})
    path=tmp_path/'test.doc';path.write_bytes(b'test document')
    api=FeishuAPI(Settings());api.client.close()
    api.client=httpx.Client(base_url='https://open.feishu.cn/open-apis/',transport=httpx.MockTransport(handle))
    try:assert api.send_file('oc_original',path,'test.doc','stable-id')=='om_sent'
    finally:api.client.close()
    assert len(requests)==2


def test_delivery_confirmation_retry_and_new_revision(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.fulfillment import save_state,get_state,analysis_key,update_values
    app=create_app(Settings(data_dir=tmp_path/'data',feishu_app_id='test',feishu_app_secret='test'),run_worker=False)
    store=app.state.store
    tid,_=store.create_task('message','通知','feishu',{'chat_id':'oc_original'},status='awaiting_confirmation')
    state={'revision':'r','analysis_key':analysis_key(store,tid),'stage':'generated','template_name':'申请.doc',
        'word_format':'doc','outputs':['docx'],'fields':[{'target':'c1','label':'姓名','value':'测试','original':'','required':True}]}
    root=store.root/'outputs'/tid/'r';root.mkdir(parents=True);(root/'filled.doc').write_bytes(b'test')
    save_state(store,tid,state)
    calls=[]
    class FakeAPI:
        def __init__(self,cfg):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def send_file(self,chat,path,name,request_id):
            calls.append((chat,name,request_id))
            if len(calls)==1:raise ValueError('模拟网络失败')
            return 'om_sent'
    monkeypatch.setattr('app.feishu.FeishuAPI',FakeAPI)
    with TestClient(app) as client:
        url=f'/api/tasks/{tid}/send-feishu'
        assert client.post(url,json={'revision':'r','confirmed':False}).status_code==400
        assert not calls
        assert client.post(url,json={'revision':'r','confirmed':True}).status_code==400
        assert client.post(url,json={'revision':'r','confirmed':True}).status_code==200
        assert calls[0]==calls[1] and calls[1][:2]==('oc_original','申请_已填写.doc')
        assert client.post(url,json={'revision':'r','confirmed':True}).status_code==200
        assert len(calls)==2
    new=update_values(store,tid,'r',{'c1':'新内容'})
    assert new['revision']!='r' and 'sent_message_id' not in new and 'send_request_id' not in new
