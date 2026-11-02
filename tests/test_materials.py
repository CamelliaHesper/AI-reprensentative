from app.config import Settings
from app.store import Store
from app.conversation import accept_message
from app.feishu import fetch_attachments
from app.workflow import process_task
from tests.test_conversation import message


def test_downloaded_materials_visible_and_reused_without_changing_notice(tmp_path, monkeypatch):
    s=Store(tmp_path)
    tid=accept_message(s,message('notice','请填写完整报名材料',mentioned=True))['task_id']
    resource={'message_id':'fmsg','file_key':'fkey','name':'报名.csv'}
    accept_message(s,message('fmsg',parent='notice',resources=[resource]))
    class API:
        def __init__(self,cfg): pass
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def download(self,item):
            assert item==resource
            return '姓名,学校\n张三,测试大学'.encode('utf-8')
    monkeypatch.setattr('app.feishu.FeishuAPI',API)
    cfg=Settings(data_dir=tmp_path,feishu_app_id='fixture',feishu_app_secret='fixture',model_api_key='',model_name='',model_base_url='')
    assert fetch_attachments(s,s.get(tid),cfg)==[]
    assert len(s.get(tid)['files'])==1
    assert fetch_attachments(s,s.get(tid),cfg)==[]
    assert len(s.get(tid)['files'])==1
    accept_message(s,message('go','开始解析',parent='fmsg'))
    s.claim_next(); process_task(s,tid,cfg)
    task=s.get(tid)
    assert task['text']=='请填写完整报名材料'
    assert '测试大学' in task['files'][0]['result']['text']
    assert task['status']=='awaiting_model'


def test_collecting_download_lock_prevents_web_enqueue(tmp_path):
    s=Store(tmp_path)
    tid=accept_message(s,message('notice','正文',mentioned=True))['task_id']
    assert s.transition(tid,'collecting_files')
    assert not s.transition(tid,'queued')
    s.recover()
    assert s.get(tid)['status']=='collecting_files'
    assert not s.transition(tid,'queued')
    s.recover_collections()
    assert s.get(tid)['status']=='collecting'
    assert s.claim_next() is None


def test_download_finish_does_not_override_other_state(tmp_path):
    s=Store(tmp_path)
    tid=accept_message(s,message('notice','正文',mentioned=True))['task_id']
    s.update(tid,status='processing')
    s.finish_collection(tid)
    assert s.get(tid)['status']=='processing'
