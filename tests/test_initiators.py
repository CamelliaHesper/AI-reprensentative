from app.store import Store
from app.feishu import refresh_initiator_names
from app.config import Settings


def test_different_senders_and_existing_tasks(tmp_path):
    s=Store(tmp_path)
    one,_=s.create_task('m1','相同任务','feishu',{'sender_id':'ou_first'})
    two,_=s.create_task('m2','相同任务','feishu',{'sender_id':'ou_second'})
    assert s.get(one)['initiator']!=s.get(two)['initiator']
    s.set_value('feishu_user_name:ou_first','张三')
    s.set_value('feishu_user_name:ou_second','李四')
    assert s.get(one)['initiator']=='张三'
    assert {r['initiator'] for r in s.listing()}=={'张三','李四'}


def test_names_can_be_backfilled_and_failures_do_not_block(tmp_path,monkeypatch):
    s=Store(tmp_path);s.create_task('m','任务','feishu',{'sender_id':'ou_one'})
    class API:
        def __init__(self,cfg):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def user_names(self,ids):return {'ou_one':'测试发起人'}
    monkeypatch.setattr('app.feishu.FeishuAPI',API)
    cfg=Settings(feishu_app_id='id',feishu_app_secret='secret')
    assert refresh_initiator_names(s,cfg)['updated']==1
    assert s.listing()[0]['initiator']=='测试发起人'
    def fail(*args):raise ValueError('permission')
    monkeypatch.setattr(API,'user_names',fail)
    assert refresh_initiator_names(s,cfg).get('warning')
    assert s.listing()[0]['initiator']=='测试发起人'
