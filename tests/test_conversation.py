from app.store import Store
from app.feishu import normalize_message
from app.conversation import accept_message
from tests.test_feishu import event


def message(mid, text='', parent=None, resources=None, mentioned=False, sender='u1', chat='c1'):
    return {'message_id':mid, 'text':text, 'parent_id':parent, 'root_id':None,
            'resources':resources or [], 'sender_id':sender, 'chat_id':chat,
            'is_mentioned':mentioned, 'chat_type':'group'}


def test_collect_long_notice_and_multiple_files_before_analysis(tmp_path):
    s=Store(tmp_path)
    first=accept_message(s,message('notice','很长的报名通知'*100,mentioned=True))
    tid=first['task_id']
    assert s.get(tid)['status']=='collecting'
    assert s.claim_next() is None
    s.link_message('bot-prompt',tid)
    for mid in ['file1','file2']:
        r=accept_message(s,message(mid,parent='bot-prompt',resources=[{'message_id':mid,'file_key':mid,'name':mid+'.csv'}]))
        assert r['action']=='attachments' and r['task_id']==tid
    t=s.get(tid)
    assert len(t['metadata']['resources'])==2 and len(s.listing())==1
    assert s.claim_next() is None
    r=accept_message(s,message('start','开始解析',parent='bot-prompt'))
    assert r['action']=='start' and r['task_id']==tid
    assert s.claim_next()['id']==tid


def test_dedup_author_and_chat_boundaries(tmp_path):
    s=Store(tmp_path)
    original=message('notice','正文',mentioned=True)
    tid=accept_message(s,original)['task_id']
    assert accept_message(s,original)['action']=='duplicate'
    assert accept_message(s,message('stranger','开始解析',parent='notice',sender='other')) is None
    assert accept_message(s,message('otherchat','开始解析',parent='notice',chat='c2')) is None
    assert accept_message(s,message('unrelated','普通群消息')) is None
    assert accept_message(s,message('supplement','补充：截止周五',parent='notice'))['action']=='supplement'
    assert s.get(tid)['metadata']['supplements'][0]=='补充：截止周五'
    restarted=Store(tmp_path)
    assert accept_message(restarted,message('start','开始解析',parent='notice'))['action']=='start'
    assert accept_message(restarted,message('late','补充',parent='notice'))['action']=='busy'
    assert len(restarted.listing())==1


def test_unmentioned_file_reply_can_be_normalized_without_accepting_unrelated_messages():
    p=event({'file_key':'f1','file_name':'报名.zip'},'file',parent_id='bot-prompt')
    assert normalize_message(p,'bot') is None
    n=normalize_message(p,'bot',allow_replies=True)
    assert n['resources'][0]['name']=='报名.zip' and n['is_mentioned'] is False
    assert normalize_message(event({'text':'闲聊'}),'bot',allow_replies=True) is None


def test_explicit_upload_session_accepts_standalone_files_only_for_selected_owner(tmp_path):
    s=Store(tmp_path)
    tid=accept_message(s,message('notice','长通知',mentioned=True))['task_id']
    s.link_message('prompt',tid)
    file=message('standalone',resources=[{'message_id':'standalone','file_key':'key','name':'材料.zip'}])
    assert accept_message(s,file) is None
    assert accept_message(s,message('begin','开始上传',parent='prompt'))['action']=='upload_started'
    assert accept_message(s,{**file,'message_id':'other','sender_id':'stranger'}) is None
    r=accept_message(s,file)
    assert r['action']=='attachments' and r['task_id']==tid
    assert s.get(tid)['metadata']['resources'][0]['name']=='材料.zip'
    assert accept_message(s,message('finish','开始解析',parent='prompt'))['action']=='start'
    assert accept_message(s,{**file,'message_id':'after'}) is None
    assert len(s.listing())==1


def test_standalone_file_event_is_available_for_session_routing():
    p=event({'file_key':'key','file_name':'材料.zip'},'file')
    assert normalize_message(p,'bot',allow_replies=True)['resources'][0]['file_key']=='key'


def test_upload_session_expires_and_does_not_guess_between_tasks(tmp_path,monkeypatch):
    s=Store(tmp_path)
    a=accept_message(s,message('a','通知A',mentioned=True))['task_id']
    b=accept_message(s,message('b','通知B',mentioned=True))['task_id']
    accept_message(s,message('open-a','开始上传',parent='a'))
    accept_message(s,message('open-b','开始上传',parent='b'))
    file=message('file',resources=[{'message_id':'file','file_key':'key','name':'a.zip'}])
    assert accept_message(s,file)['task_id']==b
    assert s.get(a)['metadata']['resources']==[]
    import time
    later=time.time()+1801
    monkeypatch.setattr('app.conversation.time.time',lambda:later)
    assert accept_message(s,{**file,'message_id':'late'}) is None


def test_start_upload_explicitly_reopens_completed_text_task(tmp_path):
    s=Store(tmp_path)
    tid=accept_message(s,message('notice','通知',mentioned=True))['task_id']
    s.update(tid,status='awaiting_confirmation')
    assert accept_message(s,message('again','开始上传',parent='notice'))['action']=='upload_started'
    assert s.get(tid)['status']=='collecting'
